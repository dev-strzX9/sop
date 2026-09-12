"""
test_derive.py — app/derive.py 단위 테스트 (DB 없이 돕니다).

"편집기 JSON → DB 행" 변환이 제대로 되는지, 이상한 입력을 만나도 죽지 않고
경고만 남기는지 확인합니다.

실행:  cd sop-backend && python -m pytest tests/test_derive.py -q
"""

import copy

import pytest

from app.derive import derive_flow_rows, document_meta, validate_document
from app.errors import ApiError


# ---------------------------------------------------------------------
# 테스트에 쓸 "정상 문서" 를 만드는 도우미
# ---------------------------------------------------------------------
def make_nodes():
    """5종 노드(start / seq / decision / sop / end)가 하나씩 들어 있는 목록."""
    return [
        {"node": "start_1", "node_type": "start", "x": 120, "y": 90, "name": "시작 조건 입력", "font_size": 17},
        {
            "node": "seq_3", "node_type": "seq", "x": 300, "y": 200,
            "role_owner": "설비 담당", "action": "Lot 조회", "description": "MES 에서 Lot 확인",
            "systems": [{"name": "MES", "menus": ["Lot 조회"]}], "manual": ["체크리스트 확인"],
            "font_size": 15, "seq_font_sizes": {"action": 15},
        },
        {"node": "decision_1", "node_type": "decision", "x": 500, "y": 200, "role_owner": "", "action": "판단 조건 입력", "description": "", "font_size": 14},
        {"node": "sop_2", "node_type": "sop", "x": 700, "y": 200, "sop_id": "SOP-ETCH-002", "sop_name": "참조 SOP", "description": "", "font_size": 17},
        {"node": "end_1", "node_type": "end", "x": 900, "y": 300, "name": "End", "font_size": 17},
    ]


def make_edges():
    """연결선 3개. 마지막 것은 route 가 None."""
    return [
        {"edge": "edge_a", "source": "start_1", "target": "seq_3", "sourcePort": "bottom", "targetPort": "top", "condition": "", "line_type": "orthogonal", "route": {"x": 200, "y": 150}},
        {"edge": "edge_b", "source": "seq_3", "target": "decision_1", "sourcePort": "right", "targetPort": "left", "condition": "조건 1", "line_type": "straight", "route": None},
        {"edge": "edge_c", "source": "decision_1", "target": "end_1", "sourcePort": "bottom", "targetPort": "top", "condition": "Yes", "line_type": "orthogonal", "route": None},
    ]


def make_flow_block(instance_id, nodes, edges):
    """순서도 페이지 블록 하나."""
    return {
        "type": "flowchart",
        "instanceId": instance_id,
        "project": {"sopInfo": {}, "nodes": nodes, "edges": edges, "counters": {}, "ui": {}},
    }


def make_doc():
    """순서도 2장(두 번째는 노드 1개)이 들어 있는 정상 문서."""
    return {
        "format": "sop-editor-mock",
        "version": 1,
        "savedAt": "2026-09-09T00:00:00Z",
        "sop": {"id": "SOP-ETCH-001", "name": "Chamber PM 절차", "desc": "설명"},
        "studio": {"owner": "홍길동/장비기술", "revision": "1.0", "area": "P", "tags": ["PM", "Chamber"]},
        "mode": "edit",
        "blocks": [
            {"type": "title", "main": "<h2>제목</h2>", "meta": ""},
            make_flow_block("flow_100_1", make_nodes(), make_edges()),
            make_flow_block("flow_100_2", [{"node": "start_1", "node_type": "start", "x": 1, "y": 2, "name": "둘째 장"}], []),
        ],
    }


