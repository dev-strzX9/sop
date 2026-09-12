"""
refs.py — 순서도의 "SOP 상자"(다른 SOP 를 가리키는 노드)가 실제 문서와 어떻게 이어지는지 다루는 곳.

SOP 상자에는 세 가지 값이 있습니다.
  ref_document_id : 가리키는 문서의 id(UUID). 이것이 "진짜 연결". 번호가 바뀌어도 id 는 그대로라 끊기지 않습니다.
  sop_id          : 사람이 보는 SOP 번호 (표시용 캐시)
  sop_name        : 사람이 보는 SOP 이름 (표시용 캐시)

저장할 때마다 서버가 상자 하나하나를 아래 표대로 정리합니다 (resolve_references).
글자(sop_id/sop_name)는 항상 "지금 라이브러리에 있는 값" 으로 새로 써 넣어서, 다음에 문서를 열면 최신 번호·이름이 보입니다.

  ┌────┬───────────────────────────────┬──────────────────────────────────────────────────────────────┐
  │규칙│ 상자 상태                       │ 서버가 하는 일                                                 │
  ├────┼───────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ 1  │ ref_document_id 있고 문서 존재   │ sop_id/sop_name 을 그 문서의 현재 번호/이름으로 덮어씀 (연결됨).   │
  │    │                               │ 그 문서가 폐기(retired)면 경고 1건.                              │
  │ 2  │ ref_document_id 있는데 문서 없음 │ ref_document_id 를 null 로 바꾸고 글자는 그대로 둠. 경고 1건.        │
  │    │ (또는 UUID 형식이 아님)          │ 개수 통계: 번호 글자가 있으면 "미작성", 번호마저 없으면 "비어 있음"     │
  │    │                               │ (그 경우 규칙 4 의 합산 경고에도 같이 세어짐)                       │
  │ 3  │ ref_document_id 없음 + sop_id 있음│ 번호로 문서를 찾음. 있으면 id 를 채우고(승격) 이름도 덮어씀 (연결됨).  │
  │    │                               │ 없으면 그대로 둠 (미작성. 경고 없음 — 나중에 만들면 다음 저장 때 승격). │
  │ 4  │ 둘 다 없음                      │ 저장은 되고 "참조 대상이 비어 있는 SOP 상자 N개" 경고 1건 (합산).     │
  └────┴───────────────────────────────┴──────────────────────────────────────────────────────────────┘

  - 자기 자신을 가리키는 상자도 허용합니다.
  - DB 조회는 저장 1회당 최대 2번입니다: id 목록으로 한 번(IN), 번호 목록으로 한 번(IN). 상자마다 조회하지 않습니다.

그 밖에 "이 문서를 참조하는 다른 문서" 를 찾는 SQL(find_referenced_by)과
문서를 열 때 함께 주는 "참조 대상들의 현재 정보"(load_ref_docs)도 여기에 모아 두었습니다.
"""

from uuid import UUID

from psycopg import AsyncConnection

from app.derive import iter_sop_nodes, uuid_or_none
# 참조 처리 결과 개수(linked / pending / empty)는 응답 모양(schemas.RefsSummary)을 그대로 씁니다.
# 같은 모양의 상자를 여기서 또 만들면 "어느 것을 써야 하나" 헷갈리기 때문입니다.
#   linked  = 연결됨: 규칙 1, 규칙 3 성공
#   pending = 미작성: 번호만 있고 문서가 없는 상자 (규칙 3 실패, 그리고 규칙 2 중 번호 글자가 적혀 있던 것)
#   empty   = 비어 있음: 번호도 id 도 없는 상자 (규칙 4, 그리고 규칙 2 중 번호 글자도 없던 것)
from app.schemas import RefsSummary


async def _load_documents_by_ids(conn: AsyncConnection, ids: list[UUID]) -> dict:
    """문서 id 목록으로 문서를 한 번에 찾아 {id: 행} 으로 돌려줍니다. 목록이 비면 조회하지 않습니다."""
    if not ids:
        return {}
    cur = await conn.execute(
        "SELECT id, sop_no, name, status FROM sop_documents WHERE id = ANY(%s)", (ids,)
    )
    return {row["id"]: row for row in await cur.fetchall()}


