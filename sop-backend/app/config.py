"""
설정(config) — 환경변수에서 값을 읽어 오는 곳.

프로그램이 "어느 DB에 붙을지", "HTML 파일이 어디 있는지" 같은 값은
코드에 직접 적지 않고 환경변수(운영체제에 미리 등록해 두는 이름=값 쌍)로 받습니다.
그래야 같은 코드를 개발 PC와 회사 서버(컨테이너)에서 값만 바꿔 쓸 수 있습니다.

읽는 환경변수 (전부 선택. 없으면 괄호 안 기본값)
  [DB 접속 — 셋 중 하나]
  DATABASE_URL        PostgreSQL 접속 주소. 예) postgresql://postgres:postgres@localhost:5432/sop   ← 있으면 이것을 최우선
  PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
                      DATABASE_URL 이 없고 PGHOST 가 있으면 이 다섯 개로 접속 정보를 조립합니다 (회사 DB 접속 방식).
  (둘 다 없음)        로컬 개발용 기본값 postgresql://postgres:postgres@localhost:5432/sop

  [SSL 접속] 회사 DB 가 암호화 접속(SSL)을 요구하면 ("no pg_hba.conf entry ... SSL off" 오류가 나면)
  DATABASE_URL 끝에 ?sslmode=require 를 붙이거나, 환경변수 PGSSLMODE=require 를 함께 줍니다.
  (PGSSLMODE / PGSSLROOTCERT 는 DB 드라이버(libpq)가 스스로 읽는 표준 환경변수라 코드에서 따로 다루지 않습니다)

  [DB 세부]
  DB_SESSION_OPTIONS  연결마다 붙이는 세션 옵션. 기본 "-c timezone=UTC".
                      빈 문자열("")이면 options 를 아예 넘기지 않습니다 (PgBouncer 처럼 options 를 거부하는 중계기 대응).
  DB_CONNECT_TIMEOUT  DB 접속을 기다리는 최대 시간(초). 기본 30
  DB_POOL_MIN / DB_POOL_MAX   연결 풀 크기(최소/최대). 기본 2 / 10
  DB_PREPARE_THRESHOLD  같은 SQL 을 몇 번 실행하면 DB 서버에 "미리 준비된 문장(prepared statement)" 으로 등록할지. 기본 5.
                      PgBouncer(트랜잭션 풀링) 뒤에서는 이 기능이 깨질 수 있으니 0 또는 빈 문자열로 두면 아예 쓰지 않습니다.

  [서버]
  ROOT_PATH           서버가 경로 접두어 뒤에서 돌 때 그 접두어. 예) /sop  (기본 "" = 접두어 없음)
  USER_HEADER         사용자 이름이 들어오는 HTTP 헤더 이름. 기본 X-User (회사 SSO 프록시가 넣어 주는 헤더 이름으로 바꿔 씀)
  LOG_LEVEL           로그 상세 정도. DEBUG / INFO / WARNING / ERROR. 기본 INFO
  CORS_ORIGINS        HTML을 다른 서버에서 띄울 때 허용할 주소 목록(쉼표 구분). 같은 서버면 비워 둡니다.
  STATIC_DIR          편집기 HTML 파일이 들어 있는 폴더. 기본 static
  STATIC_INDEX        그 폴더 안에서 "/" 로 접속했을 때 보여 줄 파일 이름. 기본 SOP_STUDIO.html
  MAX_CONTENT_MB      저장 요청 한 건의 최대 크기(MB). 기본 20 (이미지가 base64로 들어가서 큽니다)
"""

import os

from psycopg.conninfo import conninfo_to_dict, make_conninfo

