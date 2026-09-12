"""
공용 의존성(dependency) — 여러 API 함수가 똑같이 필요로 하는 것.

지금은 "지금 요청을 보낸 사용자가 누구인지" 하나뿐입니다.
아직 로그인 기능이 없어서 요청 헤더(기본 `X-User: hong`) 값을 그대로 믿고, 없으면 "anonymous" 로 봅니다.

회사 환경에서는 앞단의 SSO 프록시가 사용자 이름을 헤더에 넣어 줍니다. 그 헤더 이름이 X-User 가 아니면
환경변수 USER_HEADER 로 바꿔 줄 수 있습니다 (config.py 참고). 진짜 인증을 붙일 때도 이 함수 하나만 바꾸면 됩니다.

한글 이름 처리
  HTTP 헤더에는 원칙적으로 영문·숫자만 실을 수 있어서, 브라우저(프론트)는 "홍길동" 같은 이름을
  encodeURIComponent 로 "%ED%99%8D..." 처럼 바꿔서 보냅니다. 서버는 그것을 다시 "홍길동" 으로 되돌려 씁니다(unquote).
  영문 이름은 바뀌는 글자가 없어서 그대로입니다. 잠금·저장 기록에는 되돌린 원래 이름이 들어갑니다.

main.py 는 모든 라우터에 이 함수를 dependencies 로 걸어 둡니다(API_GUARD). 지금은 "누구인지 읽기" 만 하지만,
나중에 "허용된 사용자가 아니면 401" 을 여기서 던지면 모든 API 가 한 번에 보호됩니다.
"""

from urllib.parse import unquote

from fastapi import Request

from app.config import get_settings


def current_user(request: Request) -> str:
    """요청 헤더(USER_HEADER, 기본 X-User)에서 사용자 이름을 꺼냅니다. %인코딩된 한글은 되돌립니다. 비어 있으면 'anonymous'."""
    header_name = get_settings().user_header
    raw = request.headers.get(header_name) or ""
    # unquote: "%ED%99%8D%EA%B8%B8%EB%8F%99" → "홍길동". 영문처럼 % 가 없는 글자는 그대로 나옵니다.
    name = unquote(raw).strip()
    return name or "anonymous"
