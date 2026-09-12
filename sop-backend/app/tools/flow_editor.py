"""
flow_editor.py — 편집기(SOP_STUDIO.html) 안에 "통째로 접혀 들어 있는" 순서도 편집기를 꺼내고 다시 넣는 도구.

배경 설명
  SOP_STUDIO.html 은 문서 편집기이고, 그 안에 "순서도 편집기"라는 별도 HTML 페이지가 하나 더 들어 있습니다.
  순서도 편집기는 iframe(페이지 안의 작은 창)으로 뜨는데, 파일을 하나로 배포하려고 그 HTML 전체를
  Base64(글자만으로 이루어진 포장 방식)로 접어서 한 줄에 넣어 두었습니다.
      const EMBEDDED_FLOW_B64 = "PCFET0NUWVBF....";      ← 15만 글자짜리 한 줄
  이 줄은 사람이 읽거나 고칠 수 없으니, 고칠 때는 아래 순서로 합니다.
      1) extract : 그 한 줄을 풀어서 static/flow_editor.src.html 로 저장  (읽을 수 있는 원본 만들기)
      2) 원본(static/flow_editor.src.html)을 고침
      3) embed   : 원본을 다시 접어서 그 한 줄만 바꿔 넣음                 (다른 줄은 한 글자도 안 건드림)
      4) check   : 넣은 결과를 다시 풀었을 때 원본과 완전히 같은지 확인

실행 방법 (프로젝트 루트 sop-backend/ 에서)
    python -m app.tools.flow_editor extract
    python -m app.tools.flow_editor embed
    python -m app.tools.flow_editor check
"""

import argparse
import base64
import os
import re
import sys

# 이 파일 기준으로 프로젝트 루트(sop-backend/)와 두 파일의 위치를 계산합니다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUDIO_PATH = os.path.join(PROJECT_ROOT, "static", "SOP_STUDIO.html")
SRC_PATH = os.path.join(PROJECT_ROOT, "static", "flow_editor.src.html")

# 찾아야 하는 줄의 모양: 앞쪽 공백 + const EMBEDDED_FLOW_B64 = "…"; (따옴표 안은 Base64 글자만)
LINE_PATTERN = re.compile(r'^(?P<head>\s*const EMBEDDED_FLOW_B64 = ")(?P<b64>[A-Za-z0-9+/=]*)(?P<tail>";\s*)$')


def read_text(path: str) -> str:
    """파일을 UTF-8 글자로 읽어 돌려줍니다. (줄바꿈 문자를 바꾸지 않도록 newline='' 로 엽니다)"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    """글자를 UTF-8 로 파일에 씁니다. (줄바꿈 문자를 바꾸지 않도록 newline='' 로 엽니다)"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def split_studio(studio_text: str):
    """
    SOP_STUDIO.html 본문을 (앞부분, 줄머리, Base64, 줄꼬리, 뒷부분) 다섯 조각으로 나눕니다.
    embed 할 때 Base64 조각만 갈아 끼우면 나머지 네 조각은 그대로라서 "다른 줄은 바뀌지 않음" 이 보장됩니다.
    그 줄이 없거나 두 번 이상 나오면 오류를 냅니다.
    """
    lines = studio_text.split("\n")
    hits = [i for i, line in enumerate(lines) if LINE_PATTERN.match(line)]
    if len(hits) != 1:
        raise SystemExit(
            f"[flow_editor] SOP_STUDIO.html 에서 'const EMBEDDED_FLOW_B64 = \"…\";' 줄을 정확히 1개 찾아야 하는데 "
            f"{len(hits)}개가 있습니다. 파일이 손상되었는지 확인하세요."
        )
    idx = hits[0]
    m = LINE_PATTERN.match(lines[idx])
    before = "\n".join(lines[:idx]) + ("\n" if idx > 0 else "")
    after = ("\n" + "\n".join(lines[idx + 1:])) if idx + 1 < len(lines) else ""
    return before, m.group("head"), m.group("b64"), m.group("tail"), after


def decode_embedded(b64: str) -> str:
    """Base64 글자 뭉치를 원래의 HTML 글자(UTF-8)로 풉니다."""
    return base64.b64decode(b64).decode("utf-8")


def encode_source(src_text: str) -> str:
    """HTML 글자를 Base64 한 줄로 접습니다. (줄바꿈 없이 한 줄)"""
    return base64.b64encode(src_text.encode("utf-8")).decode("ascii")


