"""
test_save_flow.py — "저장 → 목록 → 열기" 의 기본 흐름을 시험합니다.

시나리오 (스펙 8. 완료 기준)
  - 서버와 DB 가 살아 있는지 (/health 는 DB 안 봄, /api/health 는 DB 확인)
  - 문서를 저장하면 버전 1 이 생기고, 목록과 열기에서 그대로 돌아오는지
  - 두 번 저장하면 버전이 1 → 2 로 올라가고 순서도 노드 행이 버전마다 따로 쌓이는지
  - 저장 주소: 새 문서는 POST /api/sops, 기존 문서는 PUT /api/sops/{id}. 번호 중복 409, 없는 id 404, 번호 불일치 400
  - 잘못된 문서는 막히는지 (422 / 400 / 413), 이상한 노드는 건너뛰고 경고만 남기는지
  - 폐기(DELETE) 와 되살리기(다시 저장), 목록 검색(q, area)
  - 폐기된 문서에 저장하면 draft 로 되살아나는지 (교차 결정 D)
  - 사용자 이름 헤더의 %인코딩 한글이 되돌려지는지 (교차 결정 A), GET / 에 api-base meta 가 들어가는지 (교차 결정 B)
  - 같은 문서 동시 저장, examples/sample_doc.json, anonymous 사용자, UUID 아닌 주소, 이상한 숫자 값(inf/nan)
"""

import asyncio
import copy
import json
import os
import uuid
from urllib.parse import quote

import psycopg
import pytest

from tests.conftest import PROJECT_ROOT, make_doc, save


# 서버가 켜져 있고 DB 에 연결되어 있으면 {"ok": true, "db": "up"} 을 돌려준다
async def test_health(client):
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "db": "up"}


# /health 는 DB 를 보지 않고 항상 {"ok": true} (컨테이너 플랫폼의 생존 확인용). DB 풀이 닫혀 있어도 200.
async def test_liveness_health_does_not_touch_db(client):
    from app import db

    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # DB 풀을 잠깐 닫아도 /health 는 그대로 200, /api/health 는 503 + db: down (상태 코드만 보는 모니터링도 알아채게)
    await db.close_pool()
    try:
        assert (await client.get("/health")).json() == {"ok": True}
        res = await client.get("/api/health")
        assert res.status_code == 503
        assert res.json() == {"ok": False, "db": "down"}
    finally:
        # client fixture 가 마지막에 close_pool 을 또 부르므로 다시 열어 둔다
        await db.open_pool(os.environ["DATABASE_URL"], 1, 5)


# 문서를 처음 저장(POST /api/sops)하면 201 과 함께 version_no 1, 문서 id(UUID) 를 돌려준다
async def test_first_save_returns_version_1(client):
    doc = make_doc()
    res = await client.post("/api/sops", json={"doc": doc})
    assert res.status_code == 201, res.text

    body = res.json()
    assert body["version_no"] == 1
    assert body["sop_no"] == "SOP-ETCH-001"
    assert body["warnings"] == []
    # id 가 UUID 형식의 문자열인지 확인 (형식이 틀리면 uuid.UUID() 가 오류를 낸다)
    uuid.UUID(body["id"])
    uuid.UUID(body["version_id"])
    # 시각은 UTC 로, 끝에 Z
    assert body["saved_at"].endswith("Z")
    # sop 상자 1개는 번호만 있고 그 문서가 없으므로 "미작성(pending)"
    assert body["refs"] == {"linked": 0, "pending": 1, "empty": 0}


# POST 는 base_version_no 를 무시한다 (새 문서라 비교할 이전 버전이 없다)
async def test_post_ignores_base_version_no(client):
    res = await client.post("/api/sops", json={"doc": make_doc(), "base_version_no": 7})
    assert res.status_code == 201, res.text
    assert res.json()["version_no"] == 1


