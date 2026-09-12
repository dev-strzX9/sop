"""
test_tools.py — 명령줄 도구 두 개를 DB·서버 없이 시험합니다.

  - app/tools/import_library.py : 파일 읽기(세 가지 모양, 없는 파일, 깨진 JSON), 보낼 수 있는 문서 판단,
                                  저장 흐름 "POST → 409 sop_no_taken 이면 existing_id 로 PUT" (가짜 서버로 확인)
  - app/tools/flow_editor.py    : extract / embed / check 왕복 — 접었다 풀면 원문과 같고, check 는 불일치 때 1 을 돌려준다
"""

import json

import httpx
import pytest

from app.tools import flow_editor
from app.tools import import_library as tool


def sample_doc(sop_no="SOP-ETCH-001"):
    """import 도구가 "보낼 수 있다" 고 판단하는 최소한의 문서."""
    return {"format": "sop-editor-mock", "version": 1, "sop": {"id": sop_no, "name": "이름"}, "blocks": []}


# ---------------------------------------------------------------------
# import_library: 파일 읽기
# ---------------------------------------------------------------------
# 세 가지 파일 모양(전체 내보내기 / 배열 / 문서 하나)이 모두 문서 목록으로 읽힌다
def test_load_documents_accepts_three_shapes(tmp_path):
    library = {"format": "sop-studio-library", "items": [{"sop_no": "A", "doc": sample_doc("A")}, {"sop_no": "B", "doc": sample_doc("B")}, "이상한 항목"]}
    (tmp_path / "library.json").write_text(json.dumps(library), encoding="utf-8")
    (tmp_path / "list.json").write_text(json.dumps([sample_doc("C")]), encoding="utf-8")
    (tmp_path / "one.json").write_text(json.dumps(sample_doc("D")), encoding="utf-8")

    assert [d["sop"]["id"] for d in tool.load_documents(str(tmp_path / "library.json"))] == ["A", "B"]
    assert [d["sop"]["id"] for d in tool.load_documents(str(tmp_path / "list.json"))] == ["C"]
    assert [d["sop"]["id"] for d in tool.load_documents(str(tmp_path / "one.json"))] == ["D"]


# 없는 파일 / 깨진 JSON 은 파이썬 오류 화면 대신 한국어 안내 한 줄로 종료한다 (SystemExit)
def test_load_documents_reports_missing_or_broken_file(tmp_path):
    with pytest.raises(SystemExit) as caught:
        tool.load_documents(str(tmp_path / "없는파일.json"))
    assert "파일이 없습니다" in str(caught.value)

    (tmp_path / "broken.json").write_text("{ 이건 json 이 아님", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        tool.load_documents(str(tmp_path / "broken.json"))
    assert "JSON 형식이 깨졌습니다" in str(caught.value)


# 보낼 수 있는 문서 판단: 이유 문장이 함께 나온다
def test_why_invalid_document():
    assert tool.why_invalid_document(sample_doc()) == ""
    assert tool.is_valid_document(sample_doc())
    assert "객체가 아님" in tool.why_invalid_document("문자열")
    assert "편집기 문서가 아님" in tool.why_invalid_document({"format": "other", "sop": {"id": "A"}})
    assert "sop 항목이 없음" in tool.why_invalid_document({"format": "sop-editor-mock"})
    assert "비어 있음" in tool.why_invalid_document({"format": "sop-editor-mock", "sop": {"id": "  "}})


# ---------------------------------------------------------------------
# import_library: 저장 흐름 (가짜 서버)
# ---------------------------------------------------------------------
def fake_server(seen):
    """
    httpx.MockTransport 용 가짜 서버. 요청을 seen 에 기록하고,
    SOP-EXISTS 번호의 POST 에는 409 sop_no_taken + existing_id, 그 id 로의 PUT 에는 201, 다른 POST 에는 201 을 돌려준다.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("X-User")))
        body = json.loads(request.content)
        if request.method == "POST" and request.url.path == "/api/sops":
            if body["doc"]["sop"]["id"] == "SOP-EXISTS":
                return httpx.Response(409, json={"error": {"code": "sop_no_taken", "message": "있음", "existing_id": "doc-1"}})
            if body["doc"]["sop"]["id"] == "SOP-BAD":
                return httpx.Response(422, json={"error": {"code": "invalid_document", "message": "형식 오류"}})
            return httpx.Response(201, json={"id": "new", "version_no": 1})
        if request.method == "PUT" and request.url.path == "/api/sops/doc-1":
            assert body["base_version_no"] is None      # 이관은 충돌 검사 없이 무조건 새 버전
            return httpx.Response(201, json={"id": "doc-1", "version_no": 2})
        return httpx.Response(404, json={"error": {"code": "http_error", "message": "없는 주소"}})

    return handler


# 새 번호는 POST 한 번으로 끝, 이미 있는 번호는 POST(409) 뒤 existing_id 로 PUT, 서버 오류는 메시지로 돌아온다
def test_send_document_post_then_put_on_409():
    seen = []
    with httpx.Client(transport=httpx.MockTransport(fake_server(seen))) as client:
        assert tool.send_document(client, "http://fake/", sample_doc("SOP-NEW"), "hong") == ""
        assert seen == [("POST", "/api/sops", "hong")]

        seen.clear()
        assert tool.send_document(client, "http://fake", sample_doc("SOP-EXISTS"), "hong") == ""
        assert seen == [("POST", "/api/sops", "hong"), ("PUT", "/api/sops/doc-1", "hong")]

        message = tool.send_document(client, "http://fake", sample_doc("SOP-BAD"), "hong")
        assert message == "HTTP 422 invalid_document - 형식 오류"


# 서버가 꺼져 있으면(연결 오류) 한국어 안내 문구를 돌려준다
def test_send_document_reports_connection_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert tool.send_document(client, "http://fake", sample_doc(), "hong").startswith("서버에 연결할 수 없습니다")


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
