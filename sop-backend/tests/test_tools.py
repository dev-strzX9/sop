"""
tests/test_tools.py — 개발용 도구를 시험합니다.

  - app/tools/flow_editor.py : extract / embed / check 왕복 — 접었다 풀면 원문과 같고, check 는 불일치 때 1 을 돌려준다
"""

from app.tools import flow_editor

# ---------------------------------------------------------------------
# flow_editor: extract / embed / check 왕복
# ---------------------------------------------------------------------
def make_fake_studio(tmp_path, monkeypatch, embedded_html):
    """작은 SOP_STUDIO.html 흉내 파일과 원본 파일 경로를 만들고, 도구가 그 파일들을 보도록 경로를 바꿔 끼운다."""
    studio = tmp_path / "STUDIO.html"
    src = tmp_path / "flow_editor.src.html"
    studio.write_text(
        "<html>\n<script>\n  const EMBEDDED_FLOW_B64 = \"" + flow_editor.encode_source(embedded_html) + "\";\n</script>\n</html>\n",
        encoding="utf-8", newline="",
    )
    monkeypatch.setattr(flow_editor, "STUDIO_PATH", str(studio))
    monkeypatch.setattr(flow_editor, "SRC_PATH", str(src))
    return studio, src


def test_flow_editor_round_trip_and_check(tmp_path, monkeypatch):
    original = "<!DOCTYPE html>\n<p>순서도 편집기 v1</p>\n"
    studio, src = make_fake_studio(tmp_path, monkeypatch, original)

    # extract: 접힌 것을 풀면 원문과 완전히 같다
    assert flow_editor.cmd_extract() == 0
    assert src.read_text(encoding="utf-8") == original
    assert flow_editor.cmd_check() == 0

    # 원본을 고치고 embed 하면 그 한 줄만 바뀌고, check 는 다시 통과한다
    edited = original.replace("v1", "v2 한글도 됨")
    src.write_text(edited, encoding="utf-8", newline="")
    assert flow_editor.cmd_check() == 1          # 아직 넣기 전이라 불일치 → 1
    assert flow_editor.cmd_embed() == 0
    assert flow_editor.cmd_check() == 0
    before, head, b64, tail, after = flow_editor.split_studio(studio.read_text(encoding="utf-8"))
    assert flow_editor.decode_embedded(b64) == edited
    assert before == "<html>\n<script>\n" and after == "\n</script>\n</html>\n"   # 다른 줄은 그대로

    # 원본 파일이 없으면 embed / check 는 1
    src.unlink()
    assert flow_editor.cmd_embed() == 1
    assert flow_editor.cmd_check() == 1
