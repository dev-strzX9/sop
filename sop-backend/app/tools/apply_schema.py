"""
apply_schema.py — DB 에 표(테이블)를 만들거나, 이미 있는 표를 최신 모양으로 고치는 도구.

비유하자면 "책장 조립 설명서" 입니다.
  - 책장이 아직 없으면(빈 DB)          → 전체 설명서(sql/sop_schema.sql)대로 처음부터 조립합니다.
  - 책장이 이미 있으면(표가 있는 DB)    → "선반 하나 추가" 같은 부분 설명서(sql/migrations/*.sql)를
                                          이름순(001, 002 ...)으로 차례로 적용합니다.
    부분 설명서는 전부 "멱등"(몇 번 실행해도 결과가 같음)이라 매번 전부 실행해도 안전합니다.

실행
  python -m app.tools.apply_schema                       DATABASE_URL 환경변수의 DB 에 적용
  python -m app.tools.apply_schema --url postgresql://...  주소를 직접 지정
  python -m app.tools.apply_schema --dry-run             무엇을 할지 보여 주기만 하고 실제로는 안 함

pg_trgm 이 없을 때
  부분 검색 인덱스에 쓰는 pg_trgm 확장이 설치돼 있지 않거나, CREATE EXTENSION 권한이 없는 DB 에서는
  그 확장을 쓰는 줄만 빼고 나머지를 실행합니다. 검색 기능은 똑같이 동작하고 속도만 조금 다릅니다.

이 모듈의 함수는 dev_server.py(내장 PostgreSQL 첫 실행)와 tests/conftest.py(테스트용 DB 초기화)도 같이 씁니다.
"""

import argparse
import glob
import os
import sys

import psycopg

# 이 파일 기준으로 프로젝트 루트(sop-backend/)와 SQL 파일 위치를 계산합니다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "sql", "sop_schema.sql")
MIGRATIONS_DIR = os.path.join(PROJECT_ROOT, "sql", "migrations")

# pg_trgm 관련 줄을 알아보는 표시 글자. 이 글자가 든 줄은 "확장이 없을 때" 빼고 실행합니다.
TRGM_MARKERS = ("pg_trgm", "gin_trgm_ops")

# "pg_trgm 이 없다 / 못 만든다" 일 때 PostgreSQL 이 내는 오류 종류들.
#   FeatureNotSupported  : 이 서버는 확장을 지원하지 않음
#   UndefinedFile        : 확장 파일이 설치돼 있지 않음 (내장 pgserver 가 이 경우)
#   UndefinedObject      : gin_trgm_ops 같은 이름을 모름
#   InsufficientPrivilege: CREATE EXTENSION 할 권한이 없음 (회사 DB 에서 흔함)
TRGM_ERRORS = (
    psycopg.errors.FeatureNotSupported,
    psycopg.errors.UndefinedFile,
    psycopg.errors.UndefinedObject,
    psycopg.errors.InsufficientPrivilege,
)

# 우리 표 이름 전부 (테스트에서 DROP 할 때 씀). 서로 연결돼 있어 CASCADE 로 한 번에 지웁니다.
ALL_TABLES = ("sop_edit_locks", "flow_edges", "flow_nodes", "sop_versions", "sop_documents")


