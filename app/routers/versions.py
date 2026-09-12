"""
버전(versions) API — 문서를 저장할 때마다 쌓이는 "버전"을 보여 주는 곳.

문서를 저장할 때마다 sop_versions 표에 한 줄씩 쌓입니다 (1번, 2번, 3번 ...).
이 파일은 그렇게 쌓인 버전을

  1. 목록으로 보여 주고                 GET /api/sops/{doc_id}/versions
  2. 특정 번호의 버전 내용을 열어 주고     GET /api/sops/{doc_id}/versions/{version_no}
  3. 두 버전의 순서도 노드를 비교합니다    GET /api/sops/{doc_id}/versions/{a}/diff/{b}

여기서는 읽기만 합니다. 저장하거나 고치는 일은 sops.py 가 담당합니다.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection

from app.db import get_conn
from app.errors import ApiError
from app.refs import load_ref_docs
# "문서 있는지 확인(404)" 과 "살아 있는 잠금 찾기" 는 여러 라우터가 같이 쓰므로 common.py 에서 가져옵니다
from app.routers.common import content_with_current_sop_no, find_active_lock, get_document_or_404
from app.schemas import DocumentOpen, VersionSummary

router = APIRouter(tags=["versions"])


# ---------------------------------------------------------------------
# 1. 버전 목록
# ---------------------------------------------------------------------
@router.get("/sops/{doc_id}/versions", response_model=list[VersionSummary])
async def list_versions(doc_id: UUID, conn: AsyncConnection = Depends(get_conn)):
    """문서의 버전 목록을 최신 버전부터 돌려줍니다.
    content(문서 원본 JSON)는 매우 크기 때문에 일부러 읽지 않습니다."""
    await get_document_or_404(conn, doc_id)

    # 이 문서의 모든 버전을 번호가 큰 것(최신)부터 순서대로 가져온다. content 는 뺀다
    cur = await conn.execute(
        "SELECT version_no, revision, saved_by, saved_at, change_note "
        "FROM sop_versions WHERE document_id = %s ORDER BY version_no DESC",
        (doc_id,),
    )
    version_rows = await cur.fetchall()
    return version_rows


# ---------------------------------------------------------------------
# 2. 특정 버전 열기
# ---------------------------------------------------------------------
@router.get("/sops/{doc_id}/versions/{version_no}", response_model=DocumentOpen)
async def open_version(doc_id: UUID, version_no: int, conn: AsyncConnection = Depends(get_conn)):
    """특정 번호의 버전 내용(content)을 돌려줍니다. 최신 문서 열기(GET /sops/{doc_id})와 같은 모양입니다.
    예전 버전을 다시 열어 보거나 되돌릴 때 씁니다."""
    document = await get_document_or_404(conn, doc_id)

    # 이 문서에서 번호가 version_no 인 버전 한 줄을 내용까지 포함해 찾는다
    cur = await conn.execute(
        "SELECT id, version_no, content FROM sop_versions WHERE document_id = %s AND version_no = %s",
        (doc_id, version_no),
    )
    version = await cur.fetchone()
    if version is None:
        raise ApiError(404, "version_not_found", f"{version_no}번 버전이 없습니다.", version_no=version_no)

    lock = await find_active_lock(conn, doc_id)

    # content 안의 sop.id 는 문서의 "지금" 번호로 맞춰서 내려 줍니다 (번호 변경 뒤 옛 번호가 편집기에 들어가지 않게. DB 는 그대로)
    content = content_with_current_sop_no(version["content"], document["sop_no"])
    return DocumentOpen(
        id=document["id"],
        sop_no=document["sop_no"],
        version_no=version["version_no"],
        version_id=version["id"],
        lock=lock,
        content=content,
        # 그 버전의 SOP 상자들이 가리키는 문서들의 "지금" 번호·이름·상태 (편집기가 최신 번호를 그리는 데 씀)
        ref_docs=await load_ref_docs(conn, content),
    )


# ---------------------------------------------------------------------
# 3. 두 버전 비교(diff)
# ---------------------------------------------------------------------

# 두 버전의 노드를 비교할 때 "내용이 바뀌었다" 고 볼 항목들.
# position(상자 좌표)과 font_size(글자 크기)는 일부러 뺐습니다.
# 상자를 옮기거나 글자 크기만 바꾼 것은 절차 내용이 바뀐 게 아니기 때문입니다.
COMPARE_FIELDS = [
    "node_type",
    "name",
    "role_owner",
    "action",
    "description",
    "systems",
    "manual",
    "ref_sop_no",
    "ref_sop_name",
]


async def load_nodes_of_version(conn: AsyncConnection, doc_id: UUID, version_no: int) -> dict:
    """한 버전의 순서도 노드를 전부 읽어서
    { (instance_id, node_key): 노드 행 } 모양의 사전(dict)으로 돌려줍니다.
    두 버전을 비교하려면 "같은 순서도의 같은 노드" 를 빠르게 찾아야 해서 열쇠(key)를 이렇게 잡았습니다."""
    # 버전 번호로 그 버전의 id 를 찾는다 (flow_nodes 는 버전 id 로 연결되어 있다)
    cur = await conn.execute(
        "SELECT id FROM sop_versions WHERE document_id = %s AND version_no = %s",
        (doc_id, version_no),
    )
    version = await cur.fetchone()
    if version is None:
        raise ApiError(404, "version_not_found", f"{version_no}번 버전이 없습니다.", version_no=version_no)

    # 그 버전에 속한 순서도 노드를 전부 읽는다 (비교에 필요한 항목만)
    cur = await conn.execute(
        "SELECT instance_id, node_key, node_type, name, role_owner, action, description, "
        "systems, manual, ref_sop_no, ref_sop_name "
        "FROM flow_nodes WHERE version_id = %s ORDER BY instance_id, node_key",
        (version["id"],),
    )
    node_rows = await cur.fetchall()

    nodes_by_key = {}
    for node_row in node_rows:
        key = (node_row["instance_id"], node_row["node_key"])
        nodes_by_key[key] = node_row
    return nodes_by_key


def node_summary(node_row: dict) -> dict:
    """노드 한 행을 사람이 알아보기 쉬운 짧은 요약으로 줄입니다.
    추가/삭제된 노드 목록에 "어떤 노드가 생겼나/사라졌나" 를 보여 줄 때 씁니다."""
    return {
        "instance_id": node_row["instance_id"],
        "node_key": node_row["node_key"],
        "node_type": node_row["node_type"],
        "name": node_row["name"],
        "role_owner": node_row["role_owner"],
        "action": node_row["action"],
        "ref_sop_no": node_row["ref_sop_no"],
    }


@router.get("/sops/{doc_id}/versions/{a}/diff/{b}")
async def diff_versions(doc_id: UUID, a: int, b: int, conn: AsyncConnection = Depends(get_conn)):
    """a번 버전에서 b번 버전으로 가면서 순서도 노드가 어떻게 달라졌는지 돌려줍니다.
    "누가 어느 단계를 고쳤는지" 를 검토할 때 쓰는 기능입니다.

    응답 모양:
      {
        "a": 3, "b": 4,
        "added":   [ 노드요약, ... ],     # b 에는 있는데 a 에는 없던 노드 (새로 생김)
        "removed": [ 노드요약, ... ],     # a 에는 있었는데 b 에는 없는 노드 (지워짐)
        "changed": [ { "instance_id", "node_key", "node_type",
                       "fields": { "action": {"from": "이전 값", "to": "바뀐 값"}, ... } }, ... ]
      }
      노드요약 = { instance_id, node_key, node_type, name, role_owner, action, ref_sop_no }
    """
    await get_document_or_404(conn, doc_id)

    nodes_a = await load_nodes_of_version(conn, doc_id, a)
    nodes_b = await load_nodes_of_version(conn, doc_id, b)

    added = []
    removed = []
    changed = []

    # b 에만 있는 노드 = 새로 추가된 노드
    for key, node_b in nodes_b.items():
        if key not in nodes_a:
            added.append(node_summary(node_b))

    # a 에만 있는 노드 = 삭제된 노드
    for key, node_a in nodes_a.items():
        if key not in nodes_b:
            removed.append(node_summary(node_a))

    # 양쪽 다 있는 노드 = 항목별로 값이 달라졌는지 하나씩 비교
    for key, node_a in nodes_a.items():
        if key not in nodes_b:
            continue
        node_b = nodes_b[key]

        changed_fields = {}
        for field_name in COMPARE_FIELDS:
            if node_a[field_name] != node_b[field_name]:
                changed_fields[field_name] = {"from": node_a[field_name], "to": node_b[field_name]}

        if changed_fields:
            changed.append(
                {
                    "instance_id": node_b["instance_id"],
                    "node_key": node_b["node_key"],
                    "node_type": node_b["node_type"],
                    "fields": changed_fields,
                }
            )

    return {"a": a, "b": b, "added": added, "removed": removed, "changed": changed}