# 같은 번호로 POST 를 또 하면 409 sop_no_taken 과 함께 기존 문서의 id 와 번호를 알려 준다 (프론트가 PUT 으로 갈아탈 수 있게)
async def test_post_duplicate_sop_no_is_409(client):
    first = (await client.post("/api/sops", json={"doc": make_doc()})).json()

    res = await client.post("/api/sops", json={"doc": make_doc()})
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "sop_no_taken"
    assert error["existing_id"] == first["id"]
    assert error["existing_sop_no"] == "SOP-ETCH-001"

    # 문서는 여전히 1건, 버전도 1개 (409 로 거절된 저장은 아무것도 남기지 않는다)
    assert len((await client.get("/api/sops")).json()) == 1
    assert len((await client.get(f"/api/sops/{first['id']}/versions")).json()) == 1


# 없는 id 로 PUT 하면 404 not_found
async def test_put_unknown_id_is_404(client):
    res = await client.put("/api/sops/00000000-0000-0000-0000-000000000000", json={"doc": make_doc()})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


# PUT 으로 새 버전을 쌓으면 응답이 201 이고 version_no 가 2 가 된다
async def test_put_appends_version(client):
    first = (await client.post("/api/sops", json={"doc": make_doc()})).json()
    res = await client.put(f"/api/sops/{first['id']}", json={"doc": make_doc(), "base_version_no": 1})
    assert res.status_code == 201, res.text
    assert res.json()["version_no"] == 2
    assert res.json()["id"] == first["id"]


# 저장한 문서는 목록에 보이고, 목록 항목에는 content(문서 본문)가 들어 있지 않다
async def test_list_shows_document_without_content(client):
    doc = make_doc()
    await save(client, doc)

    res = await client.get("/api/sops")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    item = items[0]
    assert item["sop_no"] == "SOP-ETCH-001"
    assert item["name"] == "Chamber PM 절차"
    assert item["area"] == "P"
    assert item["version_no"] == 1
    assert item["revision"] == "1.0"
    assert "content" not in item


# 저장한 뒤 id 로 열면 보낸 문서 JSON 이 토씨 하나 안 바뀌고 그대로 돌아온다
async def test_open_by_id_returns_same_content(client):
    doc = make_doc()
    saved = (await save(client, doc)).json()

    res = await client.get(f"/api/sops/{saved['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == saved["id"]
    assert body["sop_no"] == "SOP-ETCH-001"
    assert body["version_no"] == 1
    assert body["version_id"] == saved["version_id"]
    assert body["lock"] is None
    assert body["content"] == doc


# SOP 번호(sop_no)로 열어도 id 로 연 것과 똑같은 결과가 나온다 (라이브러리 트리는 번호로 문서를 찾는다)
async def test_open_by_no_matches_open_by_id(client):
    doc = make_doc()
    saved = (await save(client, doc)).json()

    by_id = (await client.get(f"/api/sops/{saved['id']}")).json()
    res = await client.get("/api/sops/by-no/SOP-ETCH-001")
    assert res.status_code == 200
    assert res.json() == by_id


# 같은 문서를 두 번 저장하면 버전이 1 → 2 로 올라가고, 버전 목록에는 최신이 먼저 나온다
async def test_second_save_increments_version(client, database_url):
    doc = make_doc()
    first = (await save(client, doc, change_note="첫 저장")).json()
    assert first["version_no"] == 1

    second = (await save(client, doc, change_note="두 번째 저장")).json()
    assert second["version_no"] == 2
    assert second["id"] == first["id"]          # 문서 id 는 그대로
    assert second["version_id"] != first["version_id"]  # 버전 id 는 새로

    # 버전 목록: 2건, 최신(2)이 먼저
    res = await client.get(f"/api/sops/{first['id']}/versions")
    assert res.status_code == 200
    versions = res.json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert versions[0]["change_note"] == "두 번째 저장"
    assert versions[1]["change_note"] == "첫 저장"
    for v in versions:
        assert "content" not in v

    # DB 를 직접 들여다본다: 순서도 노드 행이 버전마다 따로 쌓여 5개 × 2버전 = 10개
    with psycopg.connect(database_url) as conn:
        # 노드 행 전체 개수 (모든 버전 합계)
        total = conn.execute("SELECT count(*) FROM flow_nodes").fetchone()[0]
        assert total == 10

        # "현재 버전" 의 노드만 세면 5개 (반드시 current_version_id 로 조인해야 버전이 섞이지 않는다)
        current = conn.execute(
            "SELECT count(*) FROM flow_nodes n "
            "JOIN sop_documents d ON d.current_version_id = n.version_id "
            "WHERE d.id = %s",
            (first["id"],),
        ).fetchone()[0]
        assert current == 5

        # 연결선도 버전마다 4개씩
        edges_total = conn.execute("SELECT count(*) FROM flow_edges").fetchone()[0]
        assert edges_total == 8


# 순서도에 알 수 없는 종류(node_type "foo")의 노드가 있어도 저장은 되고, 그 노드만 건너뛰고 경고를 남긴다
async def test_bad_node_is_skipped_with_warning(client, database_url):
    doc = make_doc()
    flowchart = doc["blocks"][1]
    flowchart["project"]["nodes"].append(
        {"node": "foo_1", "node_type": "foo", "x": 0, "y": 0, "name": "이상한 노드"}
    )

    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["warnings"]) == 1
    assert "foo" in body["warnings"][0] and "foo_1" in body["warnings"][0]   # 어떤 노드가 왜 건너뛰어졌는지 적혀 있다

    # 원본 content 는 이상한 노드까지 포함해 그대로 보존된다
    opened = (await client.get(f"/api/sops/{body['id']}")).json()
    assert opened["content"] == doc

    # DB 의 노드 행은 정상 노드 5개만
    with psycopg.connect(database_url) as conn:
        # 이 버전에 속한 노드 행 개수
        count = conn.execute(
            "SELECT count(*) FROM flow_nodes WHERE version_id = %s", (body["version_id"],)
        ).fetchone()[0]
        assert count == 5