# ---------------------------------------------------------------------
# derive_flow_rows
# ---------------------------------------------------------------------
# 정상 문서: 순서도 2장 → 노드 6개, 연결선 3개, 경고 없음. 값들이 컬럼 이름대로 옮겨졌는지 확인.
def test_derive_normal_document():
    node_rows, edge_rows, warnings = derive_flow_rows(make_doc())

    assert len(node_rows) == 6
    assert len(edge_rows) == 3
    assert warnings == []

    # 첫 순서도의 노드 5개는 instance_id 가 flow_100_1, 둘째 장은 flow_100_2
    first_rows = []
    for row in node_rows:
        if row["instance_id"] == "flow_100_1":
            first_rows.append(row)
    assert len(first_rows) == 5
    assert node_rows[5]["instance_id"] == "flow_100_2"

    # seq 노드 필드 매핑
    seq = first_rows[1]
    assert seq["node_key"] == "seq_3"
    assert seq["node_type"] == "seq"
    assert seq["role_owner"] == "설비 담당"
    assert seq["action"] == "Lot 조회"
    assert seq["description"] == "MES 에서 Lot 확인"
    assert seq["systems"] == [{"name": "MES", "menus": ["Lot 조회"]}]
    assert seq["manual"] == ["체크리스트 확인"]
    assert seq["position"] == {"x": 300, "y": 200}
    assert seq["font_size"] == 15

    # sop 노드: sop_id → ref_sop_no, sop_name → ref_sop_name
    sop = first_rows[3]
    assert sop["ref_sop_no"] == "SOP-ETCH-002"
    assert sop["ref_sop_name"] == "참조 SOP"

    # start 노드: name 매핑, 없는 값은 빈 문자열
    start = first_rows[0]
    assert start["name"] == "시작 조건 입력"
    assert start["role_owner"] == ""
    assert start["systems"] == []

    # 연결선 매핑
    edge = edge_rows[1]
    assert edge["edge_key"] == "edge_b"
    assert edge["source_key"] == "seq_3"
    assert edge["target_key"] == "decision_1"
    assert edge["source_port"] == "right"
    assert edge["target_port"] == "left"
    assert edge["condition"] == "조건 1"
    assert edge["line_type"] == "straight"


# route 가 None 이면 {} 로, dict 이면 그대로.
def test_edge_route_none_becomes_empty_dict():
    _, edge_rows, _ = derive_flow_rows(make_doc())
    assert edge_rows[0]["route"] == {"x": 200, "y": 150}
    assert edge_rows[1]["route"] == {}
    assert edge_rows[2]["route"] == {}


# project 가 None 인 순서도 블록(아직 편집기가 안 뜬 상태)은 조용히 무시.
def test_flowchart_block_with_null_project_is_ignored():
    doc = make_doc()
    doc["blocks"].append({"type": "flowchart", "instanceId": "flow_empty", "project": None})
    node_rows, edge_rows, warnings = derive_flow_rows(doc)
    assert len(node_rows) == 6
    assert len(edge_rows) == 3
    assert warnings == []


# instanceId 가 없으면 블록 순번으로 flow_<index> 를 만든다.
def test_missing_instance_id_uses_block_index():
    doc = make_doc()
    del doc["blocks"][1]["instanceId"]          # blocks[1] 이 첫 순서도
    doc["blocks"][2]["instanceId"] = ""          # 빈 문자열도 없는 것으로 취급
    node_rows, _, _ = derive_flow_rows(doc)
    assert node_rows[0]["instance_id"] == "flow_1"
    assert node_rows[5]["instance_id"] == "flow_2"


# 잘못된 노드 3종(node_type 이상 / dict 아님 / node 비어 있음)은 건너뛰고 경고, 나머지는 살아 있음.
def test_bad_nodes_are_skipped_with_warnings():
    nodes = make_nodes()
    nodes.append({"node": "weird_1", "node_type": "foo", "x": 0, "y": 0})   # 지원하지 않는 종류
    nodes.append("이건 문자열")                                              # dict 아님
    nodes.append({"node": "", "node_type": "seq", "x": 0, "y": 0})           # node 키 비어 있음
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]

    node_rows, _, warnings = derive_flow_rows(doc)
    assert len(node_rows) == 5
    assert len(warnings) == 3
    assert "foo" in warnings[0]
    assert "7번째" in warnings[1]
    assert "비어 있음" in warnings[2]
    for message in warnings:
        assert message.startswith("flow_x:")


# 같은 순서도 안에서 node 키가 중복되면 첫 번째만 남기고 경고.
def test_duplicate_node_key_is_skipped():
    nodes = make_nodes()
    nodes.append({"node": "seq_3", "node_type": "seq", "x": 1, "y": 1, "action": "두 번째"})
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]

    node_rows, _, warnings = derive_flow_rows(doc)
    assert len(node_rows) == 5
    assert node_rows[1]["action"] == "Lot 조회"      # 첫 번째 것이 남는다
    assert len(warnings) == 1
    assert "중복" in warnings[0]


