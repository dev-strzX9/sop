"""
conftest.py — 모든 테스트가 공통으로 쓰는 준비물을 모아 둔 곳.

pytest 는 테스트를 돌리기 전에 이 파일을 자동으로 읽습니다. 여기서 준비하는 것:
  1. 테스트용 DB 주소 정하기  (database_url)
     - 환경변수 TEST_DATABASE_URL 이 있으면 그 DB 를 쓰고,
     - 없으면 pgserver 패키지로 임시 PostgreSQL 을 컴퓨터 안에 잠깐 띄웁니다.
  2. 테이블을 전부 지우고 스키마(sop_schema.sql)를 다시 만들기  (reset_schema)
     → 테스트마다 깨끗한 빈 DB 에서 시작합니다. (app/tools/apply_schema.py 의 함수를 그대로 씀)
  3. 서버를 실제로 띄우지 않고 API 를 호출할 수 있는 가짜 브라우저 만들기  (client)
  4. 테스트에서 쓸 샘플 문서 JSON 을 만드는 함수  (make_doc)  와 저장 API 를 부르는 함수  (save)

!! 경고 !!
  테스트는 시작할 때마다 DB 의 테이블을 전부 삭제(DROP)합니다.
  TEST_DATABASE_URL 에는 반드시 "버려도 되는" 테스트 전용 DB 만 넣으세요. 운영 DB 절대 금지.
"""

import os
import warnings

import httpx
import pytest

from app.tools.apply_schema import drop_and_reinstall

# 이 파일(tests/conftest.py)에서 두 단계 위가 프로젝트 루트(sop-backend)입니다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1x1 픽셀짜리 아주 작은 투명 PNG 이미지 (본문 페이지의 이미지 오브젝트 테스트용)
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------------
# 1. 테스트용 DB 주소
# ---------------------------------------------------------------------
# fixture = 테스트 함수가 인자 이름으로 받아 쓰는 "준비물". scope="session" = 전체 테스트 실행에서 한 번만 만든다.
# tmp_path_factory = pytest 가 주는 임시 폴더 만들기 도구 (테스트가 끝나면 알아서 정리).
@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    """
    테스트 전체(세션)에서 딱 한 번 실행되어 DB 접속 주소를 돌려줍니다.
    환경변수가 있으면 그 DB 를, 없으면 pgserver 로 내장 PostgreSQL 을 띄워서 씁니다.
    """
    url_from_env = os.environ.get("TEST_DATABASE_URL")
    if url_from_env:
        # 사람이 직접 지정한 테스트 DB. (위 경고 참고: 반드시 버려도 되는 DB)
        # 운영 DB 보호 장치: 주소에 "test" 라는 글자가 없으면 실수로 운영 주소를 넣은 것으로 보고 멈춥니다.
        # 정말로 그 주소를 쓰려면 TEST_DB_ALLOW_DROP=1 을 함께 주세요.
        if "test" not in url_from_env.lower() and os.environ.get("TEST_DB_ALLOW_DROP") != "1":
            pytest.exit(
                "TEST_DATABASE_URL 의 주소에는 'test' 라는 글자가 들어 있어야 합니다 (운영 DB 보호). "
                "테스트가 그 DB 의 표를 전부 지웁니다. 정말 맞다면 TEST_DB_ALLOW_DROP=1 을 함께 주세요."
            )
        yield url_from_env   # yield 로 값을 넘겨 주고, 테스트가 다 끝나면 이어서 아래로 내려온다
        return               # (정리할 것이 없으니 그냥 끝)

    try:
        import pgserver
    except ImportError:
        pytest.skip("TEST_DATABASE_URL 을 설정하거나 pip install pgserver 를 해 주세요")

    # 임시 폴더에 데이터 파일을 두고 PostgreSQL 을 띄웁니다. 테스트가 끝나면 지웁니다.
    data_dir = str(tmp_path_factory.mktemp("pgdata"))
    pg = pgserver.get_server(data_dir)
    try:
        yield pg.get_uri()
    finally:
        pg.cleanup()


