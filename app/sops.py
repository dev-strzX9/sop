"""
sops.py — SOP 문서의 "목록 / 열기 / 저장 / 번호 변경 / 폐기 / 참조 관계" API.

이 파일이 하는 일: 편집기(브라우저 HTML)가 라이브러리 사이드바를 그리거나, 문서를 열거나, 저장 버튼을 누를 때
부르는 주소들을 모아 둔 곳입니다. main.py 가 앞에 /api 를 붙여 주므로 여기서는 "/sops..." 만 적습니다.

  GET    /sops                        문서 목록 (라이브러리 트리용. 내용은 빼고 번호·이름만)
  GET    /sops/by-no/{sop_no}         SOP 번호로 최신 버전 열기
  GET    /sops/{doc_id}               문서 id 로 최신 버전 열기
  POST   /sops                        새 문서 만들기 (버전 1). 번호가 이미 있으면 409 sop_no_taken
  PUT    /sops/{doc_id}               기존 문서에 새 버전 한 줄 추가 (폐기된 문서였으면 draft 로 되살림)
  PATCH  /sops/{doc_id}/number        SOP 번호 바꾸기 (버전은 손대지 않음)
  DELETE /sops/{doc_id}               폐기 (행을 지우지 않고 status 만 'retired' 로)
  GET    /sops/{doc_id}/referenced-by 이 문서를 참조하는 다른 문서 목록

용어: 문서(sop_documents) = SOP 한 건 (id 는 영구 번호(UUID), sop_no 는 사람이 보는 번호 — 바뀔 수 있음).
      버전(sop_versions) = 저장 1회 = 1행 (content 컬럼에 편집기 JSON 통째).
      파생 행(flow_nodes / flow_edges) = 저장 때 서버가 순서도에서 뽑아 낸 "검색용 사본".

왜 저장 주소가 "번호" 가 아니라 "id" 인가?
      번호는 사람이 바꿀 수 있어서(오타 수정, 체계 변경) 번호로 저장하면 "새 문서인지, 번호 바꾼 문서인지" 를 구분할 수
      없습니다. id 는 절대 안 바뀌므로 id 로 저장하고, 번호 변경은 PATCH /number 라는 별도 문으로 받습니다.

열기 응답의 번호 보정
      PATCH /number 는 버전(content)을 손대지 않으므로, 번호를 바꾼 뒤에는 content 안의 sop.id(옛 번호)와
      문서의 sop_no(새 번호)가 다릅니다. 열기 응답을 만들 때만 content.sop.id 를 지금 sop_no 로 맞춰서 내려 줍니다
      (DB 는 그대로). 그래야 편집기가 옛 번호를 입력칸에 채워 넣고 "번호를 되돌릴까요?" 라고 묻는 일이 없습니다.
"""

import logging
import re
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.deps import current_user
from app.derive import SOP_NO_PATTERN, derive_flow_rows, document_meta, validate_document
from app.errors import ApiError
from app.refs import (
    count_number_only_references,
    count_referenced_by,
    find_referenced_by,
    load_ref_docs,
    resolve_references,
)
from app.routers.common import content_with_current_sop_no, find_active_lock, get_document_or_404
from app.schemas import (
    DocumentOpen,
    DocumentSummary,
    ReferencedBy,
    RenameRequest,
    RenameResponse,
    SaveRequest,
    SaveResponse,
    StatusResponse,
    to_utc_z,
)

router = APIRouter(tags=["sops"])
log = logging.getLogger("sop")

# 폐기됐던 문서에 저장하면 draft 로 되살리면서 이 문구를 warnings 에 넣습니다 (프론트가 토스트로 보여 줌)
REVIVED_WARNING = "폐기됐던 문서를 다시 살렸습니다 (draft)"