# 다른 순서도 장에서는 같은 node 키를 써도 중복이 아니다.
def test_same_node_key_in_different_flowcharts_is_allowed():
    node_rows, _, warnings = derive_flow_rows(make_doc())   # 두 장 모두 start_1 이 있음
    start_count = 0
    for row in node_rows:
        if row["node_key"] == "start_1":
            start_count += 1
    assert start_count == 2
    assert warnings == []


# 잘못된 연결선(dict 아님 / source 비어 있음 / edge 중복)은 건너뛰고 경고.
def test_bad_edges_are_skipped_with_warnings():
    edges = make_edges()
    edges.append(123)                                                      # dict 아님
    edges.append({"edge": "edge_d", "source": "", "target": "end_1"})     # source 비어 있음
    edges.append({"edge": "edge_a", "source": "start_1", "target": "end_1"})   # edge 중복
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", make_nodes(), edges)]

    _, edge_rows, warnings = derive_flow_rows(doc)
    assert len(edge_rows) == 3
    assert len(warnings) == 3


# line_type 이 비어 있으면 기본값 orthogonal.
def test_edge_line_type_defaults_to_orthogonal():
    edges = [{"edge": "e1", "source": "a", "target": "b"}]
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", [], edges)]
    _, edge_rows, _ = derive_flow_rows(doc)
    assert edge_rows[0]["line_type"] == "orthogonal"
    assert edge_rows[0]["source_port"] == ""
    assert edge_rows[0]["route"] == {}


# systems / manual 이 목록이 아니면 [] 로 두고 경고. 좌표가 숫자가 아니면 0 으로 두고 경고. font_size 는 조용히 None.
def test_bad_field_values_are_normalized():
    nodes = [{"node": "seq_1", "node_type": "seq", "x": "abc", "y": None, "systems": "MES", "manual": {"a": 1}, "font_size": "크게"}]
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]

    node_rows, _, warnings = derive_flow_rows(doc)
    assert len(node_rows) == 1
    row = node_rows[0]
    assert row["systems"] == []
    assert row["manual"] == []
    assert row["position"] == {"x": 0, "y": 0}
    assert row["font_size"] is None
    assert len(warnings) == 4   # systems, manual, x, y (font_size 는 경고 없음)


# 좌표에 inf / nan / 1e999(너무 커서 무한대) 가 오면 0 으로 두고 경고, 글자 크기가 DB int 범위 밖이면 조용히 None.
# (이런 값을 그대로 넘기면 DB 가 거부해 저장 전체가 500 이 되므로, 다른 잘못된 값과 똑같이 "정리하고 경고" 로 다룬다)
def test_non_finite_and_out_of_range_numbers_are_normalized():
    nodes = [
        {"node": "n1", "node_type": "start", "x": "inf", "y": "nan", "font_size": 2**31},
        {"node": "n2", "node_type": "start", "x": "1e999", "y": float("-inf"), "font_size": "99999999999"},
        {"node": "n3", "node_type": "start", "x": float("nan"), "y": 1e999, "font_size": -2**31 - 1},
        {"node": "n4", "node_type": "start", "x": 1, "y": 2.5, "font_size": 2147483647},   # 경계값은 살아 있어야 함
    ]
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]

    node_rows, _, warnings = derive_flow_rows(doc)
    rows = {row["node_key"]: row for row in node_rows}
    assert rows["n1"]["position"] == {"x": 0, "y": 0}
    assert rows["n2"]["position"] == {"x": 0, "y": 0}
    assert rows["n3"]["position"] == {"x": 0, "y": 0}
    assert rows["n4"]["position"] == {"x": 1, "y": 2.5}
    assert rows["n1"]["font_size"] is None
    assert rows["n2"]["font_size"] is None
    assert rows["n3"]["font_size"] is None
    assert rows["n4"]["font_size"] == 2147483647
    assert len(warnings) == 6          # n1~n3 의 x, y 좌표 경고 (font_size 는 경고 없음)
    for message in warnings:
        assert "좌표가 숫자가 아니어서 0 으로" in message


