"""
import_library.py — 예전 브라우저(IndexedDB)에 쌓여 있던 SOP 문서를 서버로 한꺼번에 옮기는 도구.

편집기 사이드바의 "전체 내보내기" 버튼을 누르면 아래 모양의 JSON 파일이 내려받아집니다.
    { "format": "sop-studio-library", "items": [ { "sop_no": "SOP-ETCH-001", "doc": {...} }, ... ] }
이 파일 안의 문서(doc)를 하나씩 꺼내어 서버의 "저장" API 에 보내 줍니다.
(처음 보는 번호면 새 문서(POST), 이미 있는 번호면 그 문서의 새 버전(PUT) 으로 저장됩니다)
(문서 JSON 하나만 들어 있는 파일이나, 문서 JSON 여러 개가 배열([...])로 든 파일도 읽을 수 있습니다)

실행 방법 (프로젝트 루트 sop-backend/ 에서):
    python -m app.tools.import_library sop-library-2026-09-09.json
    python -m app.tools.import_library sop-library-2026-09-09.json --api http://localhost:8000 --user hong

끝나면 "성공 N건 / 건너뜀 M건 / 실패 K건" 을 알려 줍니다.
  - 건너뜀: 편집기 문서가 아니거나(format 이 다름) SOP 번호(sop.id)가 비어 있는 것. 어느 문서가 왜 건너뛰었는지 함께 보여 줍니다.
  - 실패  : 서버가 오류(409 충돌, 422 형식 오류 등)를 돌려준 것. 번호와 오류 메시지를 함께 보여 줍니다.
"""

import argparse
import json
import sys

import httpx


