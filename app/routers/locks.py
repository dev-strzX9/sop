"""
편집 잠금(lock) API — 같은 문서를 두 사람이 동시에 고치지 못하게 막는 곳.

문서를 편집하기 시작할 때 "내가 잡았다" 고 잠금을 걸고(POST),
편집하는 동안 주기적으로 같은 요청을 다시 보내 잠금을 연장하며(하트비트 = 살아 있다는 신호),
편집을 끝내면 잠금을 풉니다(DELETE).
잠금에는 끝나는 시각(expires_at)이 있어서, 브라우저를 그냥 닫아 버려도 시간이 지나면 저절로 풀립니다.

  POST   /api/sops/{doc_id}/lock   잠금 잡기 / 연장하기
  DELETE /api/sops/{doc_id}/lock   내 잠금 풀기
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg import AsyncConnection

from app.db import get_conn
from app.deps import current_user
from app.errors import ApiError
# "문서 있는지 확인(404)" 은 여러 라우터가 같이 쓰므로 common.py 에서 가져옵니다
from app.routers.common import get_document_or_404
from app.schemas import LockInfo, LockRequest, StatusResponse, UnlockRequest, to_utc_z

router = APIRouter(tags=["locks"])


# ---------------------------------------------------------------------
# 1. 잠금 잡기 / 연장(하트비트)
# ---------------------------------------------------------------------
@router.post("/sops/{doc_id}/lock", response_model=LockInfo)
async def acquire_lock(
    doc_id: UUID,
    body: LockRequest,
    conn: AsyncConnection = Depends(get_conn),
    user: str = Depends(current_user),
):
    """문서의 편집 잠금을 잡습니다. 이미 내가 잡고 있으면 끝나는 시각만 뒤로 미룹니다(연장).

    규칙
      - 잠금이 없다 / 내 잠금이다 / 남의 잠금인데 이미 만료됐다  → 잡는다 (200)
      - 남의 잠금이 아직 유효하다                                 → 423 locked 오류
    """
    await get_document_or_404(conn, doc_id)

    # 본문의 user 가 비어 있으면 X-User 헤더 값을 씁니다.
    who = body.user.strip() or user
    ttl_sec = body.ttl_sec

    # 여러 SQL을 한 묶음(트랜잭션)으로 실행합니다. 중간에 오류가 나면 아무것도 저장되지 않습니다.
    async with conn.transaction():
        # 먼저 "문서 행" 에서 줄을 세운다(FOR UPDATE). 잠금 줄(sop_edit_locks)이 아직 없는 문서에 두 사람이 동시에
        # 첫 잠금을 요청하면, 아래 SELECT ... FOR UPDATE 는 "없는 줄" 은 잠글 수 없어 둘 다 INSERT 로 가서
        # 늦은 쪽이 중복 오류(500)를 내기 때문입니다. 문서 행은 항상 있으니 여기서 확실히 한 명씩 통과시킵니다.
        await conn.execute("SELECT id FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))

        # 이 문서의 잠금 줄을 읽으면서 동시에 걸어 잠근다(FOR UPDATE).
        # 두 사람이 동시에 버튼을 눌러도 한 명이 끝날 때까지 다른 한 명은 줄을 서게 되므로, 한 명만 통과합니다.
        # is_active = 끝나는 시각이 아직 지나지 않았는가 (지금 살아 있는 잠금인가)
        cur = await conn.execute(
            "SELECT locked_by, expires_at, (expires_at > now()) AS is_active "
            "FROM sop_edit_locks WHERE document_id = %s FOR UPDATE",
            (doc_id,),
        )
        existing = await cur.fetchone()

        # 남의 잠금이 아직 살아 있으면 잡을 수 없습니다.
        if existing is not None and existing["is_active"] and existing["locked_by"] != who:
            raise ApiError(
                423,
                "locked",
                f"{existing['locked_by']} 님이 편집 중입니다.",
                locked_by=existing["locked_by"],
                expires_at=to_utc_z(existing["expires_at"]),   # 응답의 다른 시각들과 같은 UTC(Z) 형식
            )

        if existing is None:
            # 잠금 줄이 없으면 새로 만든다. 끝나는 시각 = 지금 + ttl_sec 초 = now() + make_interval(secs => N)
            #   (=> 는 SQL 함수의 인자를 이름으로 넘기는 표기. RETURNING = 방금 넣은 줄을 SELECT 없이 바로 돌려받기)
            cur = await conn.execute(
                "INSERT INTO sop_edit_locks (document_id, locked_by, expires_at) "
                "VALUES (%s, %s, now() + make_interval(secs => %s)) "
                "RETURNING locked_by, expires_at",
                (doc_id, who, ttl_sec),
            )
        else:
            # 내 잠금이거나 만료된 잠금이면, 내 이름으로 바꾸고 끝나는 시각을 지금 + ttl_sec 초로 미룬다
            cur = await conn.execute(
                "UPDATE sop_edit_locks "
                "SET locked_by = %s, locked_at = now(), expires_at = now() + make_interval(secs => %s) "
                "WHERE document_id = %s "
                "RETURNING locked_by, expires_at",
                (who, ttl_sec, doc_id),
            )
        lock_row = await cur.fetchone()

    return LockInfo(locked_by=lock_row["locked_by"], expires_at=lock_row["expires_at"])


# ---------------------------------------------------------------------
# 2. 잠금 풀기
# ---------------------------------------------------------------------
@router.delete("/sops/{doc_id}/lock", response_model=StatusResponse)
async def release_lock(
    doc_id: UUID,
    body: UnlockRequest | None = None,                    # 본문 JSON. DELETE 는 본문이 없을 수 있어 "| None"
    user_query: str = Query(default="", alias="user"),    # 주소 뒤 ?user= 값. 주소에서는 user, 변수 이름은 user_query
    conn: AsyncConnection = Depends(get_conn),
    header_user: str = Depends(current_user),
):
    """내가 잡은 잠금을 풉니다. 남의 잠금은 건드리지 않습니다.
    이미 풀려 있거나 남의 잠금이어도 오류 없이 200 을 돌려줍니다 (여러 번 불러도 결과가 같음).

    누가 푸는지는 다음 순서로 정합니다: 본문 JSON 의 user → 주소 뒤 ?user= → X-User 헤더.
    (DELETE 요청에 본문을 못 싣는 도구도 있어서 세 가지를 모두 받습니다)
    """
    await get_document_or_404(conn, doc_id)

    who = ""
    if body is not None:
        who = body.user.strip()
    if not who:
        who = user_query.strip()
    if not who:
        who = header_user

    # 이 문서의 잠금 중 "내가 잡은 것" 만 지운다. 남의 잠금이면 지워지는 줄이 없다
    await conn.execute(
        "DELETE FROM sop_edit_locks WHERE document_id = %s AND locked_by = %s",
        (doc_id, who),
    )

    return StatusResponse(id=doc_id, status="unlocked")
