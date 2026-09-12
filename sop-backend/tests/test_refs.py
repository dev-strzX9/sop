"""
test_refs.py — 순서도의 "SOP 상자"(다른 SOP 참조)가 저장될 때 어떻게 정리되는지 시험합니다.

app/refs.py 의 규칙 4가지 (자세한 표는 그 파일 머리말에)
  1. ref_document_id 있고 문서 존재   → 번호·이름을 그 문서의 현재 값으로 덮어씀. 폐기된 문서면 경고
  2. ref_document_id 있는데 문서 없음  → id 를 null 로, 글자는 유지, 경고
  3. id 없음 + 번호 있음               → 번호로 찾아 있으면 id 채움(승격), 없으면 그대로(미작성, 경고 없음)
  4. 둘 다 없음                        → 저장되며 "비어 있는 SOP 상자 N개" 경고 1건

그리고
  - 저장된 content 자체가 정리된 값이어야 한다 (다시 열면 최신 번호가 보이게)
  - flow_nodes.ref_document_id 가 채워지는지, 응답 refs 통계가 맞는지
  - GET /referenced-by (현재 버전만, 자기 제외, 번호만으로 참조도 포함), DocumentOpen.ref_docs, DELETE 의 referenced_by
"""

import copy

import psycopg

from tests.conftest import make_doc, save

NO_SUCH_ID = "00000000-0000-0000-0000-000000000000"


def sop_node(doc):
    """문서의 순서도(blocks[1])에서 sop 상자(sop_1)를 찾아 돌려준다. (make_doc 의 모양에 맞춘 도우미)"""
    for node in doc["blocks"][1]["project"]["nodes"]:
        if node["node_type"] == "sop":
            return node
    raise AssertionError("sop 상자가 없습니다")


def add_sop_node(doc, key, sop_id="", sop_name="", ref_document_id=None):
    """순서도에 sop 상자를 하나 더 붙인다."""
    doc["blocks"][1]["project"]["nodes"].append({
        "node": key, "node_type": "sop", "x": 600, "y": 350,
        "sop_id": sop_id, "sop_name": sop_name, "ref_document_id": ref_document_id, "font_size": 17,
    })


def db_sop_nodes(database_url, version_id):
    """DB 에 저장된 그 버전의 sop 노드 행들을 (ref_sop_no, ref_sop_name, ref_document_id) 로 돌려준다."""
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT node_key, ref_sop_no, ref_sop_name, ref_document_id::text FROM flow_nodes "
            "WHERE version_id = %s AND node_type = 'sop' ORDER BY node_key",
            (version_id,),
        ).fetchall()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


# ---------------------------------------------------------------------
# 규칙 3: 번호만 있는 상자 — 그 번호의 문서가 없으면 미작성(그대로), 있으면 승격(id 채움 + 이름 덮어씀)
# ---------------------------------------------------------------------
async def test_rule3_pending_then_promoted(client, database_url):
    # A 문서는 sop 상자로 SOP-ETCH-002 를 "번호만" 가리킨다. 아직 002 는 없다 → 미작성, 경고 없음, 내용 그대로
    doc_a = make_doc(sop_no="SOP-ETCH-001")
    res = await save(client, doc_a)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 0, "pending": 1, "empty": 0}
    assert body["warnings"] == []
    opened = (await client.get(f"/api/sops/{body['id']}")).json()
    assert opened["content"] == doc_a               # 손대지 않음
    assert opened["ref_docs"] == []                 # 연결된 문서가 없으니 비어 있음
    assert db_sop_nodes(database_url, body["version_id"])["sop_1"] == ("SOP-ETCH-002", "Particle 대응 절차", None)

    # 이제 002 를 만든다 (이름은 상자에 적힌 것과 다르게)
    doc_b = make_doc(sop_no="SOP-ETCH-002", name="Particle 대응 절차 (정식)")
    b_id = (await save(client, doc_b)).json()["id"]

    # A 를 다시 저장하면 승격: ref_document_id 가 채워지고 이름이 002 의 현재 이름으로 바뀐다
    res = await save(client, doc_a, base_version_no=1)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 1, "pending": 0, "empty": 0}
    assert body["warnings"] == []

    opened = (await client.get(f"/api/sops/{body['id']}")).json()
    node = sop_node(opened["content"])
    assert node["ref_document_id"] == b_id
    assert node["sop_id"] == "SOP-ETCH-002"
    assert node["sop_name"] == "Particle 대응 절차 (정식)"
    # 열기 응답의 ref_docs 에 002 의 현재 정보가 들어 있다
    assert opened["ref_docs"] == [
        {"id": b_id, "sop_no": "SOP-ETCH-002", "name": "Particle 대응 절차 (정식)", "area": "P", "status": "draft"}
    ]
    # DB 의 파생 행에도 id 가 들어간다
    assert db_sop_nodes(database_url, body["version_id"])["sop_1"] == ("SOP-ETCH-002", "Particle 대응 절차 (정식)", b_id)