def load_documents(file_path: str) -> list:
    """
    JSON 파일을 읽어서 "문서(doc) 목록" 으로 만들어 돌려줍니다.
    파일 모양이 세 가지(라이브러리 내보내기 / 문서 배열 / 문서 하나) 중 어느 것이든 같은 목록으로 맞춥니다.
    파일이 없거나 JSON 이 깨져 있으면 한국어 안내 한 줄만 보여 주고 종료합니다 (파이썬 오류 화면을 쏟아 내지 않게).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"파일이 없습니다: {file_path}  (경로와 파일 이름을 확인하세요)")
    except json.JSONDecodeError as error:
        raise SystemExit(f"JSON 형식이 깨졌습니다: {file_path}  ({error.lineno}번째 줄 {error.colno}번째 글자 근처: {error.msg})")

    documents = []

    # 모양 1: "전체 내보내기" 파일 → items[] 안의 doc 를 꺼냅니다
    if isinstance(data, dict) and data.get("format") == "sop-studio-library":
        for item in data.get("items", []):
            if isinstance(item, dict):
                documents.append(item.get("doc"))

    # 모양 2: 문서 JSON 여러 개가 배열로 든 파일
    elif isinstance(data, list):
        documents = data

    # 모양 3: 문서 JSON 하나
    else:
        documents = [data]

    return documents


def why_invalid_document(doc) -> str:
    """
    서버에 보낼 수 있는 편집기 문서인지 간단히 확인합니다.
    보낼 수 있으면 빈 문자열 "", 아니면 "왜 안 되는지" 이유 문장을 돌려줍니다.
    format 이 "sop-editor-mock" 이고, sop.id(SOP 번호)가 비어 있지 않아야 합니다.
    (자세한 검사는 서버가 하므로 여기서는 "보낼 가치가 있는지" 만 봅니다)
    """
    if not isinstance(doc, dict):
        return "문서가 JSON 객체가 아님"
    if doc.get("format") != "sop-editor-mock":
        return f"편집기 문서가 아님 (format={doc.get('format')!r})"

    sop_info = doc.get("sop")
    if not isinstance(sop_info, dict):
        return "sop 항목이 없음"

    sop_no = str(sop_info.get("id") or "").strip()
    if sop_no == "":
        return "SOP 번호(sop.id)가 비어 있음"
    return ""


def is_valid_document(doc) -> bool:
    """why_invalid_document 의 예/아니오 버전. (보낼 수 있으면 True)"""
    return why_invalid_document(doc) == ""


def _error_text(response: httpx.Response) -> str:
    """서버 오류 응답({"error": {"code", "message"}})을 사람이 읽을 한 줄로 바꿉니다."""
    try:
        error_info = response.json().get("error", {})
        message = f"{error_info.get('code', '')} - {error_info.get('message', '')}"
    except Exception:
        message = response.text[:200]
    return f"HTTP {response.status_code} {message}"


def send_document(client: httpx.Client, api_base: str, doc: dict, user: str) -> str:
    """
    문서 하나를 서버에 저장합니다. 편집기와 같은 순서로:
      1) POST /api/sops 로 "새 문서" 로 만들어 보고,
      2) 서버가 "이미 있는 번호" (409 sop_no_taken) 라고 하면 그 문서의 id 로 PUT /api/sops/{id} 를 불러 새 버전을 쌓습니다.
    성공하면 빈 문자열 "", 실패하면 오류 메시지를 돌려줍니다.
    """
    base = api_base.rstrip("/")
    body = {
        "doc": doc,
        "base_version_no": None,        # 이관은 "무조건 새 버전으로 저장" 이므로 충돌 검사를 하지 않습니다
        "change_note": "IndexedDB 이관",
        "saved_by": user,
    }
    headers = {"X-User": user}

    try:
        response = client.post(f"{base}/api/sops", json=body, headers=headers)
        if response.status_code == 409 and response.json().get("error", {}).get("code") == "sop_no_taken":
            existing_id = response.json()["error"]["existing_id"]
            response = client.put(f"{base}/api/sops/{existing_id}", json=body, headers=headers)
    except httpx.HTTPError as error:
        # 서버가 꺼져 있거나 주소가 틀린 경우
        return f"서버에 연결할 수 없습니다: {error}"

    if response.status_code in (200, 201):
        return ""
    return _error_text(response)


def main() -> int:
    """명령줄 인자를 읽고, 문서를 하나씩 보내고, 결과를 집계해서 출력합니다."""
    parser = argparse.ArgumentParser(
        description="편집기 '전체 내보내기' JSON 파일의 문서를 서버로 일괄 저장합니다."
    )
    parser.add_argument("file", help="내보내기 JSON 파일 경로 (예: sop-library-2026-09-09.json)")
    parser.add_argument("--api", default="http://localhost:8000", help="서버 주소 (기본값: http://localhost:8000)")
    parser.add_argument("--user", default="import", help="저장한 사람 이름으로 기록할 값 (기본값: import)")
    args = parser.parse_args()

    documents = load_documents(args.file)
    print(f"파일에서 문서 {len(documents)}건을 읽었습니다. 서버: {args.api}")

    success_count = 0
    skipped_count = 0
    failed_list = []   # (sop_no, 오류 메시지) 를 모아 둡니다

    # 문서 크기가 클 수 있으니(이미지 base64) 제한 시간을 넉넉히 둡니다
    with httpx.Client(timeout=60.0) as client:
        for index, doc in enumerate(documents, start=1):
            reason = why_invalid_document(doc)
            if reason:
                skipped_count += 1
                # 번호가 있으면 번호로, 없으면 "N번째" 로 어느 문서인지 알려 준다
                label = ""
                if isinstance(doc, dict) and isinstance(doc.get("sop"), dict):
                    label = str(doc["sop"].get("id") or "").strip()
                print(f"  건너뜀: {label or f'{index}번째 문서'} ({reason})")
                continue

            sop_no = str(doc["sop"]["id"]).strip()
            error_message = send_document(client, args.api, doc, args.user)

            if error_message == "":
                success_count += 1
                print(f"  저장됨: {sop_no}")
            else:
                failed_list.append((sop_no, error_message))
                print(f"  실패  : {sop_no} -> {error_message}")

    print("")
    print(f"성공 {success_count}건 / 건너뜀 {skipped_count}건 / 실패 {len(failed_list)}건")

    if failed_list:
        print("")
        print("실패 목록:")
        for sop_no, error_message in failed_list:
            print(f"  {sop_no}: {error_message}")
        return 1   # 하나라도 실패하면 종료 코드 1 (스크립트에서 감지할 수 있게)

    return 0


if __name__ == "__main__":
    sys.exit(main())