# 문자열 숫자 좌표("120")는 숫자로 바꾸고, font_size 도 정수로 바꾼다.
def test_numeric_strings_are_converted():
    nodes = [{"node": "n1", "node_type": "start", "x": "120", "y": 90.5, "font_size": "17"}]
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]
    node_rows, _, warnings = derive_flow_rows(doc)
    assert node_rows[0]["position"] == {"x": 120.0, "y": 90.5}
    assert node_rows[0]["font_size"] == 17
    assert warnings == []


# ---------------------------------------------------------------------
# validate_document — 4가지 422
# ---------------------------------------------------------------------
# format 이 다르면 422 invalid_document.
def test_validate_rejects_wrong_format():
    doc = make_doc()
    doc["format"] = "something-else"
    with pytest.raises(ApiError) as caught:
        validate_document(doc)
    assert caught.value.status_code == 422
    assert caught.value.code == "invalid_document"


# version 이 정수가 아니면(문자열, 실수, bool) 422.
def test_validate_rejects_non_integer_version():
    for bad_version in ["1", 1.5, True, None]:
        doc = make_doc()
        doc["version"] = bad_version
        with pytest.raises(ApiError) as caught:
            validate_document(doc)
        assert caught.value.status_code == 422
        assert caught.value.code == "invalid_document"


# blocks 가 목록이 아니면 422.
def test_validate_rejects_non_list_blocks():
    doc = make_doc()
    doc["blocks"] = {"type": "title"}
    with pytest.raises(ApiError) as caught:
        validate_document(doc)
    assert caught.value.status_code == 422
    assert caught.value.code == "invalid_document"


# sop.id 가 비었거나 허용 글자 밖이면 422.
def test_validate_rejects_bad_sop_id():
    for bad_id in ["", "   ", None, "SOP 001", "SOP/001", "한글번호"]:
        doc = make_doc()
        doc["sop"]["id"] = bad_id
        with pytest.raises(ApiError) as caught:
            validate_document(doc)
        assert caught.value.status_code == 422
        assert caught.value.code == "invalid_document"


# doc 자체가 dict 가 아니면 422. 정상 문서는 아무 일도 없음.
def test_validate_rejects_non_dict_and_accepts_good_doc():
    with pytest.raises(ApiError) as caught:
        validate_document(["not", "a", "dict"])
    assert caught.value.status_code == 422

    validate_document(make_doc())   # 오류 없이 지나가야 함
    doc = make_doc()
    doc["sop"]["id"] = "SOP_etch.001-A"   # 허용 글자만 쓴 번호
    validate_document(doc)


# ---------------------------------------------------------------------
# document_meta
# ---------------------------------------------------------------------
# 정상 문서의 메타 값이 그대로 나온다.
def test_document_meta_normal():
    meta = document_meta(make_doc())
    assert meta.sop_no == "SOP-ETCH-001"
    assert meta.name == "Chamber PM 절차"
    assert meta.area == "P"
    assert meta.revision == "1.0"
    assert meta.owner == "홍길동/장비기술"
    assert meta.tags == ["PM", "Chamber"]
    assert meta.format == "sop-editor-mock"
    assert meta.format_version == 1
    assert meta.warnings == []


# area 가 허용값 밖이면 '' 로 바꾸고 경고 한 줄.
def test_document_meta_invalid_area_becomes_empty_with_warning():
    doc = make_doc()
    doc["studio"]["area"] = "X"
    meta = document_meta(doc)
    assert meta.area == ""
    assert meta.warnings == ["area 'X' 은 허용값(P/E/D/T/C)이 아니어서 미지정('')으로 저장했습니다"]


# area 가 없거나 빈 값이면 경고 없이 ''.
def test_document_meta_missing_area_is_empty_without_warning():
    doc = make_doc()
    del doc["studio"]["area"]
    meta = document_meta(doc)
    assert meta.area == ""
    assert meta.warnings == []