def first_different_line(a_lines, b_lines) -> int:
    """두 줄 목록을 앞에서부터 비교해 처음으로 다른 줄의 번호(0부터)를 돌려줍니다. 끝까지 같으면 짧은 쪽의 길이를 돌려줍니다."""
    shorter = min(len(a_lines), len(b_lines))
    for i in range(shorter):
        if a_lines[i] != b_lines[i]:
            return i
    return shorter


def cmd_extract() -> int:
    """SOP_STUDIO.html 의 접힌 편집기를 풀어 static/flow_editor.src.html 로 저장합니다."""
    studio = read_text(STUDIO_PATH)
    _, _, b64, _, _ = split_studio(studio)
    html = decode_embedded(b64)
    write_text(SRC_PATH, html)
    print(f"[flow_editor] 꺼내기 완료: {SRC_PATH} ({len(html.splitlines())}줄, {len(html.encode('utf-8'))}바이트)")
    return 0


def cmd_embed() -> int:
    """static/flow_editor.src.html 을 접어서 SOP_STUDIO.html 의 그 한 줄만 바꿔 넣습니다."""
    if not os.path.exists(SRC_PATH):
        print(f"[flow_editor] 원본 파일이 없습니다: {SRC_PATH}  (먼저 extract 를 실행하세요)")
        return 1
    studio = read_text(STUDIO_PATH)
    before, head, old_b64, tail, after = split_studio(studio)
    src = read_text(SRC_PATH)
    new_b64 = encode_source(src)
    if new_b64 == old_b64:
        print("[flow_editor] 원본이 이미 들어 있는 내용과 같아서 바꿀 것이 없습니다.")
        return 0
    write_text(STUDIO_PATH, before + head + new_b64 + tail + after)
    print(f"[flow_editor] 넣기 완료: SOP_STUDIO.html 의 EMBEDDED_FLOW_B64 한 줄을 교체했습니다 ({len(new_b64)}글자)")
    return 0


def cmd_check() -> int:
    """
    두 가지를 확인합니다.
      1) SOP_STUDIO.html 에 들어 있는 것을 풀었을 때 static/flow_editor.src.html 과 완전히 같은가
      2) 원본을 다시 접어 넣어도 SOP_STUDIO.html 의 나머지 줄이 그대로인가 (그 줄 외에는 아무 변화가 없는가)
    """
    if not os.path.exists(SRC_PATH):
        print(f"[flow_editor] 원본 파일이 없습니다: {SRC_PATH}  (먼저 extract 를 실행하세요)")
        return 1
    studio = read_text(STUDIO_PATH)
    before, head, b64, tail, after = split_studio(studio)
    src = read_text(SRC_PATH)

    ok = True
    embedded_html = decode_embedded(b64)
    if embedded_html == src:
        print("[flow_editor] 확인 1/2 통과: 들어 있는 편집기를 풀면 flow_editor.src.html 과 완전히 같습니다.")
    else:
        ok = False
        e_lines, s_lines = embedded_html.split("\n"), src.split("\n")
        first_diff = first_different_line(e_lines, s_lines)
        print(
            f"[flow_editor] 확인 1/2 실패: 들어 있는 내용과 flow_editor.src.html 이 다릅니다 "
            f"(처음 다른 줄: {first_diff + 1}번째, 들어 있는 것 {len(e_lines)}줄 / 원본 {len(s_lines)}줄). embed 를 다시 실행하세요."
        )

    rebuilt = before + head + encode_source(src) + tail + after
    rebuilt_before, _, _, _, rebuilt_after = split_studio(rebuilt)
    if rebuilt_before == before and rebuilt_after == after:
        print("[flow_editor] 확인 2/2 통과: 다시 넣어도 EMBEDDED_FLOW_B64 줄 외의 나머지는 바뀌지 않습니다.")
    else:
        ok = False
        print("[flow_editor] 확인 2/2 실패: 다시 넣으면 다른 줄이 바뀝니다. 도구 자체의 문제이니 개발자에게 알려 주세요.")

    print("[flow_editor] 결과: " + ("모두 통과" if ok else "실패"))
    return 0 if ok else 1


def main(argv=None) -> int:
    """명령줄에서 extract / embed / check 중 하나를 골라 실행합니다."""
    parser = argparse.ArgumentParser(description="SOP_STUDIO.html 안의 순서도 편집기를 꺼내고(extract) 넣고(embed) 확인(check)합니다.")
    parser.add_argument("command", choices=["extract", "embed", "check"], help="할 일")
    args = parser.parse_args(argv)
    return {"extract": cmd_extract, "embed": cmd_embed, "check": cmd_check}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
