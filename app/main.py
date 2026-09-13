"""
main.py — FastAPI 앱의 출발점.

여기서 하는 일
  1. 앱이 켜지고 꺼질 때 DB 연결 풀을 열고 닫는다 (lifespan)
  2. 요청마다 로그 한 줄을 남기고, 너무 큰 요청은 413으로 막는다 (middleware)
  3. 오류를 한 가지 JSON 모양으로 통일한다 (errors.py)
  4. 기능별 라우터(routers/*.py)를 /api 아래에 붙인다. 모든 API 는 current_user 를 거친다 (API_GUARD)
  5. /health (DB 안 봄, 플랫폼 생존 확인용) 와 /api/health (DB 확인. DB 가 죽어 있으면 503) 를 둔다
  6. 편집기 HTML 파일을 "/" 에서 보여 준다. 이때 <head> 바로 뒤에 <meta name="api-base" content="{ROOT_PATH}"> 를
     끼워 넣어, 서버가 "/sop" 같은 접두어 뒤에서 돌 때 프론트가 API 주소 앞에 그 접두어를 붙일 수 있게 한다.

실행:  uvicorn app.main:app --reload
회사 환경: ROOT_PATH=/sop 처럼 접두어 뒤에서 돌 때는 환경변수만 주면 됩니다 (config.py 참고).
"""

import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import get_settings, mask_password
from app.deps import current_user
from app.errors import error_body, install_error_handlers
from app.routers import locks, sops, versions
from app.schemas import HealthResponse

# 앱을 만들 때 한 번 읽는 값(_settings). 요청을 처리하는 함수 안에서는 get_settings() 를 다시 불러서
# 테스트가 환경변수를 바꿔 끼울 수 있게 합니다 (config.py 의 get_settings 설명 참고).
_settings = get_settings()