# tags: 문자열만, 공백 제거, 빈 것 제외. 목록이 아니면 [].
def test_document_meta_tags_cleanup():
    doc = make_doc()
    doc["studio"]["tags"] = ["  PM ", "", 3, None, "Chamber", "   "]
    assert document_meta(doc).tags == ["PM", "Chamber"]

    doc["studio"]["tags"] = "PM,Chamber"
    assert document_meta(doc).tags == []


# 문자열 값은 앞뒤 공백을 지운다. studio / sop 이 아예 없어도 죽지 않는다.
def test_document_meta_strips_whitespace_and_tolerates_missing_sections():
    doc = make_doc()
    doc["sop"]["id"] = "  SOP-1  "
    doc["sop"]["name"] = "  이름  "
    doc["studio"]["owner"] = " 홍길동 "
    doc["studio"]["revision"] = " 2.0 "
    meta = document_meta(doc)
    assert meta.sop_no == "SOP-1"
    assert meta.name == "이름"
    assert meta.owner == "홍길동"
    assert meta.revision == "2.0"

    bare = {"format": "sop-editor-mock", "version": 1, "blocks": []}
    meta = document_meta(bare)
    assert meta.sop_no == ""
    assert meta.area == ""
    assert meta.tags == []
    assert meta.warnings == []


# 원본 문서를 바꾸지 않는다 (통째로 저장하는 content 가 오염되면 안 됨).
def test_functions_do_not_modify_input():
    doc = make_doc()
    snapshot = copy.deepcopy(doc)
    validate_document(doc)
    document_meta(doc)
    derive_flow_rows(doc)
    assert doc == snapshot


# ---------------------------------------------------------------------
# sop 상자의 ref_document_id / iter_sop_nodes (참조 연결용 도우미)
# ---------------------------------------------------------------------
# UUID 형식이면 UUID 로, 비어 있거나 형식이 아니면 None.
def test_uuid_or_none():
    from uuid import UUID

    from app.derive import uuid_or_none

    assert uuid_or_none(" 00000000-0000-0000-0000-000000000000 ") == UUID("00000000-0000-0000-0000-000000000000")
    assert uuid_or_none("") is None
    assert uuid_or_none(None) is None
    assert uuid_or_none("이건-uuid-아님") is None


# sop 노드의 ref_document_id 는 UUID 면 행에 들어가고, 형식이 아니면 None + 경고 1건. 다른 종류 노드는 항상 None.
def test_ref_document_id_in_node_rows():
    nodes = make_nodes()
    nodes[3]["ref_document_id"] = "00000000-0000-0000-0000-000000000001"   # sop_2
    nodes[1]["ref_document_id"] = "00000000-0000-0000-0000-000000000002"   # seq_3 (sop 아님 → 무시)
    nodes.append({"node": "sop_9", "node_type": "sop", "x": 0, "y": 0, "sop_id": "SOP-X", "ref_document_id": "bad"})
    doc = make_doc()
    doc["blocks"] = [make_flow_block("flow_x", nodes, [])]

    node_rows, _, warnings = derive_flow_rows(doc)
    rows = {row["node_key"]: row for row in node_rows}
    assert str(rows["sop_2"]["ref_document_id"]) == "00000000-0000-0000-0000-000000000001"
    assert rows["seq_3"]["ref_document_id"] is None
    assert rows["sop_9"]["ref_document_id"] is None
    assert len(warnings) == 1
    assert "sop_9" in warnings[0] and "UUID" in warnings[0]


# iter_sop_nodes 는 순서도 페이지의 sop 상자만 (instance_id, node_key, 원본 dict) 로 돌려주고, 고치면 문서에 반영된다.
def test_iter_sop_nodes_yields_only_sop_boxes_by_reference():
    from app.derive import iter_sop_nodes

    doc = make_doc()
    found = list(iter_sop_nodes(doc))
    assert [(i, k) for i, k, _ in found] == [("flow_100_1", "sop_2")]
    found[0][2]["sop_name"] = "바뀐 이름"
    assert doc["blocks"][1]["project"]["nodes"][3]["sop_name"] == "바뀐 이름"

    # project 가 None 이거나 순서도가 아닌 블록은 건너뛴다
    doc["blocks"].append({"type": "flowchart", "instanceId": "flow_empty", "project": None})
    assert len(list(iter_sop_nodes(doc))) == 1