# format 값이 다르면 422 invalid_document
async def test_wrong_format_is_rejected(client):
    doc = make_doc()
    doc["format"] = "something-else"
    res = await save(client, doc)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_document"


# blocks 가 배열(list)이 아니면 422 invalid_document
async def test_blocks_not_list_is_rejected(client):
    doc = make_doc()
    doc["blocks"] = {"type": "title"}
    res = await save(client, doc)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_document"


# sop.id 가 공백이면 422 invalid_document (POST 도 PUT 도)
async def test_blank_sop_id_is_rejected(client):
    doc_id = (await save(client, make_doc())).json()["id"]

    doc = make_doc()
    doc["sop"]["id"] = "   "
    res = await client.post("/api/sops", json={"doc": doc})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_document"

    res = await client.put(f"/api/sops/{doc_id}", json={"doc": doc})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_document"


# sop.id 에 한글이 들어가면 422 invalid_document (허용 문자: 영문·숫자·-·_·.)
async def test_korean_sop_id_is_rejected(client):
    doc = make_doc(sop_no="SOP-식각-001")
    res = await save(client, doc)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_document"


# PUT 할 때 저장된 번호와 문서 안의 sop.id 가 다르면 400 sop_no_mismatch (번호를 바꾸려면 PATCH /number 를 먼저)
async def test_sop_no_mismatch(client):
    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]

    res = await client.put(f"/api/sops/{doc_id}", json={"doc": make_doc(sop_no="SOP-ETCH-999")})
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "sop_no_mismatch"
    assert error["stored_sop_no"] == "SOP-ETCH-001"
    assert error["doc_sop_no"] == "SOP-ETCH-999"
    assert "PATCH" in error["hint"]

    # 거절된 저장은 버전을 만들지 않는다
    assert len((await client.get(f"/api/sops/{doc_id}/versions")).json()) == 1