async def _load_documents_by_sop_nos(conn: AsyncConnection, sop_nos: list[str]) -> dict:
    """SOP 번호 목록으로 문서를 한 번에 찾아 {sop_no: 행} 으로 돌려줍니다. 목록이 비면 조회하지 않습니다."""
    if not sop_nos:
        return {}
    cur = await conn.execute(
        "SELECT id, sop_no, name, status FROM sop_documents WHERE sop_no = ANY(%s)", (sop_nos,)
    )
    return {row["sop_no"]: row for row in await cur.fetchall()}


def _apply_document(node: dict, found: dict) -> None:
    """상자(node)에 찾은 문서의 현재 값을 써 넣습니다. (규칙 1, 3 공통)"""
    node["ref_document_id"] = str(found["id"])
    node["sop_id"] = found["sop_no"]
    node["sop_name"] = found["name"]


async def resolve_references(conn: AsyncConnection, doc: dict) -> tuple[RefsSummary, list[str]]:
    """
    문서(doc) 안의 모든 SOP 상자를 위 표의 규칙대로 정리합니다. doc 은 제자리에서 고쳐집니다(저장될 content 가 곧 이것).
    돌려주는 값: (개수 요약, 경고 목록)
    """
    summary = RefsSummary()
    warnings: list[str] = []

    # 1단계: 상자를 한 번 훑어 "id 로 찾을 것" 과 "번호로 찾을 것" 을 모읍니다. (조회는 그 다음에 한 번씩만)
    sop_nodes = list(iter_sop_nodes(doc))
    ids_to_find: list[UUID] = []
    sop_nos_to_find: list[str] = []
    for _instance_id, _node_key, node in sop_nodes:
        ref_id = uuid_or_none(node.get("ref_document_id"))
        sop_no = str(node.get("sop_id") or "").strip()
        if ref_id is not None:
            ids_to_find.append(ref_id)
        elif sop_no:
            sop_nos_to_find.append(sop_no)

    # 2단계: DB 조회 2번 (각각 목록이 비어 있으면 건너뜀)
    by_id = await _load_documents_by_ids(conn, list(set(ids_to_find)))
    by_no = await _load_documents_by_sop_nos(conn, list(set(sop_nos_to_find)))

    # 3단계: 상자마다 규칙 적용
    empty_count = 0
    for instance_id, node_key, node in sop_nodes:
        raw_ref = str(node.get("ref_document_id") or "").strip()
        ref_id = uuid_or_none(raw_ref)
        sop_no = str(node.get("sop_id") or "").strip()

        if ref_id is not None and ref_id in by_id:
            # 규칙 1: 연결됨 → 현재 번호·이름으로 덮어씀. 폐기된 문서면 경고.
            found = by_id[ref_id]
            _apply_document(node, found)
            summary.linked += 1
            if found["status"] == "retired":
                warnings.append(f"{instance_id}: SOP 상자 {node_key} 가 가리키는 {found['sop_no']} 은(는) 폐기된 문서입니다")

        elif raw_ref != "":
            # 규칙 2: id 가 적혀 있는데 그런 문서가 없거나 UUID 형식이 아님 → 연결을 풀고 글자는 유지. 경고.
            node["ref_document_id"] = None
            if sop_no:
                summary.pending += 1
            else:
                empty_count += 1
            warnings.append(
                f"{instance_id}: SOP 상자 {node_key} 가 가리키는 문서(id {raw_ref})를 찾을 수 없어 연결을 풀었습니다 (번호 글자는 그대로)"
            )

        elif sop_no:
            # 규칙 3: 번호만 있음 → 번호로 찾아 있으면 승격(연결), 없으면 미작성 그대로.
            found = by_no.get(sop_no)
            if found is not None:
                _apply_document(node, found)
                summary.linked += 1
                if found["status"] == "retired":
                    warnings.append(f"{instance_id}: SOP 상자 {node_key} 가 가리키는 {found['sop_no']} 은(는) 폐기된 문서입니다")
            else:
                summary.pending += 1   # 미작성: 상자는 손대지 않음 (나중에 그 번호의 문서가 생기면 다음 저장 때 승격)

        else:
            # 규칙 4: 둘 다 없음 → 비어 있는 상자. 손대지 않고 개수만 세어 두었다가 마지막에 경고 1건.
            empty_count += 1

    summary.empty = empty_count
    if empty_count:
        warnings.append(f"참조 대상이 비어 있는 SOP 상자 {empty_count}개")
    return summary, warnings


