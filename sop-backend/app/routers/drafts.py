"""
자동 임시 저장(draft) API — 편집 중인 내용을 사용자별로 잠깐 맡겨 두는 곳.

"저장" 버튼을 누르면 정식 버전이 하나 쌓이지만, 임시 저장은 버전을 쌓지 않습니다.
사용자 한 명당 문서 하나에 임시본 하나만 있고, 다시 보내면 덮어씁니다.
브라우저가 갑자기 꺼져도 마지막 작업 내용을 되찾을 수 있게 하기 위한 기능입니다.
(예전에는 브라우저 localStorage 에 두던 것을 서버로 옮긴 것)

  PUT    /api/sops/{doc_id}/draft          임시본 저장(덮어쓰기)
  GET    /api/sops/{doc_id}/draft?user=    내 임시본 읽기
  DELETE /api/sops/{doc_id}/draft?user=    내 임시본 지우기
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.deps import current_user
from app.errors import ApiError
# "문서 있는지 확인(404)" 은 여러 라우터가 같이 쓰므로 common.py 에서 가져옵니다
from app.routers.common import get_document_or_404
from app.schemas import DraftRequest, DraftResponse, StatusResponse

router = APIRouter(tags=["drafts"])


# ---------------------------------------------------------------------
# 1. 임시본 저장 (덮어쓰기)
# ---------------------------------------------------------------------
@router.put("/sops/{doc_id}/draft", response_model=DraftResponse)
async def save_draft(
    doc_id: UUID,
    body: DraftRequest,
    conn: AsyncConnection = Depends(get_conn),
    user: str = Depends(current_user),
):
    """내 임시본을 저장합니다. 이미 있으면 내용과 시각을 덮어씁니다. 버전은 쌓이지 않습니다."""
    await get_document_or_404(conn, doc_id)

    # 본문의 user 가 비어 있으면 X-User 헤더 값을 씁니다.
    who = body.user.strip() or user

    # (문서, 사용자) 조합으로 임시본을 넣는다.
    #   ON CONFLICT (...) DO UPDATE = 같은 (문서, 사용자) 줄이 이미 있으면 새로 넣지 않고 그 줄을 고친다
    #   EXCLUDED.content            = "방금 넣으려던 새 내용" 을 가리키는 PostgreSQL 표기
    #   RETURNING                   = 저장된 줄을 SELECT 없이 바로 돌려받기
    cur = await conn.execute(
        "INSERT INTO sop_drafts (document_id, user_id, content) VALUES (%s, %s, %s) "
        "ON CONFLICT (document_id, user_id) DO UPDATE SET content = EXCLUDED.content, updated_at = now() "
        "RETURNING document_id, user_id, content, updated_at",
        (doc_id, who, Jsonb(body.content)),
    )
    draft_row = await cur.fetchone()
    return draft_row


# ---------------------------------------------------------------------
# 2. 임시본 읽기
# ---------------------------------------------------------------------
@router.get("/sops/{doc_id}/draft", response_model=DraftResponse)
async def get_draft(
    doc_id: UUID,
    user_query: str = Query(default="", alias="user"),   # 주소 뒤 ?user= 값. 주소에서는 user, 변수 이름은 user_query
    conn: AsyncConnection = Depends(get_conn),
    header_user: str = Depends(current_user),
):
    """내 임시본을 돌려줍니다. 주소 뒤 ?user= 가 비어 있으면 X-User 헤더 값을 씁니다.
    임시본이 없으면 404 draft_not_found 오류를 냅니다 (프론트는 이걸 "복구할 것 없음" 으로 봅니다)."""
    await get_document_or_404(conn, doc_id)

    who = user_query.strip() or header_user

    # 이 문서에 이 사용자가 맡겨 둔 임시본 한 줄을 찾는다
    cur = await conn.execute(
        "SELECT document_id, user_id, content, updated_at "
        "FROM sop_drafts WHERE document_id = %s AND user_id = %s",
        (doc_id, who),
    )
    draft_row = await cur.fetchone()
    if draft_row is None:
        raise ApiError(404, "draft_not_found", "임시 저장된 내용이 없습니다.", user=who)
    return draft_row


# ---------------------------------------------------------------------
# 3. 임시본 지우기
# ---------------------------------------------------------------------
@router.delete("/sops/{doc_id}/draft", response_model=StatusResponse)
async def delete_draft(
    doc_id: UUID,
    user_query: str = Query(default="", alias="user"),
    conn: AsyncConnection = Depends(get_conn),
    header_user: str = Depends(current_user),
):
    """내 임시본을 지웁니다. 정식 저장이 끝났거나 사용자가 "버리기" 를 눌렀을 때 씁니다.
    지울 것이 없어도 오류 없이 200 을 돌려줍니다."""
    await get_document_or_404(conn, doc_id)

    who = user_query.strip() or header_user

    # 이 문서에 이 사용자가 맡겨 둔 임시본을 지운다. 없으면 아무 일도 일어나지 않는다
    await conn.execute(
        "DELETE FROM sop_drafts WHERE document_id = %s AND user_id = %s",
        (doc_id, who),
    )

    return StatusResponse(id=doc_id, status="deleted")
