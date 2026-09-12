"""
test_conflict.py — 두 사람이 같은 문서를 동시에 편집할 때의 "버전 충돌" 을 시험합니다.

상황: A 와 B 가 둘 다 버전 1 을 열어 놓고 있다.
  A 가 먼저 저장 → 버전 2 가 됨.
  B 도 저장하려는데 B 는 아직 버전 1 을 기준으로 작업했다 (base_version_no=1).
  → 서버는 "지금은 버전 2 인데요" 하고 409 로 거절해야 한다. (B 의 저장이 A 의 작업을 덮어쓰면 안 되니까)
base_version_no 를 안 보내면(None) 검사를 건너뛰고 그냥 저장된다 (신규 문서 / 강제 저장용).
"""

from tests.conftest import make_doc, save


# 내가 연 버전(base_version_no)이 현재 최신과 같으면 저장되고, 이미 남이 올려 놓았으면 409 가 난다
async def test_stale_base_version_is_rejected(client):
    doc = make_doc()

    # 버전 1 생성
    res = await save(client, doc)
    assert res.status_code == 201
    assert res.json()["version_no"] == 1

    # 버전 1 을 기준으로 저장 → 최신이 1 이므로 통과, 버전 2 가 됨
    res = await save(client, doc, base_version_no=1)
    assert res.status_code == 201, res.text
    assert res.json()["version_no"] == 2

    # 다른 탭이 아직 버전 1 을 들고 있다가 저장 → 최신은 2 이므로 충돌
    res = await save(client, doc, base_version_no=1)
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["current_version_no"] == 2


# base_version_no 를 안 보내면(None) 충돌 검사를 건너뛰고 새 버전(3)이 만들어진다
async def test_no_base_version_skips_check(client):
    doc = make_doc()
    await save(client, doc)                      # v1
    await save(client, doc, base_version_no=1)   # v2

    res = await save(client, doc, base_version_no=None)
    assert res.status_code == 201, res.text
    assert res.json()["version_no"] == 3


# 충돌로 거절된 저장은 버전을 만들지 않는다 (버전 목록이 그대로다)
async def test_rejected_save_creates_no_version(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]   # v1
    await save(client, doc, base_version_no=1)         # v2

    res = await save(client, doc, base_version_no=1)   # 409
    assert res.status_code == 409

    versions = (await client.get(f"/api/sops/{doc_id}/versions")).json()
    assert [v["version_no"] for v in versions] == [2, 1]

    # 문서를 열어도 여전히 버전 2
    opened = (await client.get(f"/api/sops/{doc_id}")).json()
    assert opened["version_no"] == 2
