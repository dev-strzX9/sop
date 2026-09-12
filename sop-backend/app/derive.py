"""
derive.py — 편집기가 보낸 문서 JSON(doc)에서 "DB 컬럼에 넣을 값"만 골라내는 곳.

편집기(브라우저 HTML)는 문서 전체를 커다란 JSON 하나로 보냅니다. 서버는 그 JSON을 통째로
보관하지만(sop_versions.content), 검색과 목록 표시를 위해 몇 가지 값은 따로 컬럼에 복사해 둡니다.
이 파일은 그 "골라내기"만 담당하는 순수 함수(DB나 웹 프레임워크 없이 입력 → 출력만 하는 함수) 모음입니다.

여기서 하는 일 네 가지
  1. validate_document(doc) : 문서가 최소한의 형식을 갖췄는지 검사. 아니면 422 오류.
  2. document_meta(doc)     : 문서 번호·이름·AREA·개정번호·작성자·태그를 꺼낸다.
  3. derive_flow_rows(doc)  : 순서도 노드/연결선을 flow_nodes / flow_edges 행 모양으로 풀어낸다.
                              (sop 노드의 ref_document_id 는 UUID 형식만 인정, 아니면 None + 경고)
  4. iter_sop_nodes(doc)    : 순서도의 sop 상자만 골라 돌려준다. (참조 해석은 refs.py 가 DB 를 보고 함)

프론트의 JSON 형식이 바뀌면 이 파일만 고치면 되도록, JSON 키 이름은 여기에만 적습니다.
"""

import math
import re
from dataclasses import dataclass, field
from uuid import UUID

from app.errors import ApiError

SOP_DOC_FORMAT = "sop-editor-mock"                      # 편집기 문서의 format 값. 다르면 우리 문서가 아님
VALID_AREAS = ("", "P", "E", "D", "T", "C")             # 적용 AREA 허용값. '' 는 미지정
NODE_TYPES = ("start", "seq", "decision", "sop", "end")  # 노드 종류. DB 의 CHECK 제약과 같아야 함
SOP_NO_PATTERN = r"^[A-Za-z0-9._-]+$"                   # SOP 번호 허용 글자: 영문·숫자·점·밑줄·붙임표

# DB 의 int(4바이트 정수) 칸에 들어갈 수 있는 범위. 밖이면 PostgreSQL 이 "integer out of range" 로 거부하므로 미리 거릅니다.
INT4_MIN = -2_147_483_648
INT4_MAX = 2_147_483_647


# @dataclass: "이름: 타입" 만 나열하면 값을 담는 상자(클래스)를 파이썬이 만들어 줍니다.
@dataclass
class DocumentMeta:
    """문서 JSON 에서 꺼낸 메타 정보(문서를 설명하는 값들). 컬럼 이름과 같게 맞췄습니다."""

    sop_no: str            # sop.id        → sop_documents.sop_no
    name: str              # sop.name      → sop_documents.name
    area: str              # studio.area   → sop_documents.area
    revision: str          # studio.revision → sop_versions.revision
    owner: str             # studio.owner  → sop_versions.owner
    tags: list[str]        # studio.tags   → sop_versions.tags
    format: str            # format        → sop_versions.format
    format_version: int    # version       → sop_versions.format_version
    # default_factory=list: 안 주면 빈 목록으로 시작 (= [] 라고 쓰면 모든 상자가 목록 하나를 공유해서 이렇게 씀)
    warnings: list[str] = field(default_factory=list)   # 저장은 되지만 알려 둘 것


# ----- 작은 도우미 함수들 (이름 앞의 _ 는 "이 파일 안에서만 쓰는 도우미" 라는 관례) ------------------
def _text(value) -> str:
    """어떤 값이든 앞뒤 공백을 지운 문자열로 바꿉니다. None 이면 빈 문자열."""
    return str(value or "").strip()   # A or B = A 가 비어 있으면(None, "", 0) B


def _is_int(value) -> bool:
    """진짜 정수인지 확인합니다. (파이썬에서 True/False 도 정수로 취급되므로 그것은 제외)"""
    return isinstance(value, int) and not isinstance(value, bool)