LOCAL_DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/sop"


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 읽습니다. 비어 있거나 숫자가 아니면 기본값."""
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_prepare_threshold() -> int | None:
    """
    DB_PREPARE_THRESHOLD 를 읽습니다. 환경변수가 없으면 5(psycopg 기본값).
    빈 문자열이나 0 이면 None = "미리 준비된 문장을 절대 만들지 않음" (PgBouncer 트랜잭션 풀링 대응).
    """
    raw = os.environ.get("DB_PREPARE_THRESHOLD", "5").strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        return 5
    return None if value <= 0 else value


def resolve_database_url() -> str:
    """
    DB 접속 정보를 정합니다. 우선순위: DATABASE_URL → PGHOST 등 다섯 개로 조립 → 로컬 기본값.
    조립할 때는 psycopg 의 make_conninfo 를 써서 "host=... port=... user=... password=... dbname=..." 형식으로 만듭니다.
    (비밀번호에 @ 나 / 같은 특수문자가 있어도 URL 처럼 따로 인코딩할 필요가 없어 안전합니다)
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    host = os.environ.get("PGHOST", "").strip()
    if host:
        parts = {
            "host": host,
            "port": os.environ.get("PGPORT", "5432").strip() or "5432",
            "user": os.environ.get("PGUSER", "postgres").strip() or "postgres",
            "dbname": os.environ.get("PGDATABASE", "sop").strip() or "sop",
        }
        password = os.environ.get("PGPASSWORD", "")
        if password:
            parts["password"] = password
        return make_conninfo(**parts)

    return LOCAL_DEFAULT_URL


def mask_password(conninfo: str) -> str:
    """접속 정보에서 비밀번호만 **** 로 가려 돌려줍니다. 로그에 찍을 때 씁니다."""
    try:
        parts = conninfo_to_dict(conninfo)
    except Exception:
        return "(접속 정보 형식을 읽지 못함)"
    if "password" in parts:
        parts["password"] = "****"
    return make_conninfo(**parts)


class Settings:
    """환경변수 값을 한 상자에 모아 둔 것. get_settings()로 만들어 씁니다."""

    def __init__(self) -> None:
        # DB 접속 정보 (위 resolve_database_url 규칙)
        self.database_url: str = resolve_database_url()

        # 연결마다 붙이는 세션 옵션. 환경변수가 "아예 없으면" 기본값, "빈 문자열이면" 옵션 없음.
        self.db_session_options: str = os.environ.get("DB_SESSION_OPTIONS", "-c timezone=UTC").strip()

        # DB 접속 대기 시간(초)과 연결 풀 크기
        self.db_connect_timeout: int = _env_int("DB_CONNECT_TIMEOUT", 30)
        self.pool_min_size: int = _env_int("DB_POOL_MIN", 2)
        self.pool_max_size: int = _env_int("DB_POOL_MAX", 10)

        # 같은 SQL 을 몇 번 실행하면 서버측 prepared statement 로 등록할지. None 이면 안 씀 (PgBouncer 대응)
        self.db_prepare_threshold: int | None = _env_prepare_threshold()

        # 서버가 "/sop" 같은 접두어 뒤에서 돌 때. 앞에 / 는 붙이고 끝의 / 는 뗍니다.
        root_path = os.environ.get("ROOT_PATH", "").strip().rstrip("/")
        if root_path and not root_path.startswith("/"):
            root_path = "/" + root_path
        self.root_path: str = root_path

        # 사용자 이름을 담아 오는 헤더 이름 (SSO 프록시가 넣어 줌)
        self.user_header: str = os.environ.get("USER_HEADER", "X-User").strip() or "X-User"

        # 로그 상세 정도
        self.log_level: str = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"

        # "http://a.com, http://b.com" 처럼 쉼표로 나열된 문자열을 리스트로 바꿉니다.
        raw_origins = os.environ.get("CORS_ORIGINS", "")
        self.cors_origins: list[str] = [o.strip() for o in raw_origins.split(",") if o.strip()]

        # 정적 파일(편집기 HTML) 위치
        self.static_dir: str = os.environ.get("STATIC_DIR", "static")
        self.static_index: str = os.environ.get("STATIC_INDEX", "SOP_STUDIO.html")

        # 저장 요청 최대 크기. MB 단위 환경변수를 바이트로 바꿔 둡니다.
        self.max_content_bytes: int = _env_int("MAX_CONTENT_MB", 20) * 1024 * 1024


def get_settings() -> Settings:
    """부를 때마다 환경변수를 새로 읽습니다. (테스트에서 값을 바꿔 끼우기 쉽게 하려고 이렇게 합니다)"""
    return Settings()