# ---------------------------------------------------------------------
# 2. 스키마 초기화
# ---------------------------------------------------------------------
def reset_schema(url: str) -> None:
    """
    DB 를 완전히 비우고 sql/sop_schema.sql 대로 테이블을 다시 만듭니다. (apply_schema.drop_and_reinstall 을 그대로 씀)
    pg_trgm 확장(부분 검색용 부가 기능)이 설치되지 않은 PostgreSQL 이면 그 확장을 쓰는 줄만 빼고 만듭니다.
    검색 인덱스가 없을 뿐 기능은 같습니다.
    """
    result = drop_and_reinstall(url, log=lambda message: None)   # 진행 문구는 테스트 출력에 섞이지 않게 조용히
    if result["trgm_skipped"]:
        warnings.warn("pg_trgm 확장이 없어 부분 검색 인덱스 없이 스키마를 만들었습니다. (기능은 동일)")


# ---------------------------------------------------------------------
# 3. API 를 부를 수 있는 가짜 브라우저
# ---------------------------------------------------------------------
@pytest.fixture
async def client(database_url):
    """
    테스트 한 개마다 새로 만들어지는 API 호출 도구(httpx.AsyncClient).
    실제 서버 프로세스를 띄우지 않고 FastAPI 앱을 메모리 안에서 직접 호출합니다.

    주의: ASGITransport 는 앱의 lifespan(켜질 때/꺼질 때 하는 일)을 실행하지 않습니다.
    그래서 main.py 대신 여기서 DB 풀을 직접 열고(open_pool) 닫습니다(close_pool).
    """
    # 앱이 이 DB 를 쓰도록 환경변수를 먼저 정한 뒤에 앱을 import 합니다.
    os.environ["DATABASE_URL"] = database_url
    from app import db
    from app.main import app

    reset_schema(database_url)
    await db.open_pool(database_url, 1, 5)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers={"X-User": "tester"}
        ) as ac:
            yield ac
    finally:
        await db.close_pool()