# ---------------------------------------------------------------------
# "이 문서를 참조하는 다른 문서" — DELETE / PATCH number / GET referenced-by 가 함께 씀
# ---------------------------------------------------------------------
async def find_referenced_by(conn: AsyncConnection, doc_id: UUID, sop_no: str) -> list[dict]:
    """
    이 문서를 "현재 버전" 에서 참조하는 다른 문서 목록을 돌려줍니다 (문서당 1건, AREA·번호 순).
    참조로 치는 것: 상자의 ref_document_id 가 이 문서이거나, id 없이 번호(ref_sop_no)만 이 문서의 번호와 같은 것.
    자기 자신은 뺍니다. 옛 버전의 상자는 보지 않습니다 (반드시 current_version_id 로 조인).
    """
    cur = await conn.execute(
        "SELECT d.id, d.sop_no, d.name, d.area, d.status, v.version_no "
        "  FROM flow_nodes n "
        "  JOIN sop_documents d ON d.current_version_id = n.version_id "
        "  JOIN sop_versions v ON v.id = d.current_version_id "
        " WHERE (n.ref_document_id = %s OR (n.ref_document_id IS NULL AND n.ref_sop_no = %s)) "
        "   AND d.id <> %s "
        " GROUP BY d.id, d.sop_no, d.name, d.area, d.status, v.version_no "
        " ORDER BY d.area, d.sop_no",
        (doc_id, sop_no, doc_id),
    )
    return await cur.fetchall()


async def count_referenced_by(conn: AsyncConnection, doc_id: UUID, sop_no: str) -> int:
    """find_referenced_by 의 개수 버전. 응답에 숫자만 넣을 때 씁니다."""
    return len(await find_referenced_by(conn, doc_id, sop_no))


async def count_number_only_references(conn: AsyncConnection, doc_id: UUID, sop_no: str) -> int:
    """
    "id 연결 없이 번호 글자(ref_sop_no)만으로" 이 번호를 가리키는 다른 문서 수 (현재 버전만, 자기 제외).
    번호 변경(PATCH /number) 때 옛 번호로 세어 두면, 새 번호를 자동으로 따라가지 못하는(미작성) 참조가 몇 건인지 알 수 있습니다.
    """
    cur = await conn.execute(
        "SELECT count(DISTINCT d.id) AS total "
        "  FROM flow_nodes n "
        "  JOIN sop_documents d ON d.current_version_id = n.version_id "
        " WHERE n.ref_document_id IS NULL AND n.ref_sop_no = %s AND d.id <> %s",
        (sop_no, doc_id),
    )
    return (await cur.fetchone())["total"]


# ---------------------------------------------------------------------
# 문서를 열 때: 참조 대상들의 "현재" 정보
# ---------------------------------------------------------------------
async def load_ref_docs(conn: AsyncConnection, content: dict) -> list[dict]:
    """
    한 버전의 content 안 SOP 상자들이 가리키는(ref_document_id) 문서들의 지금 번호·이름·상태를 돌려줍니다.
    폐기된 문서도 포함합니다 (편집기가 "폐기된 문서를 가리킴" 을 표시하려면 알아야 하니까). 없으면 [].
    """
    ids = []
    for _instance_id, _node_key, node in iter_sop_nodes(content):
        ref_id = uuid_or_none(node.get("ref_document_id"))
        if ref_id is not None and ref_id not in ids:
            ids.append(ref_id)
    if not ids:
        return []
    cur = await conn.execute(
        "SELECT id, sop_no, name, area, status FROM sop_documents WHERE id = ANY(%s) ORDER BY area, sop_no",
        (ids,),
    )
    return await cur.fetchall()
