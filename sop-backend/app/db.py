"""
DB 연결 — PostgreSQL에 붙는 통로를 관리하는 곳.

용어
  연결(connection): 프로그램과 DB 사이의 전화선 하나. 열고 닫는 데 시간이 걸립니다.
  풀(pool): 전화선을 미리 몇 개 열어 두고 빌려 쓰는 창구. 요청마다 새로 열지 않아 빠릅니다.
  트랜잭션(transaction): "이 작업들은 한 묶음이다" 라는 약속.
      묶음 안의 작업이 전부 성공하면 함께 저장(commit)되고, 하나라도 실패하면 전부 취소(rollback)됩니다.

사용법
  - 앱이 켜질 때 open_pool(), 꺼질 때 close_pool() 을 부릅니다. (main.py의 lifespan)
  - 각 API 함수는 `conn = Depends(get_conn)` 으로 연결 하나를 빌려 씁니다.
  - 여러 SQL을 한 묶음으로 저장해야 하면  `async with conn.transaction():`  블록 안에서 실행합니다.

회사 환경(외부 PostgreSQL 에 주소로 접속) 대응
  - 풀을 열 때 실제로 접속이 될 때까지 기다리고(wait=True), 정해진 시간(timeout) 안에 안 되면 원인을 한국어로 남기고 실패합니다.
    (접속이 안 되는데 서버만 켜져 있으면 요청마다 500 이 나서 원인을 찾기 어렵기 때문)
  - 빌려 줄 때마다 연결이 살아 있는지 확인(check)해서 끊긴 연결은 자동으로 새것으로 바꿉니다.
    (방화벽/중계기가 오래 놀고 있는 연결을 끊어 버리는 경우 대응)
  - 연결 하나를 너무 오래 쓰지 않도록 수명(max_lifetime)을 두어 주기적으로 새로 맺습니다.
  - PgBouncer(트랜잭션 풀링) 뒤에서는 "미리 준비된 문장(prepared statement)" 이 깨질 수 있어서
    DB_PREPARE_THRESHOLD=0 으로 끌 수 있게 했습니다 (prepare_threshold=None 으로 전달).
"""

import logging
from typing import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.config import mask_password

log = logging.getLogger("sop")

# 풀은 앱 전체에 하나만 있으면 됩니다. open_pool() 이 채워 넣습니다.
pool: AsyncConnectionPool | None = None

# 연결 하나의 최대 수명(초). 이 시간이 지나면 풀이 그 연결을 닫고 새로 맺습니다. 30분.
MAX_LIFETIME_SEC = 30 * 60


async def open_pool(
    database_url: str,
    min_size: int = 2,
    max_size: int = 10,
    session_options: str = "-c timezone=UTC",
    connect_timeout: int = 30,
    prepare_threshold: int | None = 5,
) -> None:
    """
    앱 시작 시 한 번 호출. DB 연결 풀을 만들고, 실제로 접속이 될 때까지(최대 connect_timeout 초) 기다립니다.
    session_options 가 빈 문자열이면 options 를 넘기지 않습니다 (PgBouncer 등 options 를 거부하는 중계기 대응).
    prepare_threshold 가 None 이면 서버측 prepared statement 를 만들지 않습니다 (PgBouncer 트랜잭션 풀링 대응).
    """
    global pool   # global: 이 함수 밖(파일 전체)의 pool 변수를 바꾸겠다는 선언

    # 연결 하나를 만들 때 psycopg 에 넘길 설정들
    conn_kwargs = {
        # 조회 결과를 튜플 대신 {"컬럼이름": 값} 딕셔너리로 받습니다. 코드 읽기가 훨씬 쉽습니다.
        "row_factory": dict_row,
        # 기본은 "자동 저장" 모드. 여러 SQL을 묶어야 할 때만 conn.transaction() 을 씁니다.
        "autocommit": True,
        # 접속 한 번을 기다리는 최대 시간(초). 서버가 없으면 이 시간 뒤에 포기합니다.
        "connect_timeout": connect_timeout,
        # 같은 SQL 을 이 횟수만큼 실행하면 DB 서버에 "미리 준비된 문장" 으로 등록해 조금 빨라집니다 (기본 5).
        # PgBouncer 트랜잭션 풀링에서는 연결이 요청마다 바뀌어 그 등록이 사라지므로("prepared statement ... does not exist"),
        # 그런 환경에서는 None(= 등록 안 함)으로 둡니다. 환경변수 DB_PREPARE_THRESHOLD=0.
        "prepare_threshold": prepare_threshold,
    }
    if session_options:
        # 시각(saved_at 등)을 항상 UTC 기준으로 받기 위한 세션 옵션 (기본값). 빈 값이면 아예 넘기지 않음.
        conn_kwargs["options"] = session_options

    pool = AsyncConnectionPool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        open=False,  # 만들자마자 열지 않고 아래에서 명시적으로 엽니다.
        kwargs=conn_kwargs,
        # 연결을 빌려 줄 때마다 살아 있는지 확인. 끊겨 있으면 버리고 새 연결을 줍니다.
        check=AsyncConnectionPool.check_connection,
        # 연결 하나의 최대 수명(초). 오래된 연결은 조용히 새것으로 교체됩니다.
        max_lifetime=MAX_LIFETIME_SEC,
        # 풀에서 연결을 빌릴 때 기다리는 최대 시간(초)
        timeout=connect_timeout,
    )
    try:
        # wait=True: 최소 개수(min_size)만큼 실제로 접속이 될 때까지 기다립니다. 안 되면 PoolTimeout.
        await pool.open(wait=True, timeout=connect_timeout)
    except PoolTimeout as error:
        log.error(
            "DB 에 %d초 안에 접속하지 못했습니다. 접속 정보: %s\n"
            "  확인할 것: 1) DATABASE_URL(또는 PGHOST 등) 값  2) DB 서버가 켜져 있는지  3) 방화벽/포트  4) 계정·비밀번호\n"
            "  원인: %s",
            connect_timeout, mask_password(database_url), error,
        )
        await pool.close()
        pool = None
        raise
    except Exception as error:
        log.error("DB 연결 풀을 열지 못했습니다. 접속 정보: %s  원인: %s", mask_password(database_url), error)
        await pool.close()
        pool = None
        raise


async def close_pool() -> None:
    """앱 종료 시 한 번 호출. 열어 둔 연결을 모두 정리합니다."""
    global pool   # 파일 전체의 pool 변수를 None 으로 되돌리기 위해
    if pool is not None:
        await pool.close()
        pool = None


async def get_conn() -> AsyncIterator[AsyncConnection]:
    """
    FastAPI 의존성(Depends). API 함수 하나가 실행되는 동안 연결 하나를 빌려 주고,
    끝나면 자동으로 풀에 되돌려 줍니다.
    """
    if pool is None:
        raise RuntimeError("DB 풀이 아직 열리지 않았습니다. open_pool() 을 먼저 호출하세요.")
    async with pool.connection() as conn:
        # yield: 여기서 conn 을 빌려 주고 멈췄다가, API 함수가 끝나면 이어서 아래로 내려와 반납합니다(with 블록 종료)
        yield conn


async def ping() -> bool:
    """DB가 살아 있는지 확인. /api/health 에서 씁니다."""
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