# ---------------------------------------------------------------------
# 4. 샘플 문서 만들기 / 저장 API 부르기
# ---------------------------------------------------------------------
def make_doc(
    sop_no="SOP-ETCH-001",
    name="Chamber PM 절차",
    area="P",
    revision="1.0",
    owner="hong/장비기술",
    extra_seq=0,
):
    """
    편집기(프론트)가 만드는 것과 같은 모양의 문서 JSON 을 만듭니다.
    표지 1장 + 순서도 1장(노드 5개, 연결선 4개) + Activity 정의서 1장 + 본문 1장.
    순서도의 sop 상자(sop_1)는 "SOP-ETCH-002" 를 번호로만 가리킵니다 (ref_document_id 없음 = 그 문서가 있으면 저장 때 연결됨).
    extra_seq 를 주면 seq 노드를 그 개수만큼 더 붙입니다 (노드 수 변화를 시험할 때 사용).
    """
    nodes = [
        {"node": "start_1", "node_type": "start", "x": 120, "y": 90, "name": "시작 조건 입력", "font_size": 17},
        {
            "node": "seq_1", "node_type": "seq", "x": 120, "y": 220,
            "role_owner": "장비 엔지니어", "action": "Chamber Open", "description": "PM 시작",
            "systems": [{"name": "MES", "menus": ["Lot 조회"]}],
            "manual": ["안전 장구 착용"],
            "font_size": 15,
            "seq_font_sizes": {"role": 12, "action": 15},
        },
        {
            "node": "decision_1", "node_type": "decision", "x": 120, "y": 350,
            "role_owner": "", "action": "Particle 이상?", "description": "", "font_size": 14,
        },
        {
            "node": "sop_1", "node_type": "sop", "x": 400, "y": 350,
            "sop_id": "SOP-ETCH-002", "sop_name": "Particle 대응 절차", "ref_document_id": None,
            "description": "", "font_size": 17,
        },
        {"node": "end_1", "node_type": "end", "x": 120, "y": 480, "name": "End", "font_size": 17},
    ]
    # 노드 수를 늘리고 싶을 때 seq 노드를 더 붙인다 (seq_2, seq_3 ...)
    for i in range(extra_seq):
        seq_number = i + 2
        nodes.append({
            "node": f"seq_{seq_number}", "node_type": "seq", "x": 700, "y": 220 + 130 * i,
            "role_owner": "Role Owner", "action": f"추가 Action {seq_number}", "description": "",
            "systems": [], "manual": [], "font_size": 15,
        })

    edges = [
        {"edge": "edge_1725000000000_a001", "source": "start_1", "target": "seq_1",
         "sourcePort": "bottom", "targetPort": "top", "condition": "", "line_type": "orthogonal", "route": None},
        {"edge": "edge_1725000000000_a002", "source": "seq_1", "target": "decision_1",
         "sourcePort": "bottom", "targetPort": "top", "condition": "", "line_type": "orthogonal", "route": None},
        {"edge": "edge_1725000000000_a003", "source": "decision_1", "target": "sop_1",
         "sourcePort": "right", "targetPort": "left", "condition": "Yes", "line_type": "orthogonal", "route": {"x": 260, "y": 350}},
        {"edge": "edge_1725000000000_a004", "source": "decision_1", "target": "end_1",
         "sourcePort": "bottom", "targetPort": "top", "condition": "No", "line_type": "orthogonal", "route": None},
    ]

    flowchart_block = {
        "type": "flowchart",
        "instanceId": "flow_1725000000000_3",
        "project": {
            "sopInfo": {"sop_id": sop_no, "sop_name": name, "description": "테스트용 SOP"},
            "nodes": nodes,
            "edges": edges,
            "counters": {"start": 1, "seq": 1 + extra_seq, "decision": 1, "sop": 1, "end": 1},
            "ui": {"zoom": 1},
        },
    }

    doc = {
        "format": "sop-editor-mock",
        "version": 1,
        "savedAt": "2026-09-09T00:00:00.000Z",
        "sop": {"id": sop_no, "name": name, "desc": "테스트용 SOP"},
        "studio": {"owner": owner, "revision": revision, "area": area, "tags": ["PM", "Chamber"]},
        "mode": "edit",
        "blocks": [
            {"type": "title", "main": f"<h2>{name}</h2>", "meta": f"{sop_no} / Rev {revision}"},
            flowchart_block,
            {"type": "activity", "title": "Activity 정의서", "rows": 2, "cols": 2,
             "cells": ["Step", "설명", "1", "Chamber Open"]},
            {
                "type": "body-template",
                "title": "본문",
                "objects": [
                    {"kind": "image", "left": "8%", "top": "8%", "width": "20%", "height": "20%",
                     "zIndex": "10", "src": TINY_PNG, "alt": "테스트 이미지"},
                    {"kind": "text", "left": "40%", "top": "8%", "width": "44%", "height": "26%",
                     "zIndex": "11", "html": "<p>본문 텍스트</p>"},
                ],
            },
        ],
    }
    return doc


async def save(client, doc, base_version_no=None, change_note="", saved_by=""):
    """
    편집기(프론트)와 같은 방식으로 저장합니다: 먼저 POST /api/sops (새 문서) 를 부르고,
    "이미 있는 번호"(409 sop_no_taken) 라고 하면 그 문서의 id 로 PUT /api/sops/{id} (새 버전) 을 부릅니다.
    마지막에 부른 API 의 응답(httpx.Response)을 그대로 돌려줍니다. 상태 코드와 JSON 확인은 각 테스트가 합니다.
    """
    body = {
        "doc": doc,
        "base_version_no": base_version_no,
        "change_note": change_note,
        "saved_by": saved_by,
    }
    res = await client.post("/api/sops", json=body)
    if res.status_code == 409 and res.json()["error"]["code"] == "sop_no_taken":
        existing_id = res.json()["error"]["existing_id"]
        res = await client.put(f"/api/sops/{existing_id}", json=body)
    return res