# ----- 공용 헬퍼 (여러 엔드포인트가 같이 쓰는 작은 함수) -----------------------------------
async def load_document_open(conn: AsyncConnection, doc_row: dict | None) -> DocumentOpen:
    """
    문서 행(doc_row: SELECT id, sop_no, current_version_id 결과 한 줄, 못 찾았으면 None)을 받아 "열기" 응답을 완성합니다.
    id 로 찾든 sop_no 로 찾든 그 뒤 할 일(최신 버전 읽기, 잠금 확인, 참조 대상 정보)은 같아서 한 곳에 모았습니다.
    """
    # 문서가 없거나, 저장된 버전이 아직 하나도 없으면(current_version_id 가 비어 있으면) 열 수 없습니다.
    if doc_row is None or doc_row["current_version_id"] is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")

    # 현재 버전(최신 저장본)의 번호와 내용(content)을 읽는다
    cur = await conn.execute(
        "SELECT id, version_no, content FROM sop_versions WHERE id = %s",
        (doc_row["current_version_id"],),
    )
    version_row = await cur.fetchone()
    if version_row is None:
        raise ApiError(404, "not_found", "문서의 현재 버전을 찾을 수 없습니다.")

    # jsonb 는 psycopg 가 알아서 dict 로 바꿔 줍니다. sop.id 는 지금 번호로 보정해서 내려 줍니다 (DB 는 그대로).
    content = content_with_current_sop_no(version_row["content"], doc_row["sop_no"])
    return DocumentOpen(
        id=doc_row["id"],
        sop_no=doc_row["sop_no"],
        version_no=version_row["version_no"],
        version_id=version_row["id"],
        lock=await find_active_lock(conn, doc_row["id"]),
        content=content,
        ref_docs=await load_ref_docs(conn, content),
    )


