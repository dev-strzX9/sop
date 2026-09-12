"""
test_rename.py — SOP 번호 변경 (PATCH /api/sops/{id}/number) 을 시험합니다.

규칙
  - 성공하면 200 {id, sop_no(새), old_sop_no, referenced_by}
  - 같은 번호면 아무것도 안 바꾸고 200
  - 형식이 틀리면 422 invalid_document, 이미 쓰는 번호면 409 sop_no_taken, 없는 문서면 404
  - 다른 사용자가 잠금 중이면 423 locked (본인 잠금은 허용). 만료된 남의 잠금은 무시
  - sop_documents.sop_no 와 updated_at 만 바뀌고 버전(sop_versions)은 절대 손대지 않는다
  - 다만 "열기" 응답의 content.sop.id 는 지금 번호로 보정되어 나온다 (교차 결정 C. DB 는 옛 번호 그대로)
"""

import psycopg

from tests.conftest import make_doc, save


async def test_rename_success_and_versions_untouched(client, database_url):
    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]
    await save(client, make_doc(sop_no="SOP-ETCH-001"), base_version_no=1)   # v2

    with psycopg.connect(database_url) as conn:
        before_versions = conn.execute(
            "SELECT id, version_no, content, saved_at FROM sop_versions WHERE document_id = %s ORDER BY version_no", (doc_id,)
        ).fetchall()
        before_updated = conn.execute("SELECT updated_at FROM sop_documents WHERE id = %s", (doc_id,)).fetchone()[0]

    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-100"})
    assert res.status_code == 200, res.text
    assert res.json() == {"id": doc_id, "sop_no": "SOP-ETCH-100", "old_sop_no": "SOP-ETCH-001", "referenced_by": 0}

    # 새 번호로 열리고, 옛 번호로는 안 열린다. 버전 번호는 그대로 2.
    opened = await client.get("/api/sops/by-no/SOP-ETCH-100")
    assert opened.status_code == 200
    assert opened.json()["id"] == doc_id
    assert opened.json()["version_no"] == 2
    assert (await client.get("/api/sops/by-no/SOP-ETCH-001")).status_code == 404

    # 교차 결정 C: 열기 응답의 content.sop.id 는 새 번호로 보정되어 나온다 (id 로 열기, 번호로 열기, 특정 버전 열기 모두)
    assert opened.json()["content"]["sop"]["id"] == "SOP-ETCH-100"
    assert (await client.get(f"/api/sops/{doc_id}")).json()["content"]["sop"]["id"] == "SOP-ETCH-100"
    assert (await client.get(f"/api/sops/{doc_id}/versions/1")).json()["content"]["sop"]["id"] == "SOP-ETCH-100"
    # sop.id 외의 내용은 그대로
    expected = make_doc(sop_no="SOP-ETCH-001")
    expected["sop"]["id"] = "SOP-ETCH-100"
    assert opened.json()["content"] == expected

    with psycopg.connect(database_url) as conn:
        after_versions = conn.execute(
            "SELECT id, version_no, content, saved_at FROM sop_versions WHERE document_id = %s ORDER BY version_no", (doc_id,)
        ).fetchall()
        after_updated = conn.execute("SELECT updated_at FROM sop_documents WHERE id = %s", (doc_id,)).fetchone()[0]
    assert after_versions == before_versions        # 버전 행은 내용까지 그대로 (DB 의 content 안 sop.id 는 옛 번호 그대로)
    assert after_versions[0][2]["sop"]["id"] == "SOP-ETCH-001"
    assert after_updated > before_updated           # 문서의 updated_at 만 갱신

    # 그 뒤 PUT 은 문서 안의 번호가 새 번호여야 통과한다
    res = await client.put(f"/api/sops/{doc_id}", json={"doc": make_doc(sop_no="SOP-ETCH-001")})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "sop_no_mismatch"
    res = await client.put(f"/api/sops/{doc_id}", json={"doc": make_doc(sop_no="SOP-ETCH-100"), "base_version_no": 2})
    assert res.status_code == 201, res.text
    assert res.json()["version_no"] == 3


# 같은 번호로 바꾸면 그냥 200 (아무것도 바뀌지 않음)
async def test_rename_to_same_number_is_noop(client):
    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]
    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-001"})
    assert res.status_code == 200, res.text
    assert res.json()["sop_no"] == "SOP-ETCH-001"
    assert res.json()["old_sop_no"] == "SOP-ETCH-001"


# 이미 다른 문서가 쓰는 번호로는 못 바꾼다: 409 sop_no_taken + existing_id
async def test_rename_to_taken_number_is_409(client):
    a_id = (await save(client, make_doc(sop_no="SOP-A"))).json()["id"]
    b_id = (await save(client, make_doc(sop_no="SOP-B"))).json()["id"]

    res = await client.patch(f"/api/sops/{a_id}/number", json={"sop_no": "SOP-B"})
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "sop_no_taken"
    assert error["existing_id"] == b_id
    # A 의 번호는 그대로
    assert (await client.get(f"/api/sops/{a_id}")).json()["sop_no"] == "SOP-A"


# 형식이 틀린 번호(한글, 공백, 빈 값)는 422 invalid_document
async def test_rename_invalid_number_is_422(client):
    doc_id = (await save(client, make_doc())).json()["id"]
    for bad in ["SOP 001", "한글", "", "  "]:
        res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": bad})
        assert res.status_code == 422, bad
        assert res.json()["error"]["code"] == "invalid_document"