# 로그 형식: 시각 / 레벨 / 내용.  상세 정도는 LOG_LEVEL 환경변수 (기본 INFO)
# stream=sys.stdout: 컨테이너 플랫폼은 보통 "표준 출력(stdout)" 만 모으므로 로그를 그쪽으로 보냅니다.
# (기본값은 stderr 라서, 플랫폼에 따라 INFO 로그가 전부 "오류" 로 분류되거나 빠질 수 있습니다)
logging.basicConfig(level=_settings.log_level, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("sop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 전에 DB 풀을 열고, 앱이 끝날 때 닫습니다. (yield 앞 = 시작, 뒤 = 종료)"""
    settings = get_settings()
    log.info("DB 연결 중: %s", mask_password(settings.database_url))
    await db.open_pool(
        settings.database_url,
        settings.pool_min_size,
        settings.pool_max_size,
        session_options=settings.db_session_options,
        connect_timeout=settings.db_connect_timeout,
        prepare_threshold=settings.db_prepare_threshold,
    )
    log.info("DB pool opened")
    yield
    await db.close_pool()
    log.info("DB pool closed")


# root_path: 서버가 "/sop" 같은 접두어 뒤에서 돌 때 문서(/docs)와 주소 계산이 맞도록 알려 줍니다.
app = FastAPI(title="SOP Studio API", version="2.0", lifespan=lifespan, root_path=_settings.root_path)

# 오류 응답 모양 통일
install_error_handlers(app)


@app.middleware("http")
async def log_and_limit(request: Request, call_next):
    """
    모든 요청이 거쳐 가는 문.
    - 요청 본문이 너무 크면(기본 20MB) 바로 413 으로 돌려보냅니다.
    - 처리가 끝나면 "메서드 경로 상태코드 걸린시간" 한 줄을 로그로 남깁니다.
      413 으로 돌려보낸 요청도, 처리 중 예외로 끝난(500) 요청도 같은 형식으로 한 줄 남깁니다.
    """
    started = time.perf_counter()
    max_bytes = get_settings().max_content_bytes
    content_length = request.headers.get("content-length")
    # Content-Length 헤더가 있고, 숫자이고, 한도를 넘으면 → 본문을 읽지도 않고 413
    if content_length is not None and content_length.isdigit() and int(content_length) > max_bytes:
        log.info("%s %s -> 413 (본문 %s bytes 가 한도 %s bytes 초과)", request.method, request.url.path, content_length, max_bytes)
        return JSONResponse(
            status_code=413,
            content=error_body("payload_too_large", f"요청이 너무 큽니다. 최대 {max_bytes // (1024 * 1024)}MB 까지 저장할 수 있습니다."),
        )

    status = "ERROR"   # 예외로 끝나면 이 글자가 남는다 (예외 내용은 errors.py 가 따로 기록)
    try:
        # call_next = 다음 단계(실제 API 함수)로 넘기고 응답을 받아 온다
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, status, elapsed_ms)


# HTML을 다른 서버(다른 주소)에서 띄울 때만 필요. 같은 서버에서 서빙하면 CORS_ORIGINS 를 비워 두면 됩니다.
# 위치가 중요: Starlette 은 "나중에 등록한 미들웨어가 가장 바깥" 이라서, CORS 를 log_and_limit 뒤에 등록해야
# 413 처럼 안쪽에서 바로 돌려보내는 응답에도 CORS 헤더가 붙습니다 (안 붙으면 브라우저가 오류 내용을 못 읽음).
if _settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# 기능별 라우터를 /api 아래에 붙입니다.
# API_GUARD: 모든 API 가 current_user(deps.py)를 거칩니다. 지금은 사용자 이름을 읽기만 하지만,
# 나중에 그 함수에서 "허용되지 않은 사용자면 401" 을 던지면 API 전체가 한 번에 보호됩니다.
API_GUARD = [Depends(current_user)]
app.include_router(sops.router, prefix="/api", dependencies=API_GUARD)
app.include_router(versions.router, prefix="/api", dependencies=API_GUARD)
app.include_router(locks.router, prefix="/api", dependencies=API_GUARD)


@app.get("/health", include_in_schema=False)   # include_in_schema=False: /docs 목록에서 숨김
async def liveness():
    """
    "프로세스가 살아 있는가" 만 답합니다. DB 는 보지 않습니다 (항상 {"ok": true}).
    컨테이너 플랫폼(HCP)이 몇 초마다 두드려 보는 용도라, DB 가 잠깐 흔들려도 서버를 죽이지 않게 하려는 것입니다.
    """
    return {"ok": True}


@app.get("/api/health", response_model=HealthResponse)
async def health(response: Response):
    """
    서버와 DB가 살아 있는지 확인. 배포 후 제일 먼저 열어 보는 주소.
    둘 다 정상이면 200 {"ok": true, "db": "up"}, DB 만 죽어 있으면 503 {"ok": false, "db": "down"}.
    (상태 코드만 보는 모니터링 도구나 curl -f 도 DB 장애를 알아챌 수 있게 503 으로)
    """
    alive = await db.ping()
    if not alive:
        response.status_code = 503
    return HealthResponse(ok=alive, db="up" if alive else "down")


# ---------------------------------------------------------------------
# 정적 파일: 편집기 HTML
# ---------------------------------------------------------------------
# <head> 여는 태그를 찾는 규칙 (대소문자 무시, <head lang="ko"> 처럼 속성이 있어도 됨. <header> 는 아님)
_HEAD_TAG = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)


def inject_api_base(html: str, root_path: str) -> str:
    """
    편집기 HTML 의 <head> 바로 뒤에 <meta name="api-base" content="{ROOT_PATH}"> 한 줄을 끼워 넣습니다.
    프론트의 apiUrl() 헬퍼가 이 meta 를 읽어 모든 API 주소 앞에 접두어를 붙입니다 (ROOT_PATH 가 비어 있으면 content="").
    <head> 가 없는 이상한 파일이면 맨 앞에 붙입니다.
    """
    meta = f'<meta name="api-base" content="{root_path}">'
    match = _HEAD_TAG.search(html)
    if match is None:
        return meta + html
    end = match.end()
    return html[:end] + meta + html[end:]


@app.get("/", include_in_schema=False)
async def index():
    """브라우저에서 http://서버주소/ 로 들어오면 편집기 HTML 을 보여 줍니다 (api-base meta 를 끼워 넣어서)."""
    settings = get_settings()
    path = os.path.join(settings.static_dir, settings.static_index)
    if not os.path.isfile(path):
        return JSONResponse(
            status_code=404,
            content=error_body("static_not_found", f"편집기 파일이 없습니다: {path}  (STATIC_DIR / STATIC_INDEX 환경변수 확인)"),
        )
    with open(path, encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(inject_api_base(html, settings.root_path))


# static 폴더에 다른 파일(이미지 등)이 생기면 /static/파일이름 으로 접근할 수 있습니다.
if os.path.isdir(_settings.static_dir):
    app.mount("/static", StaticFiles(directory=_settings.static_dir), name="static")