def escape_like(text: str) -> str:
    """
    ILIKE 검색어에서 특수 글자를 "그냥 글자" 로 만듭니다.
    ILIKE 에서 % 는 "아무 글자 여러 개", _ 는 "아무 글자 한 개" 라는 뜻이라, 사용자가 SOP_ETCH 라고 치면
    SOP-ETCH 도 걸립니다. 앞에 \\ 를 붙이면 글자 그대로 찾습니다. (\\ 자체도 먼저 \\\\ 로)
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ----- 1. 문서 목록 --------------------------------------------------------------------
@router.get("/sops", response_model=list[DocumentSummary])
async def list_documents(
    status: str = "!retired", q: str = "", area: str = "", conn: AsyncConnection = Depends(get_conn)
):
    """
    문서 목록을 돌려줍니다. 라이브러리 트리(사이드바)를 그릴 때 프론트가 호출합니다.
    status: "all"=전부, "!retired"=폐기 제외(기본), 그 외=그 상태만.  q: 번호/이름 부분 검색.  area: AREA(P/E/D/T/C)로 거르기.
    """
    # WHERE 조건은 "고정된 문장 조각"만 리스트에 넣고, 사용자가 입력한 값은 전부 params 로 따로 넘깁니다.
    # 사용자 입력이 SQL 문장 안에 직접 섞이면 위험(SQL 인젝션)하기 때문입니다.
    status = status.strip() or "!retired"   # ?status= 처럼 비워서 보내도 기본값(폐기 제외)으로
    conditions = []
    params = []

    if status == "all":
        pass  # 상태 조건 없음
    elif status.startswith("!"):
        conditions.append("d.status <> %s")
        params.append(status[1:])
    else:
        conditions.append("d.status = %s")
        params.append(status)

    if q:
        conditions.append("(d.sop_no ILIKE %s OR d.name ILIKE %s)")
        like_pattern = "%" + escape_like(q) + "%"  # ILIKE = 대소문자 구분 없는 "포함" 검색
        params.extend([like_pattern, like_pattern])  # %s 가 두 번이라 값도 두 번

    if area:
        conditions.append("d.area = %s")
        params.append(area)

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)
    # 문서 목록을 읽되, 현재 버전 행을 LEFT JOIN 해서 버전 번호·개정·작성자를 함께 가져온다.
    # content 컬럼은 문서당 수백 KB(이미지 포함)라 목록에서는 절대 읽지 않는다 (느려지고 메모리만 낭비).
    # LEFT JOIN 인 이유: 아직 버전이 없는 문서도 목록에는 나오게 하려고.
    # COALESCE(a, b) = a 가 비어 있으면(NULL) b. 버전이 없는 문서의 revision/owner 를 '' 로 채운다.
    sql = (
        "SELECT d.id, d.sop_no, d.name, d.area, d.status, d.updated_at, "
        "       v.version_no, COALESCE(v.revision, '') AS revision, COALESCE(v.owner, '') AS owner "
        "  FROM sop_documents d LEFT JOIN sop_versions v ON v.id = d.current_version_id "
        + where_sql + " ORDER BY d.area, d.sop_no"
    )
    cur = await conn.execute(sql, params)
    # 행(dict) 목록을 그대로 돌려주면 response_model(DocumentSummary) 이 모양을 맞춰 준다
    return await cur.fetchall()


# ----- 2. SOP 번호로 열기 (※ "/sops/{doc_id}" 보다 위에 있어야 by-no 가 doc_id 로 오해되지 않음) -----
@router.get("/sops/by-no/{sop_no}", response_model=DocumentOpen)
async def open_document_by_no(sop_no: str, conn: AsyncConnection = Depends(get_conn)):
    """SOP 번호(예: SOP-ETCH-001)로 최신 버전을 엽니다. 트리는 항목을 sop_no 로 식별하므로 문서를 클릭할 때 프론트가 호출합니다."""
    # SOP 번호로 문서 행을 찾는다
    cur = await conn.execute(
        "SELECT id, sop_no, current_version_id FROM sop_documents WHERE sop_no = %s", (sop_no,)
    )
    document_row = await cur.fetchone()   # 없으면 None → load_document_open 이 404
    return await load_document_open(conn, document_row)


# ----- 3. 문서 id 로 열기 ---------------------------------------------------------------
@router.get("/sops/{doc_id}", response_model=DocumentOpen)
async def open_document(doc_id: UUID, conn: AsyncConnection = Depends(get_conn)):
    """문서 id(uuid)로 최신 버전을 엽니다. id 를 이미 알 때(예: 저장 응답을 받은 뒤 다시 불러올 때) 프론트가 호출합니다."""
    # 문서 id 로 문서 행을 찾는다
    cur = await conn.execute(
        "SELECT id, sop_no, current_version_id FROM sop_documents WHERE id = %s", (doc_id,)
    )
    document_row = await cur.fetchone()   # 없으면 None → load_document_open 이 404
    return await load_document_open(conn, document_row)


# ----- 4. 저장 (POST = 새 문서, PUT = 새 버전). 둘 다 아래 _append_version 을 씁니다 ----------------
async def _append_version(
    conn: AsyncConnection, doc_id: UUID, body: SaveRequest, user: str, check_base_version: bool
) -> SaveResponse:
    """
    문서(doc_id)에 새 버전 한 줄을 추가하는 "저장의 본체". POST(새 문서)와 PUT(기존 문서)이 똑같이 씁니다.
    반드시 트랜잭션(conn.transaction()) 안에서 불러야 합니다 — 중간에 409 등을 던지면 전부 취소되게.
    user 는 "이번 저장의 사용자 이름" 으로, 부르는 쪽(create_document / save_document)이 한 번만 정해서 넘깁니다.

    순서
      a. 문서 행을 잠근다(FOR UPDATE) → 같은 문서를 동시에 저장하려는 다른 요청은 여기서 줄을 선다
      b. 잠근 상태에서 다시 검사: 번호 일치(PUT 만, 400) / base_version_no (PUT 만, 409)
      c. 순서도의 SOP 상자 참조를 정리한다 (refs.resolve_references — doc 이 제자리에서 갱신됨)
      d. 정리된 doc 에서 노드/연결선 행을 뽑는다
      e. 버전 행 + 노드/연결선 행을 넣고, 문서의 "현재 버전" 포인터를 옮기고, 이름/AREA 를 갱신한다.
         폐기(retired)됐던 문서면 draft 로 되살리고 경고 한 줄을 붙인다
    """
    doc = body.doc
    meta = document_meta(doc)
    saved_by = user

    # a. 문서 행을 잠근다. (PostgreSQL 은 MAX 같은 집계함수와 FOR UPDATE 를 한 문장에 같이 쓸 수 없어서,
    #    문서 행을 먼저 잠근 뒤 최대 버전 번호는 따로 구합니다)
    cur = await conn.execute("SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
    doc_row = await cur.fetchone()
    if doc_row is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
    sop_no = doc_row["sop_no"]

    # b-1. (PUT 만) 문서 안의 번호가 저장된 번호와 다르면 400. save_document 가 트랜잭션 밖에서 한 번 검사하지만,
    #      그 사이 다른 요청의 PATCH /number 가 번호를 바꿨을 수 있어 잠금 아래에서 한 번 더 확인합니다.
    if check_base_version and meta.sop_no != sop_no:
        raise ApiError(
            400, "sop_no_mismatch",
            f"저장된 SOP 번호({sop_no})와 문서 안의 번호({meta.sop_no})가 다릅니다.",
            stored_sop_no=sop_no, doc_sop_no=meta.sop_no,
            hint="번호 변경은 PATCH /api/sops/{id}/number 를 쓰세요",
        )

    # 이 문서의 가장 큰 버전 번호를 찾는다 (버전이 하나도 없으면 0)
    cur = await conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS max_no FROM sop_versions WHERE document_id = %s", (doc_id,)
    )
    max_no = (await cur.fetchone())["max_no"]

    # b-2. 내가 열었던 버전(base_version_no)과 지금 최신 버전이 다르면 → 그 사이 누군가 먼저 저장한 것.
    #      base_version_no 가 None 이면(강제 저장) 이 검사를 건너뜁니다. POST 는 아예 검사하지 않습니다.
    if check_base_version and body.base_version_no is not None and body.base_version_no != max_no:
        raise ApiError(
            409, "version_conflict",
            f"다른 사람이 먼저 v{max_no} 을(를) 저장했습니다. 다시 불러온 뒤 저장하세요.",
            current_version_no=max_no,
        )
    new_version_no = max_no + 1

    # c. SOP 상자 참조 정리 (doc 의 sop_id / sop_name / ref_document_id 가 최신값으로 바뀐다)
    refs_summary, ref_warnings = await resolve_references(conn, doc)

    # d. 순서도에서 노드/연결선 행을 뽑아 낸다. 이상한 노드는 건너뛰고 warnings 에 남는다.
    node_rows, edge_rows, row_warnings = derive_flow_rows(doc)
    warnings = meta.warnings + ref_warnings + row_warnings

    # e. 새 버전 행을 추가한다. content 에는 (참조가 정리된) 편집기 JSON 을 통째로(Jsonb 로 감싸서) 넣는다.
    cur = await conn.execute(
        "INSERT INTO sop_versions (document_id, version_no, format, format_version, content, "
        "                          revision, owner, tags, change_note, saved_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, saved_at",
        (doc_id, new_version_no, meta.format, meta.format_version, Jsonb(doc),
         meta.revision, meta.owner, meta.tags, body.change_note, saved_by),
    )
    version_row = await cur.fetchone()
    version_id = version_row["id"]

    # 순서도 노드 행들을 한꺼번에 넣는다 (executemany = 같은 INSERT 를 여러 값으로 반복). 0개면 건너뜀.
    node_params = []
    for node in node_rows:
        node_params.append((
            version_id, node["instance_id"], node["node_key"], node["node_type"], node["name"],
            node["role_owner"], node["action"], node["description"], Jsonb(node["systems"]), Jsonb(node["manual"]),
            node["ref_sop_no"], node["ref_sop_name"], node["ref_document_id"], Jsonb(node["position"]), node["font_size"],
        ))
    if node_params:
        # executemany 는 커서(cursor)라는 도구를 통해서만 쓸 수 있어서 잠깐 하나 만들어 씁니다.
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO flow_nodes (version_id, instance_id, node_key, node_type, name, role_owner, action, "
                "  description, systems, manual, ref_sop_no, ref_sop_name, ref_document_id, position, font_size) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                node_params,
            )

    # 순서도 연결선 행들을 한꺼번에 넣는다. 0개면 건너뜀.
    edge_params = []
    for edge in edge_rows:
        edge_params.append((
            version_id, edge["instance_id"], edge["edge_key"], edge["source_key"], edge["target_key"],
            edge["source_port"], edge["target_port"], edge["condition"], edge["line_type"], Jsonb(edge["route"]),
        ))
    if edge_params:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO flow_edges (version_id, instance_id, edge_key, source_key, target_key, "
                "  source_port, target_port, condition, line_type, route) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                edge_params,
            )

    # 폐기(retired)됐던 문서에 저장하면 draft 로 되살린다. (그대로 두면 201 인데 트리에 영영 안 보이는 상태가 됨)
    # 다른 상태(draft/review/approved)는 그대로 둔다.
    new_status = doc_row["status"]
    if new_status == "retired":
        new_status = "draft"
        warnings.append(REVIVED_WARNING)

    # 문서의 "현재 버전" 포인터를 방금 만든 버전으로 옮기고, 이름/AREA/상태도 맞춘다
    await conn.execute(
        "UPDATE sop_documents SET current_version_id = %s, name = %s, area = %s, status = %s, updated_at = now() "
        "WHERE id = %s",
        (version_id, meta.name, meta.area, new_status, doc_id),
    )

    log.info(
        "saved %s v%s nodes=%s edges=%s refs=%s/%s/%s status=%s by %s",
        sop_no, new_version_no, len(node_rows), len(edge_rows),
        refs_summary.linked, refs_summary.pending, refs_summary.empty, new_status, saved_by,
    )
    return SaveResponse(
        id=doc_id, sop_no=sop_no, version_id=version_id, version_no=new_version_no,
        saved_at=version_row["saved_at"], warnings=warnings, refs=refs_summary,
    )


async def _raise_if_sop_no_taken(conn: AsyncConnection, sop_no: str) -> None:
    """같은 번호의 문서가 이미 있으면 409 sop_no_taken 을 던집니다 (그 문서의 id 와 번호를 함께). 없으면 아무 일도 없음."""
    cur = await conn.execute("SELECT id, sop_no FROM sop_documents WHERE sop_no = %s", (sop_no,))
    existing = await cur.fetchone()
    if existing is not None:
        raise ApiError(
            409, "sop_no_taken",
            f"SOP 번호 {existing['sop_no']} 은(는) 이미 라이브러리에 있습니다. "
            f"그 문서의 새 버전으로 저장하려면 PUT /api/sops/{existing['id']} 를 쓰세요.",
            existing_id=str(existing["id"]), existing_sop_no=existing["sop_no"],
        )


async def _other_users_lock_warning(conn: AsyncConnection, doc_id: UUID, saved_by: str) -> str | None:
    """나 말고 다른 사람의 만료되지 않은 잠금이 있으면 안내 문구를, 없으면 None 을 돌려줍니다. (저장 자체는 막지 않음)"""
    cur = await conn.execute(
        "SELECT locked_by, expires_at FROM sop_edit_locks WHERE document_id = %s AND locked_by <> %s AND expires_at > now()",
        (doc_id, saved_by),
    )
    lock_row = await cur.fetchone()
    if lock_row is None:
        return None
    return f"{lock_row['locked_by']} 님이 이 문서를 편집 중입니다 (잠금 만료: {to_utc_z(lock_row['expires_at'])})"


@router.post("/sops", response_model=SaveResponse, status_code=201)
async def create_document(
    body: SaveRequest, user: str = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
):
    """
    새 문서를 만들고 버전 1 을 저장합니다. 편집기에서 "아직 라이브러리에 없는" 문서를 처음 저장할 때 프론트가 호출합니다.
    문서 안의 SOP 번호(doc.sop.id)가 이미 있으면 409 sop_no_taken 과 함께 그 문서의 id 를 알려 줍니다
    (프론트는 "그 문서의 새 버전으로 저장할까요?" 라고 물은 뒤 PUT /sops/{existing_id} 로 다시 보냅니다).
    base_version_no 는 무시합니다 (새 문서라 비교할 이전 버전이 없으니까).
    ※ 요청 크기 20MB 제한(413)은 main.py 의 미들웨어가 이미 처리하므로 여기서는 검사하지 않습니다.
    """
    # a. 문서 JSON 형식 검사 (이상하면 derive.validate_document 가 422 를 던집니다). 번호가 비어 있어도 여기서 422.
    validate_document(body.doc)
    meta = document_meta(body.doc)
    # 한 요청 안에서 사용자 이름은 한 번만 정한다: 본문 saved_by 가 있으면 그것, 없으면 헤더 값.
    # (created_by / saved_by / 임시본 삭제 / 잠금 경고가 전부 같은 이름을 쓰게)
    who = body.saved_by.strip() or user

    # b. 같은 번호의 문서가 이미 있으면 409. (동시에 두 요청이 들어오는 드문 경우는 아래 UniqueViolation 으로 한 번 더 막습니다)
    await _raise_if_sop_no_taken(conn, meta.sop_no)

    # c. 여기부터 DB 작업을 한 묶음(트랜잭션)으로 처리합니다. 블록 안에서 오류(raise)가 나면 그때까지 한 작업이
    #    전부 자동 취소(롤백)되므로, 반쯤 저장된 찌꺼기가 남지 않습니다.
    try:
        async with conn.transaction():
            # 문서 행을 만든다. created_by 는 처음 만들 때만 들어갑니다.
            cur = await conn.execute(
                "INSERT INTO sop_documents (sop_no, name, area, created_by) VALUES (%s, %s, %s, %s) RETURNING id",
                (meta.sop_no, meta.name, meta.area, who),
            )
            doc_id = (await cur.fetchone())["id"]
            response = await _append_version(conn, doc_id, body, who, check_base_version=False)
    except psycopg.errors.UniqueViolation:
        # 위 b 검사와 INSERT 사이에 다른 요청이 같은 번호를 먼저 만든 경우 (트랜잭션은 이미 취소됨)
        await _raise_if_sop_no_taken(conn, meta.sop_no)
        raise

    # d. (트랜잭션 밖) 다른 사람의 잠금이 있으면 안내 문구를 함께 돌려준다. 새 문서라 보통은 없다.
    response.warning = await _other_users_lock_warning(conn, doc_id, who)
    return response


@router.put("/sops/{doc_id}", response_model=SaveResponse, status_code=201)
async def save_document(
    doc_id: UUID, body: SaveRequest, user: str = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
):
    """
    기존 문서에 새 버전을 추가합니다. 편집기에서 "라이브러리에 저장" 을 누를 때(열어 둔 문서의 id 로) 프론트가 호출합니다.
    기존 버전을 고치지 않고 항상 새 버전 행을 하나 추가합니다 (이력 보존).
      - 문서가 없으면 404 not_found
      - base_version_no 가 지금 최신과 다르면 409 version_conflict (다른 사람이 먼저 저장함)
      - 문서 안의 번호(doc.sop.id)가 저장된 번호와 다르면 400 sop_no_mismatch → 번호를 바꾸려면 PATCH /number 를 먼저 쓰세요
      - 폐기(retired)된 문서였으면 draft 로 되살리고 warnings 에 안내 문구를 넣습니다
    """
    # a. 문서 JSON 형식 검사
    validate_document(body.doc)
    meta = document_meta(body.doc)
    who = body.saved_by.strip() or user   # 사용자 이름은 여기서 한 번만 정한다

    # b. 문서가 있는지, 번호가 맞는지 먼저 확인 (빠른 실패. 잠금 아래에서 _append_version 이 한 번 더 확인합니다)
    doc_row = await get_document_or_404(conn, doc_id)
    if meta.sop_no != doc_row["sop_no"]:
        raise ApiError(
            400, "sop_no_mismatch",
            f"저장된 SOP 번호({doc_row['sop_no']})와 문서 안의 번호({meta.sop_no})가 다릅니다.",
            stored_sop_no=doc_row["sop_no"], doc_sop_no=meta.sop_no,
            hint="번호 변경은 PATCH /api/sops/{id}/number 를 쓰세요",
        )

    # c. 저장 본체 (트랜잭션 안에서. 400/409 가 나면 전부 취소)
    async with conn.transaction():
        response = await _append_version(conn, doc_id, body, who, check_base_version=True)

    # d. (트랜잭션 밖) 다른 사람의 잠금이 있으면 안내 문구를 함께 돌려준다.
    response.warning = await _other_users_lock_warning(conn, doc_id, who)
    return response


# ----- 5. 번호 변경 (버전은 손대지 않음) --------------------------------------------------
@router.patch("/sops/{doc_id}/number", response_model=RenameResponse)
async def rename_document(
    doc_id: UUID, body: RenameRequest, user: str = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
):
    """
    문서의 SOP 번호만 바꿉니다. 편집기에서 번호 입력칸을 고친 뒤 저장할 때 프론트가 (저장 전에) 호출합니다.
    sop_documents.sop_no 와 updated_at 만 바뀌고 버전(sop_versions)은 절대 손대지 않습니다.
    id 로 연결된(ref_document_id) 다른 문서의 SOP 상자는 다음에 열거나 저장할 때 자동으로 새 번호를 보게 됩니다.
      - 다른 사용자가 잠금 중이면 423 locked (본인 잠금은 허용)
      - 번호 형식이 틀리면 422 invalid_document
      - 이미 다른 문서가 쓰는 번호면 409 sop_no_taken
      - 같은 번호면 아무것도 바꾸지 않고 200
    응답의 referenced_by 는 "새 번호 기준" 으로 센 수 = id 로 연결된 참조 (+ 새 번호를 글자로 적어 둔 참조).
    옛 번호를 글자로만 적어 두었던(미작성) 상자는 새 번호를 따라가지 못하므로 여기 들어가지 않고, 로그에만 남깁니다.
    """
    who = body.user.strip() or user
    new_sop_no = body.sop_no.strip()
    if new_sop_no == "" or re.match(SOP_NO_PATTERN, new_sop_no) is None:
        raise ApiError(422, "invalid_document", f"SOP 번호 '{new_sop_no}' 는 비어 있거나 허용되지 않는 글자가 있습니다. (영문·숫자·- _ . 만 가능)")

    try:
        async with conn.transaction():
            # 문서 행을 잠그고(FOR UPDATE) 읽는다. 없으면 404.
            cur = await conn.execute("SELECT id, sop_no FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
            doc_row = await cur.fetchone()
            if doc_row is None:
                raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
            old_sop_no = doc_row["sop_no"]

            # 다른 사람이 편집 중(만료되지 않은 남의 잠금)이면 번호를 바꿀 수 없다
            cur = await conn.execute(
                "SELECT locked_by, expires_at FROM sop_edit_locks "
                "WHERE document_id = %s AND locked_by <> %s AND expires_at > now()",
                (doc_id, who),
            )
            lock_row = await cur.fetchone()
            if lock_row is not None:
                raise ApiError(
                    423, "locked",
                    f"{lock_row['locked_by']} 님이 편집 중이라 번호를 바꿀 수 없습니다.",
                    locked_by=lock_row["locked_by"], expires_at=to_utc_z(lock_row["expires_at"]),
                )

            pending_old = 0
            if new_sop_no != old_sop_no:
                # 다른 문서가 이미 그 번호를 쓰고 있으면 409
                cur = await conn.execute("SELECT id FROM sop_documents WHERE sop_no = %s", (new_sop_no,))
                taken = await cur.fetchone()
                if taken is not None:
                    raise ApiError(
                        409, "sop_no_taken", f"SOP 번호 {new_sop_no} 은(는) 이미 다른 문서가 쓰고 있습니다.",
                        existing_id=str(taken["id"]),
                    )
                # 옛 번호를 "글자로만" 가리키던 문서 수 (번호가 바뀌면 이들은 자동으로 따라오지 못한다 → 로그로 남김)
                pending_old = await count_number_only_references(conn, doc_id, old_sop_no)
                # 번호와 갱신 시각만 바꾼다 (버전 행은 그대로)
                await conn.execute(
                    "UPDATE sop_documents SET sop_no = %s, updated_at = now() WHERE id = %s", (new_sop_no, doc_id)
                )
    except psycopg.errors.UniqueViolation:
        # 위 검사와 UPDATE 사이에 다른 요청이 같은 번호를 먼저 차지한 드문 경우
        cur = await conn.execute("SELECT id FROM sop_documents WHERE sop_no = %s", (new_sop_no,))
        taken = await cur.fetchone()
        raise ApiError(
            409, "sop_no_taken", f"SOP 번호 {new_sop_no} 은(는) 이미 다른 문서가 쓰고 있습니다.",
            existing_id=str(taken["id"]) if taken else None,
        )

    referenced_by = await count_referenced_by(conn, doc_id, new_sop_no)
    if new_sop_no != old_sop_no:
        log.info(
            "renamed %s -> %s (referenced_by=%s, 옛 번호를 글자로만 참조하던 문서=%s) by %s",
            old_sop_no, new_sop_no, referenced_by, pending_old, who,
        )
    return RenameResponse(id=doc_id, sop_no=new_sop_no, old_sop_no=old_sop_no, referenced_by=referenced_by)


# ----- 6. 폐기 (삭제 대신 status 만 바꿈 — SOP 는 이력 보존이 원칙) -----------------------------
@router.delete("/sops/{doc_id}", response_model=StatusResponse)
async def retire_document(doc_id: UUID, conn: AsyncConnection = Depends(get_conn)):
    """
    문서를 폐기 상태로 바꿉니다 (행은 남겨 둡니다 — 이 문서에 다시 저장하면 draft 로 되살아납니다). 트리에서 "삭제" 를 누를 때 프론트가 호출합니다.
    응답의 referenced_by 는 "이 문서를 참조하는 다른 문서 수" — 0 이 아니면 프론트가 주의를 줄 수 있습니다.
    """
    # 문서 상태를 'retired' 로 바꾸고, 바뀐 문서의 id 와 번호를 돌려받는다 (없는 문서면 결과 없음)
    cur = await conn.execute(
        "UPDATE sop_documents SET status = 'retired', updated_at = now() WHERE id = %s RETURNING id, sop_no", (doc_id,)
    )
    row = await cur.fetchone()
    if row is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
    referenced_by = await count_referenced_by(conn, row["id"], row["sop_no"])
    return StatusResponse(id=row["id"], status="retired", referenced_by=referenced_by)


# ----- 8. 이 문서를 참조하는 다른 문서 --------------------------------------------------------
@router.get("/sops/{doc_id}/referenced-by", response_model=list[ReferencedBy])
async def list_referenced_by(doc_id: UUID, conn: AsyncConnection = Depends(get_conn)):
    """
    이 문서를 "현재 버전" 의 순서도에서 참조하는 다른 문서 목록을 돌려줍니다 (자기 자신 제외, 문서당 1건).
    id 로 연결된 참조와, 번호만 적혀 있는(아직 연결 안 된) 참조를 모두 셉니다.
    라이브러리 패널의 "이 문서를 참조하는 문서 N건" 표시에 프론트가 씁니다.
    """
    doc_row = await get_document_or_404(conn, doc_id)
    rows = await find_referenced_by(conn, doc_row["id"], doc_row["sop_no"])
    return [ReferencedBy(**row) for row in rows]
