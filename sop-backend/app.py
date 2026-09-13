"""
app.py — 서버를 켜는 "시작 버튼".

이 파일이 하는 일은 딱 하나입니다: app/main.py 에 만들어 둔 FastAPI 앱을 uvicorn(웹 서버 프로그램)에 실어서 켭니다.
회사 컨테이너 규칙이 "python app.py 로 시작" 이라서, 그 규칙에 맞춘 파일입니다 (Dockerfile 의 ENTRYPOINT 가 이 파일을 부릅니다).

켜는 법
    python app.py                       → http://localhost:8000
    PORT=9000 python app.py             → 포트 바꾸기 (컨테이너 플랫폼이 PORT 를 정해 주면 그대로 씀)

이 파일은 DB 를 만들지 않습니다. DB 접속 주소는 환경변수(DATABASE_URL 등, app/config.py 참고)로 받고,
표(테이블)는 배포 전에 `python -m app.tools.apply_schema` 로 한 번 만들어 두어야 합니다.
(내 PC 에서 DB 없이 그냥 띄워 보려면 app/tools/dev_server.py 를 쓰세요 — 내장 PostgreSQL 까지 같이 켜 줍니다)

※ 파일 이름이 app.py 이고 폴더 이름도 app/ 이라 헷갈릴 수 있는데, 파이썬은 "from app.main import ..." 에서
   같은 이름이 있으면 폴더(app/)를 먼저 찾으므로 문제없이 동작합니다.
"""

import asyncio
import os
import sys

import uvicorn


def main() -> None:
    """환경변수를 읽어 uvicorn 을 켭니다. 이 함수가 끝나는 순간(Ctrl+C 등) 서버도 꺼집니다."""

    # 윈도우 전용 처리: 윈도우의 기본 비동기 방식(Proactor)은 DB 드라이버(psycopg)와 맞지 않아 접속이 실패합니다.
    # 그래서 윈도우에서만 "Selector" 방식으로 바꿔 줍니다. 맥·리눅스에서는 이 줄이 그냥 지나갑니다.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 포트: 환경변수 PORT 가 있으면 그 값, 없으면 8000. (컨테이너 플랫폼은 보통 PORT 를 정해서 넣어 줍니다)
    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        "app.main:app",             # "app/main.py 파일 안의 app 이라는 변수" 를 켜라
        host="0.0.0.0",             # 0.0.0.0 = 이 컴퓨터로 오는 모든 접속을 받음 (컨테이너 바깥에서도 접속 가능)
        port=port,
        proxy_headers=True,         # 앞단 프록시(SSO/로드밸런서)가 붙이는 X-Forwarded-* 헤더를 믿음 (https 주소 계산 등)
        forwarded_allow_ips="*",    # 어느 프록시에서 오든 그 헤더를 믿음 (컨테이너 앞은 항상 회사 프록시이므로)
    )


# "이 파일을 직접 실행했을 때만" 아래가 실행됩니다. 다른 파일이 import 해서 가져다 쓸 때는 실행되지 않습니다.
if __name__ == "__main__":
    main()
