"""
test_versions_drafts.py — 버전 이력과 자동 임시 저장(draft)을 시험합니다.

  - 특정 버전 열기: GET /api/sops/{id}/versions/{번호} → 그때 저장한 문서가 그대로 나온다
  - 버전 비교(diff): 두 버전의 순서도 노드가 어떻게 달라졌는지 (추가/삭제/수정)
  - 임시 저장(draft): 버전을 쌓지 않고 사용자별로 1개만 덮어쓴다. 정식 저장하면 지워진다.
"""

import copy
import json

from tests.conftest import make_doc, save


# 버전 1 을 열면 처음 저장했던 문서가, 버전 2 를 열면 두 번째 문서가 그대로 나온다
async def test_open_specific_version(client):
    doc_v1 = make_doc()
    doc_id = (await save(client, doc_v1)).json()["id"]

    doc_v2 = copy.deepcopy(doc_v1)
    doc_v2["sop"]["name"] = "이름 바꿈"
    await save(client, doc_v2)

    res = await client.get(f"/api/sops/{doc_id}/versions/1")
    assert res.status_code == 200
    body = res.json()
    assert body["version_no"] == 1
    assert body["content"] == doc_v1

    res = await client.get(f"/api/sops/{doc_id}/versions/2")
    assert res.status_code == 200
    assert res.json()["content"] == doc_v2


# 없는 버전 번호를 열면 404
async def test_open_missing_version_404(client):
    doc_id = (await save(client, make_doc())).json()["id"]
    res = await client.get(f"/api/sops/{doc_id}/versions/99")
    assert res.status_code == 404
    assert "error" in res.json()


# 없는 문서 id 의 버전 목록을 보면 404
async def test_versions_of_unknown_document_404(client):
    res = await client.get("/api/sops/00000000-0000-0000-0000-000000000000/versions")
    assert res.status_code == 404


# diff: v1(seq 1개) → v2(seq 하나 추가 + seq_1 의 action 변경) 이면 added 1건, changed 에 seq_1 의 action 변경(from/to)이 있다
async def test_diff_between_versions(client):
    doc_v1 = make_doc()
    doc_id = (await save(client, doc_v1)).json()["id"]

    doc_v2 = make_doc(extra_seq=1)     # seq_2 가 추가됨
    for node in doc_v2["blocks"][1]["project"]["nodes"]:
        if node["node"] == "seq_1":
            node["action"] = "Chamber Open 후 Wet Clean"
    await save(client, doc_v2)

    res = await client.get(f"/api/sops/{doc_id}/versions/1/diff/2")
    assert res.status_code == 200, res.text
    diff = res.json()

    # 추가된 노드는 seq_2 하나
    assert len(diff["added"]) == 1
    assert "seq_2" in json.dumps(diff["added"], ensure_ascii=False)

    # 삭제된 노드는 없음
    assert diff["removed"] == []

    # 수정된 노드는 seq_1 하나. 응답 모양은 versions.py 의 diff_versions 설명에 확정되어 있으므로 그대로 비교한다
    # (from = 예전 값, to = 새 값. 둘이 뒤바뀌면 실패해야 한다)
    assert len(diff["changed"]) == 1
    changed = diff["changed"][0]
    assert (changed["instance_id"], changed["node_key"], changed["node_type"]) == ("flow_1725000000000_3", "seq_1", "seq")
    assert changed["fields"] == {"action": {"from": "Chamber Open", "to": "Chamber Open 후 Wet Clean"}}


# 같은 내용으로 두 번 저장하면 diff 는 비어 있다
async def test_diff_identical_versions_is_empty(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]
    await save(client, doc)

    diff = (await client.get(f"/api/sops/{doc_id}/versions/1/diff/2")).json()
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["changed"] == []


# draft: PUT 으로 임시 저장한 내용이 GET 으로 그대로 돌아오고, 다시 PUT 하면 덮어써진다 (버전은 안 늘어난다)
async def test_draft_put_get_overwrite(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]

    draft_content = copy.deepcopy(doc)
    draft_content["sop"]["name"] = "작업 중 (임시)"
    res = await client.put(f"/api/sops/{doc_id}/draft", json={"user": "tester", "content": draft_content})
    assert res.status_code == 200, res.text

    res = await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})
    assert res.status_code == 200
    body = res.json()
    assert body["document_id"] == doc_id
    assert body["user_id"] == "tester"
    assert body["content"] == draft_content

    # 한 번 더 덮어쓰기
    draft_content["sop"]["name"] = "작업 중 (임시 2)"
    await client.put(f"/api/sops/{doc_id}/draft", json={"user": "tester", "content": draft_content})
    body = (await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})).json()
    assert body["content"]["sop"]["name"] == "작업 중 (임시 2)"

    # 버전은 여전히 1 개
    versions = (await client.get(f"/api/sops/{doc_id}/versions")).json()
    assert len(versions) == 1


# 정식 저장(PUT /api/sops/{id})을 하면 그 사용자의 임시 저장은 지워져서 GET 이 404 가 된다
async def test_draft_is_removed_after_real_save(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]

    await client.put(f"/api/sops/{doc_id}/draft", json={"user": "tester", "content": doc})
    assert (await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})).status_code == 200

    # saved_by 를 비우면 X-User 헤더의 "tester" 가 저장한 사람이 된다
    res = await save(client, doc, saved_by="")
    assert res.status_code == 201

    res = await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})
    assert res.status_code == 404


# 임시 저장은 사용자별로 따로다: hong 의 정식 저장은 lee 의 임시 저장을 지우지 않는다
async def test_draft_is_per_user(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]

    await client.put(f"/api/sops/{doc_id}/draft", json={"user": "lee", "content": doc})
    await save(client, doc, saved_by="hong")

    res = await client.get(f"/api/sops/{doc_id}/draft", params={"user": "lee"})
    assert res.status_code == 200


# DELETE 로 임시 저장을 지우면 200 이고, 그 뒤 GET 은 404
async def test_draft_delete(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]
    await client.put(f"/api/sops/{doc_id}/draft", json={"user": "tester", "content": doc})

    res = await client.delete(f"/api/sops/{doc_id}/draft", params={"user": "tester"})
    assert res.status_code == 200

    res = await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})
    assert res.status_code == 404


# 임시 저장이 없는 상태에서 GET 하면 404
async def test_draft_missing_404(client):
    doc_id = (await save(client, make_doc())).json()["id"]
    res = await client.get(f"/api/sops/{doc_id}/draft", params={"user": "tester"})
    assert res.status_code == 404
