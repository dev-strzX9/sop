"""
dev_server.py — 도커도, PostgreSQL 설치도 없이 내 PC 에서 바로 서버를 띄우는 도구.

어떻게 가능한가요?
  파이썬 라이브러리 `pgserver` 안에 PostgreSQL 실행 파일이 통째로 들어 있습니다.
  이 스크립트는 그 내장 PostgreSQL 을 이 폴더 안(pgdata/)에 띄우고, 테이블을 만들고,
  그 DB 에 연결된 API 서버(uvicorn)를 켭니다. 끄면 DB 도 같이 멈추지만 데이터는 pgdata/ 에 남습니다.

실행
  pip install -r requirements-dev.txt        (pgserver 가 여기에 들어 있습니다)
  python -m app.tools.dev_server             → http://localhost:8000
  python -m app.tools.dev_server --port 9000 --reload

주의
  - 내장 PostgreSQL 에는 pg_trgm(부분 검색 인덱스용 확장)이 없어서, 그 인덱스 2개만 빼고 테이블을 만듭니다.
    검색 기능은 똑같이 동작하고 속도만 조금 다릅니다. 실제 운영은 docker compose 나 정식 PostgreSQL 을 쓰세요.
  - 이미 만들어진 pgdata/ 로 켜면 sql/migrations/ 의 갱신 파일이 자동으로 적용됩니다 (데이터는 그대로).
  - 데이터를 완전히 지우고 새로 시작하려면 서버를 끄고 pgdata/ 폴더를 삭제하면 됩니다.
"""

import argparse
import asyncio
import json
import os
import sys

# psutil: "이 번호의 프로그램이 살아 있나" 를 안전하게 물어보는 라이브러리 (pgserver 가 함께 설치해 줍니다)
import psutil

# 표 만들기/갱신은 apply_schema.py 가 담당합니다 (테스트도 같은 함수를 씀). 여기서는 그냥 빌려 씁니다.
from app.tools.apply_schema import PROJECT_ROOT, apply_schema


def prune_dead_handles(data_dir: str) -> None:
    """
    내장 PostgreSQL 을 "지금 몇 명이 쓰고 있는지" 적어 두는 명단(.handle_pids.json)에서
    이미 죽어 버린 프로그램의 번호를 지웁니다.

    왜 필요한가요?
      서버가 정상 종료되지 않으면(터미널을 그냥 닫거나, 강제 종료되거나) 자기 번호를 명단에서
      못 지우고 사라집니다. 그러면 다음에 Ctrl+C 로 얌전히 껐을 때도 PostgreSQL 이
      "아직 쓰는 사람이 남아 있네" 하고 계속 켜져 있게 됩니다. 그래서 시작할 때 한 번
      명단을 훑어서, 실제로는 없는 번호를 미리 치워 둡니다.
    """
    path = os.path.join(data_dir, ".handle_pids.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            pids = json.load(f)
        alive = []
        for pid in pids:
            # psutil.pid_exists = 그 번호의 프로그램이 지금 돌고 있으면 True.
            # (예전에는 os.kill(pid, 0) 으로 확인했는데, 윈도우에서는 그 명령이 "확인" 이 아니라 진짜로 프로그램을
            #  꺼 버리므로 절대 쓰면 안 됩니다. psutil 은 어느 운영체제에서나 안전하게 확인만 합니다)
            try:
                if psutil.pid_exists(int(pid)):
                    alive.append(pid)
            except (ValueError, TypeError):
                pass  # 번호가 아닌 이상한 값은 명단에서 뺍니다
        if len(alive) != len(pids):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(alive, f)
            print(f"[dev] 이전에 비정상 종료된 기록 {len(pids) - len(alive)}건을 정리했습니다.")
    except (OSError, ValueError):
        # 명단 파일이 깨져 있어도 서버는 떠야 하므로 조용히 넘어갑니다.
        pass


def main() -> int:
    """명령줄 옵션을 읽고 → 내장 PostgreSQL 켜기 → 스키마 적용 → API 서버 켜기 순서로 진행합니다."""
    parser = argparse.ArgumentParser(description="도커/PostgreSQL 설치 없이 SOP Studio 서버를 띄웁니다.")
    parser.add_argument("--port", type=int, default=8000, help="서버 포트 (기본 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="접속 허용 주소 (다른 PC 에서 접속하려면 0.0.0.0)")
    parser.add_argument("--data-dir", default=os.path.join(PROJECT_ROOT, "pgdata"), help="DB 데이터를 둘 폴더")
    parser.add_argument("--reload", action="store_true", help="코드를 고치면 서버를 자동으로 다시 켭니다 (개발용)")
    args = parser.parse_args()

    try:
        import pgserver
        import uvicorn
    except ImportError as e:
        print(f"필요한 라이브러리가 없습니다: {e.name}")
        print("이 폴더에서  pip install -r requirements-dev.txt  를 먼저 실행하세요.")
        return 1

    # 1. 내장 PostgreSQL 켜기. 처음 실행하면 data_dir 에 DB 파일을 만드느라 몇 초 걸립니다.
    print(f"[dev] 내장 PostgreSQL 시작 중... (데이터 폴더: {args.data_dir})")
    prune_dead_handles(args.data_dir)
    pg = pgserver.get_server(args.data_dir)
    database_url = pg.get_uri()
    print(f"[dev] DB 접속 주소: {database_url}")

    # 2. 테이블 만들기 (처음이면 전체 스키마, 이미 있으면 sql/migrations/ 의 갱신 파일만. 데이터는 지우지 않음)
    apply_schema(database_url, log=lambda message: print("[dev] " + message))

    # 3. API 서버가 이 DB 를 쓰도록 환경변수로 알려 주고 켭니다. (config.py 가 DATABASE_URL 을 읽습니다)
    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("STATIC_DIR", os.path.join(PROJECT_ROOT, "static"))
    print(f"[dev] 브라우저에서 열기: http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
    print("[dev] 끄려면 Ctrl+C")
    # 윈도우 전용: 기본 비동기 방식(Proactor)이 DB 드라이버(psycopg)와 맞지 않아 Selector 방식으로 바꿉니다. (app.py 와 같은 처리)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    finally:
        # 4. 서버가 끝나면 내장 PostgreSQL 도 정리합니다 (데이터 파일은 남습니다).
        pg.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
