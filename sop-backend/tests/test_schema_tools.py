"""
test_schema_tools.py — 표 만들기/갱신 도구(app/tools/apply_schema.py)와 마이그레이션 파일을 시험합니다.

  - 마이그레이션(sql/migrations/*.sql)은 멱등: 두 번 실행해도 오류 없고 결과가 같다
  - apply_schema 는 표가 없으면 전체 스키마, 있으면 마이그레이션만 실행한다
  - --dry-run 은 DB 를 전혀 바꾸지 않는다
  - config.py 의 DB 주소 조립 규칙 (DATABASE_URL 우선 → PGHOST 등 → 기본값)
"""

import os

import psycopg

from app.tools import apply_schema as tool
from tests.conftest import reset_schema


def column_exists(url, table, column):
    """그 표에 그 칸이 있는지 DB 카탈로그에서 확인한다."""
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s", (table, column)
        ).fetchone()
    return row is not None


def index_exists(url, name):
    """그 이름의 인덱스가 DB 에 있는지 확인한다. (to_regclass = 이름이 있으면 이름을, 없으면 NULL 을 돌려주는 PostgreSQL 함수)"""
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def drop_ref_column(url):
    """옛날 DB 인 척: ref_document_id 칸(과 인덱스)을 지운다."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("ALTER TABLE flow_nodes DROP COLUMN IF EXISTS ref_document_id")


# 새 설치: 전체 스키마에 ref_document_id 칸과 부분 인덱스가 들어 있다
def test_fresh_install_has_ref_column(database_url):
    reset_schema(database_url)
    assert column_exists(database_url, "flow_nodes", "ref_document_id")
    assert index_exists(database_url, "ix_flow_nodes_ref_doc")


# 기존 DB(칸 없음)에 apply_schema 를 돌리면 마이그레이션이 칸을 만들고, 한 번 더 돌려도 오류 없이 그대로다
def test_migration_is_idempotent(database_url):
    reset_schema(database_url)
    drop_ref_column(database_url)
    assert not column_exists(database_url, "flow_nodes", "ref_document_id")
    assert not index_exists(database_url, "ix_flow_nodes_ref_doc")

    messages = []
    result = tool.apply_schema(database_url, log=messages.append)
    assert result["mode"] == "migrate"
    assert result["files"] == [os.path.join("sql", "migrations", "001_ref_document_id.sql")]
    assert column_exists(database_url, "flow_nodes", "ref_document_id")
    assert index_exists(database_url, "ix_flow_nodes_ref_doc")

    # 두 번째 실행: 오류 없이 같은 결과
    result_again = tool.apply_schema(database_url, log=messages.append)
    assert result_again["mode"] == "migrate"
    assert column_exists(database_url, "flow_nodes", "ref_document_id")
    assert index_exists(database_url, "ix_flow_nodes_ref_doc")

    # 칸의 외래키(참조 문서가 지워지면 NULL)도 살아 있다: 없는 문서 id 는 넣을 수 없다
    with psycopg.connect(database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO flow_nodes (version_id, instance_id, node_key, node_type, ref_document_id) "
                "VALUES ('00000000-0000-0000-0000-000000000000', 'f', 'n', 'sop', '00000000-0000-0000-0000-000000000000')"
            )
            raise AssertionError("외래키가 없어서 아무 id 나 들어갔습니다")
        except psycopg.errors.ForeignKeyViolation:
            pass


# dry-run 은 무엇을 할지 알려 주기만 하고 DB 를 바꾸지 않는다
def test_dry_run_changes_nothing(database_url):
    reset_schema(database_url)
    drop_ref_column(database_url)

    messages = []
    result = tool.apply_schema(database_url, dry_run=True, log=messages.append)
    assert result["mode"] == "migrate"
    assert result["files"] == [os.path.join("sql", "migrations", "001_ref_document_id.sql")]
    assert any("dry-run" in m for m in messages)
    assert not column_exists(database_url, "flow_nodes", "ref_document_id")

    # 표가 하나도 없을 때의 dry-run 은 "install" 계획을 보여 주고 역시 아무것도 만들지 않는다
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS " + ", ".join(tool.ALL_TABLES) + " CASCADE")
    result = tool.apply_schema(database_url, dry_run=True, log=messages.append)
    assert result["mode"] == "install"
    assert result["files"] == [os.path.join("sql", "sop_schema.sql")]
    with psycopg.connect(database_url) as conn:
        assert not tool.tables_exist(conn)

    reset_schema(database_url)   # 다음 테스트를 위해 원상 복구


# pg_trgm 줄 걷어 내기: 그 두 표시 글자가 든 줄만 빠진다
def test_strip_trgm_removes_only_trgm_lines():
    sql = "CREATE EXTENSION IF NOT EXISTS pg_trgm;\nCREATE TABLE t (a int);\nCREATE INDEX i ON t USING gin (a gin_trgm_ops);\n"
    assert tool.strip_trgm(sql) == "CREATE TABLE t (a int);"


# config: DATABASE_URL 이 최우선, 없으면 PGHOST 등으로 조립, 그것도 없으면 로컬 기본값
def test_database_url_resolution(monkeypatch):
    from app.config import get_settings, mask_password

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:1/d")
    monkeypatch.setenv("PGHOST", "ignored")
    assert get_settings().database_url == "postgresql://u:p@h:1/d"

    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("PGHOST", "db.company.local")
    monkeypatch.setenv("PGPORT", "5433")
    monkeypatch.setenv("PGUSER", "sop_app")
    monkeypatch.setenv("PGPASSWORD", "p@ss/word")
    monkeypatch.setenv("PGDATABASE", "sop")
    url = get_settings().database_url
    # 조립된 접속 정보는 psycopg 가 그대로 읽을 수 있는 형식이고 각 값이 들어 있다
    from psycopg.conninfo import conninfo_to_dict

    parts = conninfo_to_dict(url)
    assert parts == {"host": "db.company.local", "port": "5433", "user": "sop_app", "password": "p@ss/word", "dbname": "sop"}
    # 로그용 마스킹은 비밀번호를 감춘다
    assert "p@ss/word" not in mask_password(url)
    assert "****" in mask_password(url)

    for name in ["PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"]:
        monkeypatch.delenv(name)
    assert get_settings().database_url == "postgresql://postgres:postgres@localhost:5432/sop"


# config: 나머지 새 환경변수의 기본값과 읽기
def test_other_settings(monkeypatch):
    from app.config import get_settings

    for name in ["DB_SESSION_OPTIONS", "DB_CONNECT_TIMEOUT", "DB_POOL_MIN", "DB_POOL_MAX", "DB_PREPARE_THRESHOLD",
                 "ROOT_PATH", "USER_HEADER", "LOG_LEVEL"]:
        monkeypatch.delenv(name, raising=False)
    s = get_settings()
    assert s.db_session_options == "-c timezone=UTC"
    assert s.db_connect_timeout == 30
    assert (s.pool_min_size, s.pool_max_size) == (2, 10)
    assert s.db_prepare_threshold == 5
    assert s.root_path == ""
    assert s.user_header == "X-User"
    assert s.log_level == "INFO"

    monkeypatch.setenv("DB_SESSION_OPTIONS", "")       # 빈 문자열 = options 안 넘김
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "5")
    monkeypatch.setenv("ROOT_PATH", "sop/")             # 앞 / 붙이고 끝 / 떼기
    monkeypatch.setenv("USER_HEADER", "X-Sso-User")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = get_settings()
    assert s.db_session_options == ""
    assert s.db_connect_timeout == 5
    assert s.root_path == "/sop"
    assert s.user_header == "X-Sso-User"
    assert s.log_level == "DEBUG"

    # DB_PREPARE_THRESHOLD: 0 이나 빈 값이면 None(prepared statement 안 씀. PgBouncer 대응), 숫자면 그 값
    monkeypatch.setenv("DB_PREPARE_THRESHOLD", "0")
    assert get_settings().db_prepare_threshold is None
    monkeypatch.setenv("DB_PREPARE_THRESHOLD", "")
    assert get_settings().db_prepare_threshold is None
    monkeypatch.setenv("DB_PREPARE_THRESHOLD", "7")
    assert get_settings().db_prepare_threshold == 7


# DB 풀은 prepare_threshold=None 으로도 열리고 같은 SQL 을 여러 번 실행해도 정상이다 (PgBouncer 대응 경로)
async def test_pool_opens_with_prepare_threshold_disabled(database_url):
    from app import db

    await db.close_pool()
    try:
        await db.open_pool(database_url, 1, 2, prepare_threshold=None)
        async with db.pool.connection() as conn:
            assert conn.prepare_threshold is None
            for _ in range(7):   # 기본값(5)이었다면 여기서 서버측 prepared statement 가 만들어졌을 횟수
                assert (await (await conn.execute("SELECT 1 AS one")).fetchone())["one"] == 1
    finally:
        await db.close_pool()


# USER_HEADER 로 정한 헤더에서 사용자 이름을 읽는다 (저장한 사람 이름에 반영)
async def test_user_header_name_is_configurable(client, monkeypatch):
    from tests.conftest import make_doc

    monkeypatch.setenv("USER_HEADER", "X-Sso-User")
    res = await client.post("/api/sops", json={"doc": make_doc()}, headers={"X-Sso-User": "kim"})
    assert res.status_code == 201, res.text
    versions = (await client.get(f"/api/sops/{res.json()['id']}/versions")).json()
    assert versions[0]["saved_by"] == "kim"
    assert versions[0]["saved_at"].endswith("Z")