# area 가 허용 값(P/E/D/T/C)이 아니면 저장은 되지만 ''(미지정)으로 바뀌고 경고 1건이 붙는다
async def test_unknown_area_becomes_blank_with_warning(client):
    doc = make_doc(area="X")
    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["warnings"]) == 1

    items = (await client.get("/api/sops")).json()
    assert items[0]["area"] == ""


# 폐기(DELETE)하면 목록에서 사라지고, status=all 로는 보이며, 다시 저장하면 draft 로 되살아난다
async def test_retire_and_revive_by_saving(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]

    res = await client.delete(f"/api/sops/{doc_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "retired"
    assert res.json()["referenced_by"] == 0   # 아무도 이 문서를 참조하지 않는다

    # 기본 목록에서는 사라짐
    items = (await client.get("/api/sops")).json()
    assert items == []

    # 전체 보기에서는 보임
    items_all = (await client.get("/api/sops", params={"status": "all"})).json()
    assert len(items_all) == 1
    assert items_all[0]["status"] == "retired"

    # 되살리기: 폐기된 문서에 다시 저장하면 draft 로 돌아온다 (별도 복구 API 는 없다)
    res = await save(client, doc, base_version_no=1)
    assert res.status_code == 201
    assert any("폐기" in w for w in res.json()["warnings"])

    items = (await client.get("/api/sops")).json()
    assert len(items) == 1
    assert items[0]["id"] == doc_id
    assert items[0]["status"] == "draft"


# 목록 검색: q 는 SOP 번호/이름의 일부로 찾고, area 는 적용 AREA 로 거른다
async def test_list_search_and_area_filter(client):
    await save(client, make_doc(sop_no="SOP-ETCH-001", name="Chamber PM 절차", area="P"))
    await save(client, make_doc(sop_no="SOP-CMP-001", name="Slurry 교체", area="E"))

    # q=ETCH 로는 1건
    items = (await client.get("/api/sops", params={"q": "ETCH"})).json()
    assert [i["sop_no"] for i in items] == ["SOP-ETCH-001"]

    # q=zzz 로는 0건
    items = (await client.get("/api/sops", params={"q": "zzz"})).json()
    assert items == []

    # area=P 로는 ETCH 문서만
    items = (await client.get("/api/sops", params={"area": "P"})).json()
    assert [i["sop_no"] for i in items] == ["SOP-ETCH-001"]

    # 아무 조건 없으면 2건, area 순으로 정렬 (E 가 P 보다 먼저)
    items = (await client.get("/api/sops")).json()
    assert [i["sop_no"] for i in items] == ["SOP-CMP-001", "SOP-ETCH-001"]


# 요청 크기가 20MB 를 넘으면(Content-Length 헤더 기준) 413 payload_too_large
async def test_payload_too_large(client):
    doc = make_doc()
    # 실제로 21MB 를 보내는 대신 헤더로 "21MB 입니다" 라고 알려 준다. 미들웨어가 헤더만 보고 먼저 막는다.
    too_big = str(21 * 1024 * 1024)
    res = await client.post(
        "/api/sops",
        json={"doc": doc, "base_version_no": None},
        headers={"Content-Length": too_big},
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


# 저장한 문서를 조금 바꿔 다시 저장해도(깊은 복사) 열었을 때 마지막에 보낸 내용이 나온다
async def test_latest_open_reflects_last_save(client):
    doc_v1 = make_doc()
    doc_id = (await save(client, doc_v1)).json()["id"]

    doc_v2 = copy.deepcopy(doc_v1)
    doc_v2["sop"]["name"] = "Chamber PM 절차 (개정)"
    doc_v2["studio"]["revision"] = "1.1"
    await save(client, doc_v2)

    opened = (await client.get(f"/api/sops/{doc_id}")).json()
    assert opened["version_no"] == 2
    assert opened["content"] == doc_v2

    items = (await client.get("/api/sops")).json()
    assert items[0]["name"] == "Chamber PM 절차 (개정)"
    assert items[0]["revision"] == "1.1"


# ---------------------------------------------------------------------
# 교차 결정 D: 폐기(retired)된 문서에 PUT 으로 저장하면 draft 로 되살아나고 warnings 에 안내 문구가 붙는다
# ---------------------------------------------------------------------
async def test_put_on_retired_document_revives_it_as_draft(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]
    assert (await client.delete(f"/api/sops/{doc_id}")).status_code == 200

    res = await client.put(f"/api/sops/{doc_id}", json={"doc": doc, "base_version_no": 1})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["version_no"] == 2
    assert "폐기됐던 문서를 다시 살렸습니다 (draft)" in body["warnings"]

    # 기본 목록(폐기 제외)에 다시 보이고 상태는 draft
    items = (await client.get("/api/sops")).json()
    assert [i["id"] for i in items] == [doc_id]
    assert items[0]["status"] == "draft"

    # 살아 있는 문서를 저장할 때는 그 문구가 붙지 않는다
    res = await client.put(f"/api/sops/{doc_id}", json={"doc": doc, "base_version_no": 2})
    assert res.status_code == 201, res.text
    assert res.json()["warnings"] == []


# ---------------------------------------------------------------------
# 교차 결정 A: 프론트가 encodeURIComponent 로 감싸 보낸 한글 이름을 서버가 되돌려 기록한다 (영문은 그대로)
# ---------------------------------------------------------------------
async def test_percent_encoded_korean_user_header_is_decoded(client):
    encoded = quote("홍길동")                      # "%ED%99%8D%EA%B8%B8%EB%8F%99" (프론트의 encodeURIComponent 와 같은 결과)
    assert encoded.startswith("%")
    res = await client.post("/api/sops", json={"doc": make_doc()}, headers={"X-User": encoded})
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]
    versions = (await client.get(f"/api/sops/{doc_id}/versions")).json()
    assert versions[0]["saved_by"] == "홍길동"

    # 잠금 기록에도 되돌린 이름이 들어간다
    res = await client.post(f"/api/sops/{doc_id}/lock", json={}, headers={"X-User": encoded})
    assert res.status_code == 200, res.text
    assert res.json()["locked_by"] == "홍길동"

    # 영문 이름은 바뀌는 글자가 없다
    res = await client.put(f"/api/sops/{doc_id}", json={"doc": make_doc()}, headers={"X-User": "hong"})
    assert res.status_code == 201, res.text
    assert (await client.get(f"/api/sops/{doc_id}/versions")).json()[0]["saved_by"] == "hong"


# ---------------------------------------------------------------------
# 교차 결정 B: GET / 는 편집기 HTML 의 <head> 바로 뒤에 <meta name="api-base" content="{ROOT_PATH}"> 를 끼워 준다
# ---------------------------------------------------------------------
async def test_index_injects_api_base_meta(client, monkeypatch, tmp_path):
    # 작은 가짜 편집기 파일로 시험한다 (진짜 SOP_STUDIO.html 은 400KB)
    (tmp_path / "index.html").write_text("<!doctype html><html><head lang=\"ko\"><title>t</title></head><body>hi</body></html>", encoding="utf-8")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    monkeypatch.setenv("STATIC_INDEX", "index.html")

    # ROOT_PATH 가 비어 있으면 content=""
    monkeypatch.setenv("ROOT_PATH", "")
    res = await client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert '<head lang="ko"><meta name="api-base" content="">' in res.text
    assert res.text.count("api-base") == 1

    # ROOT_PATH=/sop 이면 content="/sop" (config 가 앞 / 를 붙이고 끝 / 를 뗀다)
    monkeypatch.setenv("ROOT_PATH", "sop/")
    res = await client.get("/")
    assert '<head lang="ko"><meta name="api-base" content="/sop">' in res.text

    # <head> 가 없는 이상한 파일이면 맨 앞에 붙는다 (<header> 는 <head> 로 치지 않는다)
    (tmp_path / "index.html").write_text("<header>no head</header>", encoding="utf-8")
    assert (await client.get("/")).text == '<meta name="api-base" content="/sop"><header>no head</header>'

    # 파일이 없으면 404 static_not_found
    monkeypatch.setenv("STATIC_INDEX", "nope.html")
    res = await client.get("/")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "static_not_found"


# 진짜 편집기 파일(static/SOP_STUDIO.html)로도 GET / 가 200 text/html 이고 meta 가 딱 한 번 들어간다
async def test_index_serves_real_editor_html(client, monkeypatch):
    monkeypatch.setenv("STATIC_DIR", os.path.join(PROJECT_ROOT, "static"))
    monkeypatch.setenv("STATIC_INDEX", "SOP_STUDIO.html")
    monkeypatch.setenv("ROOT_PATH", "")
    res = await client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    # <head> 바로 뒤에 딱 한 번 들어간다 (파일 안 JS 주석에 같은 글자가 예시로 적혀 있을 수 있어 "개수" 대신 "위치" 로 확인)
    assert '<head><meta name="api-base" content="">' in res.text
    assert res.text.index('<meta name="api-base"') == res.text.index("<head>") + len("<head>")
    assert "EMBEDDED_FLOW_B64" in res.text


# ---------------------------------------------------------------------
# 그 밖의 빠져 있던 시나리오
# ---------------------------------------------------------------------
# 같은 문서를 5개 요청이 동시에 PUT 해도 (FOR UPDATE 로 줄을 서므로) 버전이 2..6 으로 빠짐없이 쌓인다
async def test_concurrent_puts_serialize_versions(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]

    responses = await asyncio.gather(
        *[client.put(f"/api/sops/{doc_id}", json={"doc": doc, "base_version_no": None}) for _ in range(5)]
    )
    assert [r.status_code for r in responses] == [201] * 5
    assert sorted(r.json()["version_no"] for r in responses) == [2, 3, 4, 5, 6]
    versions = (await client.get(f"/api/sops/{doc_id}/versions")).json()
    assert [v["version_no"] for v in versions] == [6, 5, 4, 3, 2, 1]


# README 의 curl 예제가 쓰는 examples/sample_doc.json 은 그대로 POST 해서 201 이 되어야 한다
async def test_sample_doc_json_saves(client):
    with open(os.path.join(PROJECT_ROOT, "examples", "sample_doc.json"), encoding="utf-8") as f:
        body = json.load(f)
    res = await client.post("/api/sops", json=body)
    assert res.status_code == 201, res.text
    assert res.json()["sop_no"] == body["doc"]["sop"]["id"]
    assert res.json()["version_no"] == 1


# X-User 헤더가 비어 있으면 saved_by / created_by 는 'anonymous'
async def test_missing_user_header_is_anonymous(client, database_url):
    res = await client.post("/api/sops", json={"doc": make_doc()}, headers={"X-User": ""})
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]
    assert (await client.get(f"/api/sops/{doc_id}/versions")).json()[0]["saved_by"] == "anonymous"
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT created_by FROM sop_documents WHERE id = %s", (doc_id,)).fetchone()[0] == "anonymous"


