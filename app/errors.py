"""
오류 형식 — 모든 오류 응답을 같은 모양으로 맞추는 곳.

프론트가 오류를 한 가지 방식으로만 처리하면 되도록, 어떤 오류든 아래 모양으로 내려보냅니다.

    { "error": { "code": "version_conflict", "message": "다른 사람이 먼저 저장했습니다", ...추가정보 } }

  code    : 프로그램이 구분하는 짧은 영문 이름 (if 문으로 비교하는 용도)
  message : 사람이 읽는 설명
  추가정보 : 필요할 때만 (예: current_version_no, locked_by, expires_at)

자주 쓰는 상태 코드
  400 잘못된 요청   404 없음   409 충돌(버전)   413 너무 큼   422 형식 오류   423 잠김   500 서버 오류
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
# FastAPI 는 Starlette 위에서 돕니다. "없는 주소(404)" 같은 오류는 Starlette 층에서 만들어지므로
# fastapi.HTTPException 이 아니라 그 부모인 Starlette 의 HTTPException 을 잡아야 전부 걸립니다.
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("sop")


class ApiError(Exception):
    """
    API 함수 안에서 `raise ApiError(404, "not_found", "문서가 없습니다")` 처럼 던지면
    아래 install_error_handlers() 가 받아서 정해진 JSON 모양으로 바꿔 줍니다.
    추가 정보는 키워드 인자로 넘깁니다:  ApiError(409, "version_conflict", "...", current_version_no=4)
    """

    def __init__(self, status_code: int, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra


def error_body(code: str, message: str, **extra) -> dict:
    """오류 JSON 본문을 만듭니다. {"error": {"code": ..., "message": ..., 추가정보}}"""
    body = {"code": code, "message": message}
    body.update(extra)
    return {"error": body}


def install_error_handlers(app: FastAPI) -> None:
    """앱에 오류 처리기를 등록합니다. main.py 에서 한 번 호출."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        # 우리가 의도적으로 던진 오류. 그대로 JSON으로.
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, **exc.extra))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # 요청 JSON이 pydantic 모델 형식과 다를 때 (필드 누락, 타입 불일치 등)
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "요청 형식이 올바르지 않습니다.", details=exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        # 없는 주소(404), 허용 안 된 메서드(405) 등 프레임워크가 만드는 오류도 같은 모양으로.
        # (Starlette 의 클래스를 잡는 이유는 위 import 주석 참고)
        return JSONResponse(status_code=exc.status_code, content=error_body("http_error", str(exc.detail)))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        # 예상 못 한 오류. 자세한 내용은 서버 로그에만 남기고, 응답에는 스택트레이스를 넣지 않습니다.
        log.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=error_body("internal_error", "서버 내부 오류가 발생했습니다."))
