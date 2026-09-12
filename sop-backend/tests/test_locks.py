"""
test_locks.py — 편집 잠금(한 번에 한 사람만 편집)을 시험합니다.

잠금 규칙 (스펙 4.6)
  - 잠금이 없거나, 내 잠금이거나, 만료된 잠금이면 → 획득 성공 200 {locked_by, expires_at}
  - 남이 잡고 있는 유효한 잠금이면 → 423 {code:"locked", locked_by, expires_at}
  - 같은 호출을 다시 부르면 하트비트(살아 있다는 신호)가 되어 만료 시각이 뒤로 늘어난다
  - DELETE 로 내 잠금만 해제할 수 있다
  - 저장은 잠금과 상관없이 되지만, 남이 잠근 문서를 저장하면 응답 warning 으로 알려 준다
"""

import asyncio
from datetime import datetime

import psycopg

from tests.conftest import make_doc, save


async def _lock(client, doc_id, user, ttl_sec=120):
    """잠금 획득 API 를 부르는 짧은 도우미. 응답(httpx.Response)을 돌려준다."""
    return await client.post(f"/api/sops/{doc_id}/lock", json={"user": user, "ttl_sec": ttl_sec})


async def _unlock(client, doc_id, user):
    """잠금 해제 API 를 부르는 도우미. (DELETE 에 본문을 실으려면 client.request 를 써야 한다)"""
    return await client.request("DELETE", f"/api/sops/{doc_id}/lock", json={"user": user})


# hong 이 잠그면 lee 는 423 으로 막히고, hong 이 다시 부르면 만료 시각이 연장되며, hong 이 풀면 lee 가 잡을 수 있다
async def test_lock_conflict_heartbeat_and_release(client):
    doc_id = (await save(client, make_doc())).json()["id"]

    # hong 획득
    res = await _lock(client, doc_id, "hong")
    assert res.status_code == 200, res.text
    first = res.json()
    assert first["locked_by"] == "hong"
    first_expires = datetime.fromisoformat(first["expires_at"])

    # lee 시도 → 잠겨 있음
    res = await _lock(client, doc_id, "lee")
    assert res.status_code == 423
    error = res.json()["error"]
    assert error["code"] == "locked"
    assert error["locked_by"] == "hong"
    assert error["expires_at"].endswith("Z")   # 다른 시각들과 같은 UTC(Z) 형식

    # hong 이 다시 부르면 하트비트: 성공하고 만료 시각이 처음보다 뒤로 늘어난다 (600초로 늘렸으니 확실히 뒤)
    res = await _lock(client, doc_id, "hong", ttl_sec=600)
    assert res.status_code == 200
    second_expires = datetime.fromisoformat(res.json()["expires_at"])
    assert second_expires > first_expires

    # hong 해제 → lee 획득 성공
    res = await _unlock(client, doc_id, "hong")
    assert res.status_code == 200

    res = await _lock(client, doc_id, "lee")
    assert res.status_code == 200, res.text
    assert res.json()["locked_by"] == "lee"


# 남의 잠금은 해제할 수 없다 (lee 가 hong 의 잠금을 DELETE 해도 hong 잠금은 그대로)
async def test_cannot_release_others_lock(client):
    doc_id = (await save(client, make_doc())).json()["id"]
    await _lock(client, doc_id, "hong")

    res = await _unlock(client, doc_id, "lee")
    assert res.status_code == 200        # 남의 잠금 해제는 "아무 일도 안 하고" 200 (오류 아님)
    assert res.json()["status"] == "unlocked"

    # 여전히 hong 이 잡고 있으므로 lee 는 못 잡는다
    res = await _lock(client, doc_id, "lee")
    assert res.status_code == 423
    assert res.json()["error"]["locked_by"] == "hong"


# 잠금이 만료되면(하트비트가 끊기면) 다른 사람이 잡을 수 있다
async def test_expired_lock_can_be_taken(client, database_url):
    doc_id = (await save(client, make_doc())).json()["id"]
    await _lock(client, doc_id, "hong")

    # 시간이 흐른 척: DB 에서 만료 시각을 1분 전으로 되돌린다
    with psycopg.connect(database_url, autocommit=True) as conn:
        # 이 문서의 잠금 만료 시각을 과거로 바꾼다
        conn.execute(
            "UPDATE sop_edit_locks SET expires_at = now() - interval '1 minute' WHERE document_id = %s",
            (doc_id,),
        )

    res = await _lock(client, doc_id, "lee")
    assert res.status_code == 200, res.text
    assert res.json()["locked_by"] == "lee"


# 남이 잠근 문서를 저장해도 저장은 되지만(201) 응답 warning 에 잠근 사람 이름이 들어가고, 문서 열기에도 lock 정보가 보인다
async def test_save_while_locked_by_other_gives_warning(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]
    await _lock(client, doc_id, "hong")

    # lee 가 저장
    res = await save(client, doc, saved_by="lee")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["warning"] is not None
    assert "hong" in body["warning"]

    # 문서를 열면 lock 필드에 hong 이 보인다
    opened = (await client.get(f"/api/sops/{doc_id}")).json()
    assert opened["lock"] is not None
    assert opened["lock"]["locked_by"] == "hong"
    assert "expires_at" in opened["lock"]


# 내가 잠근 문서를 내가 저장하면 warning 이 없다
async def test_save_by_lock_owner_has_no_warning(client):
    doc = make_doc()
    doc_id = (await save(client, doc)).json()["id"]
    await _lock(client, doc_id, "hong")

    res = await save(client, doc, saved_by="hong")
    assert res.status_code == 201, res.text
    assert res.json()["warning"] is None


# 없는 문서 id 로 잠그려 하면 404
async def test_lock_unknown_document_404(client):
    res = await _lock(client, "00000000-0000-0000-0000-000000000000", "hong")
    assert res.status_code == 404
    assert "error" in res.json()


# 잠금 줄이 아직 없는 문서에 두 사람이 동시에 첫 잠금을 요청하면 한 명은 200, 다른 한 명은 423 (500 이 아님)
# (문서 행을 먼저 FOR UPDATE 로 잠가 한 명씩 통과시키므로 둘 다 INSERT 로 달려들어 중복 오류가 나는 일이 없다)
async def test_first_lock_race_gives_200_and_423(client, monkeypatch):
    from psycopg import AsyncConnection

    doc_id = (await save(client, make_doc())).json()["id"]

    # 두 요청이 "정말로 동시에" 잠금 줄을 읽도록, 잠금 줄을 읽는 SQL 뒤에 잠깐 멈추게 해서 겹치는 시간을 넓힌다.
    # (문서 행 잠금이 없다면 둘 다 "잠금 줄 없음" 을 보고 INSERT 로 가서 늦은 쪽이 중복 오류 → 500 이 된다)
    real_execute = AsyncConnection.execute

    async def slow_lock_select(self, query, *args, **kwargs):
        cur = await real_execute(self, query, *args, **kwargs)
        if isinstance(query, str) and query.startswith("SELECT locked_by, expires_at, (expires_at > now())"):
            await asyncio.sleep(0.3)
        return cur

    monkeypatch.setattr(AsyncConnection, "execute", slow_lock_select)

    responses = await asyncio.gather(_lock(client, doc_id, "hong"), _lock(client, doc_id, "lee"))
    assert sorted(r.status_code for r in responses) == [200, 423]
    winner = [r for r in responses if r.status_code == 200][0].json()["locked_by"]
    loser = [r for r in responses if r.status_code == 423][0].json()["error"]
    assert loser["locked_by"] == winner