# 본문 saved_by 가 있으면 created_by 와 saved_by 가 같은 이름이다 (한 요청 안에서 사용자 이름은 하나)
async def test_body_saved_by_is_used_for_created_by_too(client, database_url):
    res = await client.post("/api/sops", json={"doc": make_doc(), "saved_by": "kim"})
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]
    assert (await client.get(f"/api/sops/{doc_id}/versions")).json()[0]["saved_by"] == "kim"
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT created_by FROM sop_documents WHERE id = %s", (doc_id,)).fetchone()[0] == "kim"


# UUID 가 아닌 주소는 422 validation_error (오류 모양도 다른 오류와 같다)
async def test_non_uuid_path_is_422(client):
    res = await client.get("/api/sops/not-a-uuid")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


# 좌표에 inf / nan / 1e999, 글자 크기에 int 범위 밖 값이 와도 저장은 되고 그 값만 0 / None 으로 정리된다 (500 이 아님)
async def test_non_finite_numbers_do_not_break_save(client, database_url):
    doc = make_doc()
    nodes = doc["blocks"][1]["project"]["nodes"]
    nodes[0]["x"] = "inf"
    nodes[0]["y"] = "nan"
    nodes[0]["font_size"] = 2**31
    nodes[1]["x"] = "1e999"
    nodes[1]["y"] = "-inf"
    nodes[1]["font_size"] = "99999999999"

    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert len([w for w in body["warnings"] if "좌표" in w]) == 4
    assert (await client.get(f"/api/sops/{body['id']}")).json()["content"] == doc   # 원본은 그대로 보존

    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT node_key, position, font_size FROM flow_nodes WHERE version_id = %s AND node_key IN ('start_1', 'seq_1') ORDER BY node_key",
            (body["version_id"],),
        ).fetchall()
    assert rows == [("seq_1", {"x": 0, "y": 0}, None), ("start_1", {"x": 0, "y": 0}, None)]