# 없는 문서는 404
async def test_rename_unknown_document_404(client):
    res = await client.patch("/api/sops/00000000-0000-0000-0000-000000000000/number", json={"sop_no": "SOP-X"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


# 남의 잠금이라도 이미 만료됐으면 번호를 바꿀 수 있다 (expires_at > now() 조건)
async def test_rename_ignores_expired_lock_of_other_user(client, database_url):
    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]
    await client.post(f"/api/sops/{doc_id}/lock", json={"user": "hong", "ttl_sec": 300})

    # 시간이 흐른 척: DB 에서 만료 시각을 1분 전으로 되돌린다
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "UPDATE sop_edit_locks SET expires_at = now() - interval '1 minute' WHERE document_id = %s", (doc_id,)
        )

    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-002", "user": "lee"})
    assert res.status_code == 200, res.text
    assert res.json()["sop_no"] == "SOP-ETCH-002"


# 다른 사용자가 잠금 중이면 423 locked, 본인 잠금이면 허용. user 는 본문 → 헤더 순으로 정한다
async def test_rename_respects_other_users_lock(client):
    doc_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]
    await client.post(f"/api/sops/{doc_id}/lock", json={"user": "hong", "ttl_sec": 300})

    # lee(본문) 가 시도 → 423
    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-002", "user": "lee"})
    assert res.status_code == 423
    error = res.json()["error"]
    assert error["code"] == "locked"
    assert error["locked_by"] == "hong"
    assert error["expires_at"].endswith("Z")

    # 헤더의 tester 도 남이므로 423
    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-002"})
    assert res.status_code == 423

    # hong 본인은 허용 (헤더로)
    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-002"}, headers={"X-User": "hong"})
    assert res.status_code == 200, res.text
    assert res.json()["sop_no"] == "SOP-ETCH-002"

    # hong 본인 (본문으로)
    res = await client.patch(f"/api/sops/{doc_id}/number", json={"sop_no": "SOP-ETCH-003", "user": "hong"})
    assert res.status_code == 200, res.text


# 번호를 바꿔도 id 로 연결된 다른 문서의 참조는 끊기지 않고, 응답 referenced_by 에 그 수가 나온다
async def test_rename_keeps_linked_references(client):
    t_id = (await save(client, make_doc(sop_no="SOP-ETCH-002"))).json()["id"]     # 대상
    a_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]     # 002 를 가리킴 → 연결됨

    res = await client.patch(f"/api/sops/{t_id}/number", json={"sop_no": "SOP-ETCH-900"})
    assert res.status_code == 200, res.text
    assert res.json()["referenced_by"] == 1

    # A 를 열면 ref_docs 에 새 번호가 보인다 (저장된 content 의 글자는 아직 옛 번호지만 편집기는 ref_docs 로 최신 번호를 그린다)
    opened = (await client.get(f"/api/sops/{a_id}")).json()
    assert opened["ref_docs"][0]["id"] == t_id
    assert opened["ref_docs"][0]["sop_no"] == "SOP-ETCH-900"
    assert (await client.get(f"/api/sops/{t_id}/referenced-by")).json()[0]["id"] == a_id


# 번호를 "글자로만" 가리키던(미작성) 참조는 번호 변경을 따라오지 못한다 — 이것이 현재의 의도된 동작이다.
#   A 가 002 를 번호만으로 가리킴(002 없음) → 002 생성(A 는 재저장 안 함) → 002 를 900 으로 변경
#   → PATCH 응답 referenced_by 는 0 (새 번호 기준: id 연결도, 900 글자 참조도 없음), 900 의 referenced-by 도 [].
#   A 의 상자는 여전히 "SOP-ETCH-002" 글자만 들고 있어 다음에 A 를 저장할 때도 승격되지 않는다(그 번호의 문서가 없으니 미작성 그대로).
#   서버는 이런 문서 수를 로그에만 남긴다. (id 로 연결된 참조와 달리 자동으로 따라가지 않는다는 점을 테스트로 고정)
async def test_rename_does_not_carry_number_only_references(client):
    doc_a = make_doc(sop_no="SOP-ETCH-001")
    a_id = (await save(client, doc_a)).json()["id"]                                  # 002 없음 → 번호만
    t_id = (await save(client, make_doc(sop_no="SOP-ETCH-002"))).json()["id"]        # 이제 002 생김 (A 는 재저장 안 함)
    assert [i["id"] for i in (await client.get(f"/api/sops/{t_id}/referenced-by")).json()] == [a_id]

    res = await client.patch(f"/api/sops/{t_id}/number", json={"sop_no": "SOP-ETCH-900"})
    assert res.status_code == 200, res.text
    assert res.json()["referenced_by"] == 0
    assert (await client.get(f"/api/sops/{t_id}/referenced-by")).json() == []

    # A 를 다시 저장해도 옛 번호 글자로는 아무 문서도 찾지 못해 미작성 그대로
    body = (await save(client, doc_a, base_version_no=1)).json()
    assert body["refs"] == {"linked": 0, "pending": 1, "empty": 0}
    assert (await client.get(f"/api/sops/{t_id}/referenced-by")).json() == []