# ---------------------------------------------------------------------
# 규칙 1: id 로 연결된 상자 — 대상 문서의 "현재" 번호·이름으로 글자를 덮어쓴다 (번호가 바뀌어도 따라감)
# ---------------------------------------------------------------------
async def test_rule1_linked_box_follows_current_number_and_name(client, database_url):
    b_id = (await save(client, make_doc(sop_no="SOP-ETCH-002", name="원래 이름"))).json()["id"]

    # A 는 상자에 id 를 넣어 두되 글자는 옛날 것(틀린 번호·이름)으로
    doc_a = make_doc(sop_no="SOP-ETCH-001")
    node = sop_node(doc_a)
    node["ref_document_id"] = b_id
    node["sop_id"] = "OLD-NUMBER"
    node["sop_name"] = "옛 이름"

    res = await save(client, doc_a)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 1, "pending": 0, "empty": 0}
    assert body["warnings"] == []

    saved_node = sop_node((await client.get(f"/api/sops/{body['id']}")).json()["content"])
    assert saved_node["sop_id"] == "SOP-ETCH-002"
    assert saved_node["sop_name"] == "원래 이름"
    assert saved_node["ref_document_id"] == b_id
    assert db_sop_nodes(database_url, body["version_id"])["sop_1"] == ("SOP-ETCH-002", "원래 이름", b_id)

    # B 의 번호를 바꾸면, A 를 다시 저장할 때 새 번호가 자동으로 들어간다
    res = await client.patch(f"/api/sops/{b_id}/number", json={"sop_no": "SOP-ETCH-777"})
    assert res.status_code == 200, res.text
    res = await save(client, doc_a, base_version_no=1)
    assert res.status_code == 201
    saved_node = sop_node((await client.get(f"/api/sops/{body['id']}")).json()["content"])
    assert saved_node["sop_id"] == "SOP-ETCH-777"


# 규칙 1 에서 대상이 폐기(retired)된 문서면 저장은 되지만(201) 경고 1건
async def test_rule1_retired_target_gives_warning(client):
    b_id = (await save(client, make_doc(sop_no="SOP-ETCH-002"))).json()["id"]
    await client.delete(f"/api/sops/{b_id}")   # 폐기

    doc_a = make_doc(sop_no="SOP-ETCH-001")
    sop_node(doc_a)["ref_document_id"] = b_id
    res = await save(client, doc_a)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"]["linked"] == 1
    assert len(body["warnings"]) == 1
    assert "폐기" in body["warnings"][0]
    assert "SOP-ETCH-002" in body["warnings"][0]

    # 열기 응답의 ref_docs 에는 폐기된 문서도 status=retired 로 들어온다
    opened = (await client.get(f"/api/sops/{body['id']}")).json()
    assert opened["ref_docs"][0]["id"] == b_id
    assert opened["ref_docs"][0]["status"] == "retired"


# 규칙 3 으로 승격되는 대상이 폐기 문서여도 저장은 되고(201) 경고가 붙는다
async def test_rule3_promotion_to_retired_target_gives_warning(client):
    b_id = (await save(client, make_doc(sop_no="SOP-ETCH-002"))).json()["id"]
    await client.delete(f"/api/sops/{b_id}")

    res = await save(client, make_doc(sop_no="SOP-ETCH-001"))   # 상자는 번호만 가리킴
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"]["linked"] == 1
    assert any("폐기" in w for w in body["warnings"])