# 목록: ?status= 처럼 빈 값은 기본값(폐기 제외)과 같고, q 의 % _ \ 는 와일드카드가 아니라 글자로 찾는다
async def test_list_blank_status_and_wildcard_escape(client):
    await save(client, make_doc(sop_no="SOP-ETCH-001", name="Chamber PM"))
    await save(client, make_doc(sop_no="SOP_ETCH_002", name="100% 검사"))
    retired_id = (await save(client, make_doc(sop_no="SOP-OLD-001"))).json()["id"]
    await client.delete(f"/api/sops/{retired_id}")

    items = (await client.get("/api/sops", params={"status": ""})).json()
    assert [i["sop_no"] for i in items] == ["SOP-ETCH-001", "SOP_ETCH_002"]       # 폐기 제외

    assert [i["sop_no"] for i in (await client.get("/api/sops", params={"q": "SOP_ETCH"})).json()] == ["SOP_ETCH_002"]
    assert [i["sop_no"] for i in (await client.get("/api/sops", params={"q": "100%"})).json()] == ["SOP_ETCH_002"]
    # "%" 나 "_" 하나만 쳐도 "전부" 가 아니라 그 글자가 실제로 들어 있는 문서만 나온다
    assert [i["sop_no"] for i in (await client.get("/api/sops", params={"q": "%"})).json()] == ["SOP_ETCH_002"]
    assert [i["sop_no"] for i in (await client.get("/api/sops", params={"q": "_"})).json()] == ["SOP_ETCH_002"]
    assert (await client.get("/api/sops", params={"q": "\\"})).json() == []