# ----- 작은 도우미들 ------------------------------------------------------------------------
def read_sql(path: str) -> str:
    """SQL 파일을 통째로 읽어 문자열로 돌려줍니다."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip_trgm(sql: str) -> str:
    """SQL 에서 pg_trgm 확장을 쓰는 줄만 빼고 돌려줍니다. (확장이 없는 DB 용 대체 경로)"""
    kept_lines = []
    for line in sql.splitlines():
        if any(marker in line for marker in TRGM_MARKERS):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def is_trgm_error(error: Exception) -> bool:
    """이 오류가 "pg_trgm 이 없어서/못 만들어서" 난 것인지 판단합니다. 다른 이유의 오류는 False."""
    if not isinstance(error, TRGM_ERRORS):
        return False
    return any(marker in str(error) for marker in TRGM_MARKERS)


def tables_exist(conn) -> bool:
    """우리 표(sop_documents)가 이미 만들어져 있는지 확인합니다."""
    # to_regclass = "이 이름의 표가 있으면 이름을, 없으면 NULL 을" 돌려주는 PostgreSQL 함수
    row = conn.execute("SELECT to_regclass('sop_documents') AS found").fetchone()
    return row[0] is not None


def list_migration_files() -> list[str]:
    """sql/migrations/ 안의 .sql 파일 경로를 이름순으로 돌려줍니다. (001_..., 002_... 순서가 곧 실행 순서)"""
    return sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))


def run_sql_file(conn, path: str) -> bool:
    """
    SQL 파일 하나를 한 묶음(트랜잭션)으로 실행합니다. 중간에 실패하면 그 파일의 내용은 전부 취소됩니다.
    pg_trgm 때문에 실패하면 그 줄만 빼고 한 번 더 시도합니다.
    돌려주는 값: pg_trgm 줄을 빼고 실행했으면 True, 원본 그대로 성공했으면 False.
    """
    sql = read_sql(path)
    try:
        with conn.transaction():
            conn.execute(sql)
        return False
    except Exception as error:
        if not is_trgm_error(error):
            raise
    # pg_trgm 관련 줄만 빼고 다시 시도
    with conn.transaction():
        conn.execute(strip_trgm(sql))
    return True


# ----- 본체 ------------------------------------------------------------------------------
def apply_schema(database_url: str, dry_run: bool = False, log=print) -> dict:
    """
    DB 상태를 보고 알맞은 SQL 을 실행합니다.
      - 표가 없으면  : sql/sop_schema.sql 전체 실행 (새 설치)
      - 표가 있으면  : sql/migrations/*.sql 을 이름순으로 실행 (기존 DB 갱신)
    dry_run=True 면 무엇을 실행할지 보여 주기만 하고 DB 는 건드리지 않습니다.
    log 는 진행 상황을 출력할 함수 (기본 print. 조용히 하려면 lambda m: None).
    돌려주는 값: {"mode": "install" 또는 "migrate", "files": [실행한 파일들], "trgm_skipped": bool}
    """
    result = {"mode": "", "files": [], "trgm_skipped": False}

    try:
        conn = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "DB 에 접속할 수 없습니다. DATABASE_URL(또는 --url / PGHOST 등) 값과 방화벽, 비밀번호를 확인하세요.\n"
            f"  원인: {error}"
        ) from error

    with conn:
        if tables_exist(conn):
            result["mode"] = "migrate"
            files = list_migration_files()
            log(f"표가 이미 있습니다 → 마이그레이션 {len(files)}개를 이름순으로 적용합니다.")
        else:
            result["mode"] = "install"
            files = [SCHEMA_PATH]
            log("표가 없습니다 → 전체 스키마(sql/sop_schema.sql)를 실행합니다.")

        for path in files:
            name = os.path.relpath(path, PROJECT_ROOT)
            if dry_run:
                log(f"  [dry-run] 실행 예정: {name}")
                result["files"].append(name)
                continue
            skipped = run_sql_file(conn, path)
            result["files"].append(name)
            if skipped:
                result["trgm_skipped"] = True
                log(f"  적용 완료: {name}  (pg_trgm 확장을 못 써서 부분 검색 인덱스 줄은 건너뜀 — 기능은 동일)")
            else:
                log(f"  적용 완료: {name}")

    if dry_run:
        log("dry-run 이라 실제로는 아무것도 바꾸지 않았습니다.")
    elif result["trgm_skipped"]:
        log(
            "안내: pg_trgm 확장이 없거나 만들 권한이 없어 부분 검색 인덱스 없이 진행했습니다.\n"
            "      DBA 에게 'CREATE EXTENSION pg_trgm;' 을 부탁한 뒤 이 도구를 다시 실행하면 인덱스가 만들어집니다."
        )
    return result


def drop_and_reinstall(database_url: str, log=print) -> dict:
    """
    !! 테스트 전용 !!  우리 표를 전부 지우고(DROP) 전체 스키마를 다시 만듭니다. 데이터가 모두 사라집니다.
    tests/conftest.py 가 "매 테스트를 빈 DB 에서 시작" 하려고 씁니다. 명령줄(main)에서는 일부러 제공하지 않습니다.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        # 기존 표를 전부 삭제한다 (없으면 그냥 넘어감). CASCADE = 서로 연결된 제약 조건까지 같이 삭제
        conn.execute("DROP TABLE IF EXISTS " + ", ".join(ALL_TABLES) + " CASCADE")
    return apply_schema(database_url, dry_run=False, log=log)


def main() -> int:
    """명령줄에서 실행할 때의 입구. --url / DATABASE_URL 을 읽어 apply_schema 를 부릅니다."""
    parser = argparse.ArgumentParser(description="SOP Studio DB 에 표를 만들거나 최신 모양으로 갱신합니다.")
    parser.add_argument("--url", default="", help="DB 접속 주소. 비우면 DATABASE_URL(또는 PGHOST 등) 환경변수를 씁니다")
    parser.add_argument("--dry-run", action="store_true", help="무엇을 실행할지 보여 주기만 하고 DB 는 건드리지 않습니다")
    args = parser.parse_args()

    database_url = args.url
    if not database_url:
        # config.py 와 같은 규칙으로 주소를 정합니다 (DATABASE_URL → PGHOST 조립 → 로컬 기본값)
        from app.config import get_settings

        database_url = get_settings().database_url

    apply_schema(database_url, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