# ---------------------------------------------------------------------
# 규칙 2: id 가 있는데 그 문서가 없거나 UUID 형식이 아님 → id 를 null 로, 글자는 유지, 경고
# ---------------------------------------------------------------------
async def test_rule2_dangling_or_malformed_id_is_cleared_with_warning(client, database_url):
    doc = make_doc(sop_no="SOP-ETCH-001")
    sop_node(doc)["ref_document_id"] = NO_SUCH_ID                       # 없는 문서
    add_sop_node(doc, "sop_2", sop_id="SOP-X", sop_name="엑스", ref_document_id="이건-uuid-아님")   # 형식 불량

    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 0, "pending": 2, "empty": 0}
    assert len(body["warnings"]) == 2
    for message in body["warnings"]:
        assert "찾을 수 없어" in message

    content = (await client.get(f"/api/sops/{body['id']}")).json()["content"]
    nodes = {n["node"]: n for n in content["blocks"][1]["project"]["nodes"] if n["node_type"] == "sop"}
    assert nodes["sop_1"]["ref_document_id"] is None
    assert nodes["sop_1"]["sop_id"] == "SOP-ETCH-002"     # 글자는 그대로
    assert nodes["sop_2"]["ref_document_id"] is None
    assert nodes["sop_2"]["sop_name"] == "엑스"

    rows = db_sop_nodes(database_url, body["version_id"])
    assert rows["sop_1"] == ("SOP-ETCH-002", "Particle 대응 절차", None)
    assert rows["sop_2"] == ("SOP-X", "엑스", None)


# ---------------------------------------------------------------------
# 규칙 4: 번호도 id 도 없는 상자 → 저장되고 합산 경고 1건
# ---------------------------------------------------------------------
async def test_rule4_empty_boxes_give_one_summed_warning(client):
    doc = make_doc(sop_no="SOP-ETCH-001")
    sop_node(doc)["sop_id"] = ""
    sop_node(doc)["sop_name"] = ""
    add_sop_node(doc, "sop_2")                 # 완전히 빈 상자 하나 더
    add_sop_node(doc, "sop_3", sop_id="SOP-NEW")   # 이건 미작성

    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 0, "pending": 1, "empty": 2}
    assert body["warnings"] == ["참조 대상이 비어 있는 SOP 상자 2개"]

    # 저장된 내용은 손대지 않았다
    assert (await client.get(f"/api/sops/{body['id']}")).json()["content"] == doc


# 자기 자신을 가리키는 상자도 허용된다 (PUT 때 자기 문서가 있으니 연결됨)
async def test_self_reference_is_allowed(client):
    doc = make_doc(sop_no="SOP-ETCH-001")
    sop_node(doc)["sop_id"] = "SOP-ETCH-001"
    first = (await save(client, doc)).json()
    # 처음 저장(POST) 때는 문서 행이 먼저 만들어지고 참조를 정리하므로 자기 자신도 바로 연결된다
    assert first["refs"]["linked"] == 1
    node = sop_node((await client.get(f"/api/sops/{first['id']}")).json()["content"])
    assert node["ref_document_id"] == first["id"]
    assert node["sop_name"] == "Chamber PM 절차"

    # 자기 참조는 referenced-by 에 세지 않는다
    assert (await client.get(f"/api/sops/{first['id']}/referenced-by")).json() == []