# 번호 검사는 문서 행을 잠근(FOR UPDATE) 뒤에 한 번 더 이뤄진다: 사전 검사를 건너뛰고 저장 본체를 직접 불러도 400 이 난다
# (PUT 의 사전 검사 통과 → 다른 요청의 PATCH /number → 저장, 순서로 번호가 어긋난 content 가 저장되는 것을 막는 검사)
async def test_sop_no_mismatch_is_rechecked_under_row_lock(client):
    from app import db
    from app.errors import ApiError
    from app.routers.sops import _append_version
    from app.schemas import SaveRequest

    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]

    # PATCH 가 먼저 번호를 바꾼 상황을 흉내 낸 뒤, 옛 번호가 든 문서로 저장 본체만 직접 부른다
    assert (await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-100"})).status_code == 200
    body = SaveRequest(doc=make_doc(sop_no="SOP-ETCH-001"), base_version_no=None)
    async with db.pool.connection() as conn:
        with pytest.raises(ApiError) as caught:
            async with conn.transaction():
                await _append_version(conn, uuid.UUID(doc_id), body, "tester", check_base_version=True)
    assert caught.value.status_code == 400
    assert caught.value.code == "sop_no_mismatch"
    assert caught.value.extra["stored_sop_no"] == "SOP-ETCH-100"

    # 거절된 저장은 버전을 만들지 않았다
    assert len((await client.get(f"/api/sops/{doc_id}/versions")).json()) == 1
