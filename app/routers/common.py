"""
common.py — 여러 라우터(sops / versions / locks)가 똑같이 쓰는 작은 도우미 모음.

예전에는 "문서가 있는지 확인해서 없으면 404" 와 "지금 살아 있는 잠금 찾기" 가 파일마다 복사되어 있어서,
404 문구 하나를 바꾸려 해도 네 곳을 고쳐야 했습니다. 그래서 여기 한 곳에 모아 두고 각 라우터가 가져다 씁니다.
"열기 응답의 번호 보정"(content_with_current_sop_no)도 sops.py 와 versions.py 가 같이 쓰므로 여기 둡니다.
"""

from uuid import UUID

from psycopg import AsyncConnection

from app.errors import ApiError
from app.schemas import LockInfo


async def get_document_or_404(conn: AsyncConnection, doc_id: UUID) -> dict:
    """
    문서 id 로 문서 행(id, sop_no, status, current_version_id)을 찾아 돌려줍니다. 없으면 404 not_found.
    거의 모든 API 가 "그 문서가 정말 있는지" 부터 확인해야 해서 따로 떼어 두었습니다.
    """
    cur = await conn.execute(
        "SELECT id, sop_no, status, current_version_id FROM sop_documents WHERE id = %s", (doc_id,)
    )
    document = await cur.fetchone()
    if document is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
    return document


async def find_active_lock(conn: AsyncConnection, doc_id: UUID) -> LockInfo | None:
    """
    이 문서에 아직 유효한(만료되지 않은) 편집 잠금이 있으면 그 정보를, 없으면 None 을 돌려줍니다.
    문서를 열 때 "지금 누가 편집 중인지" 를 같이 알려 주기 위해 필요합니다.
    """
    # 끝나는 시각(expires_at)이 지금보다 뒤인 잠금만 찾는다 = 아직 살아 있는 잠금
    cur = await conn.execute(
        "SELECT locked_by, expires_at FROM sop_edit_locks WHERE document_id = %s AND expires_at > now()",
        (doc_id,),
    )
    lock_row = await cur.fetchone()
    if lock_row is None:
        return None
    return LockInfo(locked_by=lock_row["locked_by"], expires_at=lock_row["expires_at"])


def content_with_current_sop_no(content: dict, sop_no: str) -> dict:
    """
    저장된 content 의 sop.id 를 문서의 "지금" 번호(sop_no)로 맞춘 사본을 돌려줍니다. 원본과 DB 는 건드리지 않습니다.
    PATCH /number 는 버전(content)을 손대지 않으므로, 번호를 바꾼 뒤에는 content 안의 sop.id(옛 번호)와 문서의
    sop_no(새 번호)가 다릅니다. 열기 응답을 만들 때만 맞춰 주면 편집기가 옛 번호를 입력칸에 채우는 일이 없습니다.
    """
    if not isinstance(content, dict):
        return content
    sop = content.get("sop")
    if not isinstance(sop, dict) or sop.get("id") == sop_no:
        return content
    fixed = dict(content)            # 바깥 껍데기만 복사 (blocks 같은 큰 덩어리는 그대로 공유)
    fixed["sop"] = dict(sop, id=sop_no)
    return fixed