# ---------------------------------------------------------------------
# referenced-by: 현재 버전만, 자기 제외, id 연결과 번호만 참조 모두 포함, AREA·번호 순
# ---------------------------------------------------------------------
async def test_referenced_by_lists_current_version_only(client):
    # 대상 T (SOP-ETCH-002) 를 만들고, A(연결됨), B(번호만), C(참조 없음) 를 만든다
    t_id = (await save(client, make_doc(sop_no="SOP-ETCH-002", name="대상"))).json()["id"]

    doc_a = make_doc(sop_no="SOP-ETCH-001", area="P")           # 번호만 적혀 있지만 002 가 있으니 승격 → id 연결
    a_id = (await save(client, doc_a)).json()["id"]

    doc_b = make_doc(sop_no="SOP-CMP-001", area="E")
    b_body = (await save(client, doc_b)).json()
    assert b_body["refs"]["linked"] == 1

    doc_c = make_doc(sop_no="SOP-CMP-002", area="E")
    sop_node(doc_c)["sop_id"] = "SOP-OTHER"                      # 다른 번호 → 미작성, T 와 무관
    await save(client, doc_c)

    res = await client.get(f"/api/sops/{t_id}/referenced-by")
    assert res.status_code == 200, res.text
    items = res.json()
    assert [(i["sop_no"], i["area"], i["version_no"]) for i in items] == [("SOP-CMP-001", "E", 1), ("SOP-ETCH-001", "P", 1)]
    assert items[1]["id"] == a_id
    assert items[1]["name"] == "Chamber PM 절차"
    assert items[1]["status"] == "draft"

    # A 의 새 버전에서 참조를 지우면 → 현재 버전 기준이므로 A 는 목록에서 빠진다 (옛 버전의 참조는 세지 않음)
    doc_a2 = copy.deepcopy(doc_a)
    doc_a2["blocks"][1]["project"]["nodes"] = [n for n in doc_a2["blocks"][1]["project"]["nodes"] if n["node_type"] != "sop"]
    doc_a2["blocks"][1]["project"]["edges"] = [e for e in doc_a2["blocks"][1]["project"]["edges"] if e["target"] != "sop_1"]
    await save(client, doc_a2, base_version_no=1)
    items = (await client.get(f"/api/sops/{t_id}/referenced-by")).json()
    assert [i["sop_no"] for i in items] == ["SOP-CMP-001"]


# 번호만 적힌(아직 연결 안 된) 참조도 referenced-by 에 잡힌다: 002 가 나중에 생기면 001 이 "번호로" 참조 중인 것이 보인다
async def test_referenced_by_includes_number_only_references(client):
    a_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]   # 002 없음 → 번호만
    t_id = (await save(client, make_doc(sop_no="SOP-ETCH-002"))).json()["id"]   # 이제 002 생김

    items = (await client.get(f"/api/sops/{t_id}/referenced-by")).json()
    assert [i["id"] for i in items] == [a_id]

    # 폐기(DELETE) 응답에도 그 수가 들어간다
    res = await client.delete(f"/api/sops/{t_id}")
    assert res.status_code == 200
    assert res.json()["referenced_by"] == 1


# 없는 문서의 referenced-by 는 404 not_found
async def test_referenced_by_unknown_document_404(client):
    res = await client.get(f"/api/sops/{NO_SUCH_ID}/referenced-by")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


# 번호만으로 세는 조건은 "ref_document_id 가 NULL 인" 상자에만 적용된다.
#   T(002) 에 A 가 id 로 연결됨 → T 의 번호를 900 으로 변경 → 새 문서 U 가 002 를 차지.
#   A 의 상자는 ref_document_id=T 이면서 ref_sop_no 글자는 옛 번호 '002' 로 남아 있지만,
#   id 가 있는 상자이므로 U 의 referenced-by 에 잡히면 안 되고 T 의 referenced-by 에만 잡혀야 한다.
async def test_referenced_by_number_match_only_counts_unlinked_boxes(client):
    t_id = (await save(client, make_doc(sop_no="SOP-ETCH-002", name="대상 T"))).json()["id"]
    a_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]      # 002 가 있으니 승격 → id 연결
    assert (await client.patch(f"/api/sops/{t_id}/number", json={"sop_no": "SOP-ETCH-900"})).status_code == 200
    u_id = (await save(client, make_doc(sop_no="SOP-ETCH-002", name="새 문서 U"))).json()["id"]

    assert (await client.get(f"/api/sops/{u_id}/referenced-by")).json() == []
    assert [i["id"] for i in (await client.get(f"/api/sops/{t_id}/referenced-by")).json()] == [a_id]


# 한 문서에 같은 대상을 가리키는 상자가 둘이어도 referenced-by 와 ref_docs 에는 그 문서가 한 번만 나온다 (refs.linked 는 2)
async def test_referenced_by_lists_each_document_once(client):
    t_id = (await save(client, make_doc(sop_no="SOP-T", name="대상"))).json()["id"]

    doc_a = make_doc(sop_no="SOP-A")
    sop_node(doc_a)["sop_id"] = "SOP-T"
    add_sop_node(doc_a, "sop_2", sop_id="SOP-T")          # 같은 대상을 가리키는 상자 하나 더
    res = await save(client, doc_a)
    assert res.status_code == 201, res.text
    assert res.json()["refs"]["linked"] == 2

    items = (await client.get(f"/api/sops/{t_id}/referenced-by")).json()
    assert [i["sop_no"] for i in items] == ["SOP-A"]
    opened = (await client.get(f"/api/sops/{res.json()['id']}")).json()
    assert [d["id"] for d in opened["ref_docs"]] == [t_id]