def _to_number(value):
    """
    좌표(x, y)용. 숫자로 바꿀 수 있으면 숫자를, 아니면 None 을 돌려줍니다.
    "inf"(무한대) / "nan"(숫자 아님) / "1e999"(너무 커서 무한대가 됨) 은 JSON 에 넣을 수 없어 DB 가 거부하므로 None 으로 봅니다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    # math.isfinite = 보통의 유한한 숫자인가 (무한대·NaN 이 아닌가). int 는 항상 유한.
    if isinstance(number, float) and not math.isfinite(number):
        return None
    return number


def _to_int_or_none(value):
    """
    글자 크기(font_size)용. 정수로 바꿀 수 있고 DB int 범위 안이면 정수, 아니면 None.
    (OverflowError: float("inf") 처럼 무한대를 int() 로 바꾸려 할 때 나는 오류)
    """
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < INT4_MIN or number > INT4_MAX:
        return None
    return number


def _dict_or_empty(value) -> dict:
    """dict 이면 그대로, 아니면 빈 dict. (JSON 에 sop / studio 가 빠져 있어도 죽지 않게)"""
    if isinstance(value, dict):
        return value
    return {}


def _list_or_empty(value) -> list:
    """list 이면 그대로, 아니면 빈 list. (project.nodes / edges 가 없거나 이상해도 죽지 않게)"""
    if isinstance(value, list):
        return value
    return []


def uuid_or_none(value) -> UUID | None:
    """
    값이 UUID 형식의 문자열이면 UUID 로 바꿔 돌려주고, 비어 있거나 형식이 아니면 None.
    sop 노드의 ref_document_id(참조 문서 id)를 읽을 때 씁니다. refs.py 도 같은 함수를 씁니다.
    """
    text = _text(value)
    if text == "":
        return None
    try:
        return UUID(text)
    except ValueError:
        return None


def _ref_document_id(node: dict, instance_id: str, node_key: str, warnings: list[str]) -> UUID | None:
    """
    sop 노드의 ref_document_id 를 꺼냅니다. 비어 있으면 None(경고 없음),
    무언가 적혀 있는데 UUID 형식이 아니면 None 으로 두고 경고를 남깁니다.
    (저장 흐름에서는 refs.resolve_references 가 먼저 이런 값을 정리하므로 여기서 경고가 나는 일은 드뭅니다)
    """
    raw = _text(node.get("ref_document_id"))
    if raw == "":
        return None
    parsed = uuid_or_none(raw)
    if parsed is None:
        warnings.append(f"{instance_id}: SOP 상자 {node_key} 의 ref_document_id 가 UUID 형식이 아니어서 비웠습니다")
    return parsed


def _list_field(node: dict, key: str, instance_id: str, node_key: str, warnings: list[str]) -> list:
    """노드의 systems / manual 처럼 목록이어야 하는 값을 꺼냅니다. 목록이 아니면 [] 로 두고 경고를 남깁니다."""
    value = node.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{instance_id}: 노드 {node_key} 의 {key} 가 목록이 아니어서 비웠습니다")
        return []
    return value


def _coordinate(node: dict, key: str, instance_id: str, node_key: str, warnings: list[str]):
    """노드의 x / y 좌표를 꺼냅니다. 숫자로 못 바꾸면 0 으로 두고 경고를 남깁니다."""
    number = _to_number(node.get(key))
    if number is None:
        warnings.append(f"{instance_id}: 노드 {node_key} 의 {key} 좌표가 숫자가 아니어서 0 으로 두었습니다")
        return 0
    return number


# ----- 1. 형식 검사 -----------------------------------------------------
def _invalid(message: str) -> ApiError:
    """형식 오류(422 invalid_document)를 만듭니다. 메시지만 바꿔서 여러 곳에서 씁니다."""
    return ApiError(422, "invalid_document", message)


def validate_document(doc) -> None:
    """
    문서가 저장해도 되는 최소 형식인지 검사합니다. 문제가 있으면 422 오류를 던집니다.
    검사 항목: format 값, version 이 정수인지, blocks 가 목록(list)인지, sop.id(문서 번호)가 올바른지.
    """
    if not isinstance(doc, dict):
        raise _invalid("문서(doc)는 JSON 객체여야 합니다.")
    if doc.get("format") != SOP_DOC_FORMAT:
        raise _invalid(f"format 값이 '{SOP_DOC_FORMAT}' 이어야 합니다. (받은 값: {doc.get('format')!r})")
    if not _is_int(doc.get("version")):
        raise _invalid(f"version 은 정수여야 합니다. (받은 값: {doc.get('version')!r})")
    if not isinstance(doc.get("blocks"), list):
        raise _invalid("blocks 는 페이지 목록(배열)이어야 합니다.")

    sop_no = _text(_dict_or_empty(doc.get("sop")).get("id"))
    if sop_no == "":
        raise _invalid("SOP 번호(sop.id)가 비어 있습니다.")
    if re.match(SOP_NO_PATTERN, sop_no) is None:
        raise _invalid(f"SOP 번호 '{sop_no}' 에 허용되지 않는 글자가 있습니다. (영문·숫자·- _ . 만 가능)")


# ----- 2. 문서 메타 정보 -------------------------------------------------
def document_meta(doc) -> DocumentMeta:
    """
    문서 번호·이름·AREA·개정번호·작성자·태그를 꺼냅니다.
    AREA 가 허용값 밖이면 '' 로 바꾸고 warnings 에 남깁니다 (저장은 막지 않음).
    """
    warnings: list[str] = []
    sop = _dict_or_empty(doc.get("sop"))
    studio = _dict_or_empty(doc.get("studio"))

    # AREA: P/E/D/T/C 가 아니면 미지정('') 으로.
    area = _text(studio.get("area"))
    if area not in VALID_AREAS:
        warnings.append(f"area '{area}' 은 허용값(P/E/D/T/C)이 아니어서 미지정('')으로 저장했습니다")
        area = ""

    # 태그: 목록 안의 문자열만, 공백 제거, 빈 것은 버림.
    tags: list[str] = []
    tags_raw = studio.get("tags")
    if isinstance(tags_raw, list):
        for tag in tags_raw:
            if isinstance(tag, str) and tag.strip() != "":
                tags.append(tag.strip())

    # format_version: validate_document 를 거쳤다면 정수. 혹시 아니면 0.
    format_version = doc.get("version")
    if not _is_int(format_version):
        format_version = 0

    return DocumentMeta(
        sop_no=_text(sop.get("id")),
        name=_text(sop.get("name")),
        area=area,
        revision=_text(studio.get("revision")),
        owner=_text(studio.get("owner")),
        tags=tags,
        format=_text(doc.get("format")),
        format_version=format_version,
        warnings=warnings,
    )


# ----- 3. 순서도 노드 / 연결선 → 행(row) ----------------------------------
def _node_row(instance_id: str, node, index: int, warnings: list[str]):
    """
    노드 JSON 한 개를 flow_nodes 행(dict) 으로 바꿉니다.
    형식이 크게 잘못됐으면 warnings 에 이유를 적고 None 을 돌려줍니다 (그 노드만 건너뜀).
    index 는 사람이 읽는 순번(1부터)이라 안내 문구에만 씁니다.
    """
    if not isinstance(node, dict):
        warnings.append(f"{instance_id}: 노드 {index}번째 항목을 건너뜀 (객체가 아님)")
        return None

    node_key = _text(node.get("node"))
    if node_key == "":
        warnings.append(f"{instance_id}: 노드 {index}번째 항목을 건너뜀 (node 키가 비어 있음)")
        return None

    node_type = _text(node.get("node_type"))
    if node_type not in NODE_TYPES:
        warnings.append(
            f"{instance_id}: 노드 {index}번째 항목({node_key})을 건너뜀 (node_type '{node_type}' 는 지원하지 않음)"
        )
        return None

    return {
        "instance_id": instance_id,
        "node_key": node_key,
        "node_type": node_type,
        "name": _text(node.get("name")),
        "role_owner": _text(node.get("role_owner")),
        "action": _text(node.get("action")),
        "description": _text(node.get("description")),
        "systems": _list_field(node, "systems", instance_id, node_key, warnings),
        "manual": _list_field(node, "manual", instance_id, node_key, warnings),
        "ref_sop_no": _text(node.get("sop_id")),
        "ref_sop_name": _text(node.get("sop_name")),
        # sop 노드만 값이 있고 나머지 종류는 None. (sop 노드가 아닌데 값이 있어도 무시)
        "ref_document_id": _ref_document_id(node, instance_id, node_key, warnings) if node_type == "sop" else None,
        "position": {
            "x": _coordinate(node, "x", instance_id, node_key, warnings),
            "y": _coordinate(node, "y", instance_id, node_key, warnings),
        },
        "font_size": _to_int_or_none(node.get("font_size")),
    }


def _edge_row(instance_id: str, edge, index: int, warnings: list[str]):
    """
    연결선 JSON 한 개를 flow_edges 행(dict) 으로 바꿉니다.
    edge / source / target 중 하나라도 비어 있으면 건너뛰고 warnings 에 남깁니다.
    """
    if not isinstance(edge, dict):
        warnings.append(f"{instance_id}: 연결선 {index}번째 항목을 건너뜀 (객체가 아님)")
        return None

    edge_key = _text(edge.get("edge"))
    source_key = _text(edge.get("source"))
    target_key = _text(edge.get("target"))
    if edge_key == "" or source_key == "" or target_key == "":
        warnings.append(f"{instance_id}: 연결선 {index}번째 항목을 건너뜀 (edge/source/target 중 빈 값이 있음)")
        return None

    # 선 종류가 비어 있으면 기본값 orthogonal(직각으로 꺾이는 선).
    line_type = _text(edge.get("line_type")) or "orthogonal"
    # route(꺾은 선 조절점)는 dict 여야 합니다. None 이거나 다른 형식이면 빈 dict.
    route = _dict_or_empty(edge.get("route"))

    return {
        "instance_id": instance_id,
        "edge_key": edge_key,
        "source_key": source_key,
        "target_key": target_key,
        "source_port": _text(edge.get("sourcePort")),
        "target_port": _text(edge.get("targetPort")),
        "condition": _text(edge.get("condition")),
        "line_type": line_type,
        "route": route,
    }


def iter_sop_nodes(doc):
    """
    문서 안의 모든 순서도 페이지에서 "sop 상자"(다른 SOP 를 가리키는 노드)만 골라
    (instance_id, node_key, 노드 dict) 를 하나씩 돌려주는 반복자(generator)입니다.
    돌려주는 노드 dict 는 원본 그대로라서, 받는 쪽에서 값을 고치면 문서(doc)에 바로 반영됩니다.
    (refs.resolve_references 가 저장 직전에 sop_id / sop_name / ref_document_id 를 최신값으로 고칠 때 씁니다)
    """
    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(blocks, list):
        return
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "flowchart":
            continue
        project = block.get("project")
        if not isinstance(project, dict):
            continue
        instance_id = _text(block.get("instanceId")) or f"flow_{block_index}"
        nodes = _list_or_empty(project.get("nodes"))
        for node in nodes:
            if isinstance(node, dict) and _text(node.get("node_type")) == "sop":
                yield instance_id, _text(node.get("node")), node


def derive_flow_rows(doc) -> tuple[list[dict], list[dict], list[str]]:
    """
    문서 안의 모든 순서도 페이지에서 노드와 연결선을 꺼내 (노드 행 목록, 연결선 행 목록, 경고 목록) 으로 돌려줍니다.
    형식이 이상한 노드/연결선은 저장을 막지 않고 그 항목만 건너뛴 뒤 경고에 남깁니다.
    (원본 JSON 은 어차피 통째로 보관되므로 편집기 복원에는 영향이 없습니다)
    """
    node_rows: list[dict] = []
    edge_rows: list[dict] = []
    warnings: list[str] = []

    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(blocks, list):
        return node_rows, edge_rows, warnings

    for block_index, block in enumerate(blocks):
        # 순서도 페이지만 봅니다. project 가 없으면(순서도 편집기가 아직 안 뜬 상태) 노드 없음으로 취급.
        if not isinstance(block, dict) or block.get("type") != "flowchart":
            continue
        project = block.get("project")
        if not isinstance(project, dict):
            continue

        instance_id = _text(block.get("instanceId"))
        if instance_id == "":
            instance_id = f"flow_{block_index}"

        # 같은 순서도 안에서 같은 키가 두 번 나오면 DB 의 UNIQUE 제약에 걸리므로 첫 번째만 남깁니다.
        seen_node_keys: set[str] = set()
        seen_edge_keys: set[str] = set()

        nodes = _list_or_empty(project.get("nodes"))
        for node_index, node in enumerate(nodes):
            row = _node_row(instance_id, node, node_index + 1, warnings)
            if row is None:
                continue
            if row["node_key"] in seen_node_keys:
                warnings.append(f"{instance_id}: 노드 {node_index + 1}번째 항목을 건너뜀 (node '{row['node_key']}' 중복)")
                continue
            seen_node_keys.add(row["node_key"])
            node_rows.append(row)

        edges = _list_or_empty(project.get("edges"))
        for edge_index, edge in enumerate(edges):
            row = _edge_row(instance_id, edge, edge_index + 1, warnings)
            if row is None:
                continue
            if row["edge_key"] in seen_edge_keys:
                warnings.append(f"{instance_id}: 연결선 {edge_index + 1}번째 항목을 건너뜀 (edge '{row['edge_key']}' 중복)")
                continue
            seen_edge_keys.add(row["edge_key"])
            edge_rows.append(row)

    return node_rows, edge_rows, warnings