# 규칙 2 인데 번호 글자까지 비어 있는 상자(id 만 있고 그 문서가 없음): 통계는 "비어 있음", 경고는 연결 해제 1건 + 빈 상자 합산 1건
# (refs.py 머리말 표에 적힌 대로. 프론트의 상태 표시(미작성/비어 있음)와 맞추기 위해 테스트로 고정)
async def test_rule2_dangling_id_without_number_counts_as_empty(client):
    doc = make_doc(sop_no="SOP-ETCH-001")
    node = sop_node(doc)
    node["sop_id"] = ""
    node["sop_name"] = ""
    node["ref_document_id"] = NO_SUCH_ID

    res = await save(client, doc)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["refs"] == {"linked": 0, "pending": 0, "empty": 1}
    assert len(body["warnings"]) == 2
    assert "찾을 수 없어 연결을 풀었습니다" in body["warnings"][0]
    assert body["warnings"][1] == "참조 대상이 비어 있는 SOP 상자 1개"
    assert sop_node((await client.get(f"/api/sops/{body['id']}")).json()["content"])["ref_document_id"] is None


# 저장 1회당 참조 조회 SQL 은 최대 2번 (id 목록 한 번, 번호 목록 한 번). 상자마다 조회하지 않는다.
async def test_resolve_references_queries_at_most_twice_per_save(client, monkeypatch):
    from app import refs

    calls = {"by_ids": 0, "by_sop_nos": 0}
    real_by_ids = refs._load_documents_by_ids
    real_by_nos = refs._load_documents_by_sop_nos

    async def counting_by_ids(conn, ids):
        calls["by_ids"] += 1
        return await real_by_ids(conn, ids)

    async def counting_by_nos(conn, sop_nos):
        calls["by_sop_nos"] += 1
        return await real_by_nos(conn, sop_nos)

    monkeypatch.setattr(refs, "_load_documents_by_ids", counting_by_ids)
    monkeypatch.setattr(refs, "_load_documents_by_sop_nos", counting_by_nos)

    t_id = (await save(client, make_doc(sop_no="SOP-T"))).json()["id"]
    calls["by_ids"] = calls["by_sop_nos"] = 0

    # 상자 6개: id 연결 2개, 번호만 3개(있는 번호·없는 번호 섞어서), 빈 것 1개
    doc = make_doc(sop_no="SOP-A")
    sop_node(doc)["ref_document_id"] = t_id
    add_sop_node(doc, "sop_2", sop_id="SOP-T", ref_document_id=t_id)
    add_sop_node(doc, "sop_3", sop_id="SOP-T")
    add_sop_node(doc, "sop_4", sop_id="SOP-NEW-1")
    add_sop_node(doc, "sop_5", sop_id="SOP-NEW-2")
    add_sop_node(doc, "sop_6")
    res = await save(client, doc)
    assert res.status_code == 201, res.text
    assert res.json()["refs"] == {"linked": 3, "pending": 2, "empty": 1}
    assert calls == {"by_ids": 1, "by_sop_nos": 1}


# 특정 버전 열기(GET /versions/{n})에도 ref_docs 가 붙는다
async def test_open_version_has_ref_docs(client):
    b_id = (await save(client, make_doc(sop_no="SOP-ETCH-002", name="대상"))).json()["id"]
    a_id = (await save(client, make_doc(sop_no="SOP-ETCH-001"))).json()["id"]

    res = await client.get(f"/api/sops/{a_id}/versions/1")
    assert res.status_code == 200
    assert [d["id"] for d in res.json()["ref_docs"]] == [b_id]

    # by-no 로 열어도 같다
    assert (await client.get("/api/sops/by-no/SOP-ETCH-001")).json()["ref_docs"] == res.json()["ref_docs"]
