# ---------------------------------------------------------------------
# Dockerfile — API 서버를 "컨테이너 이미지" 로 포장하는 설명서
#
# 컨테이너 = 파이썬, 라이브러리, 우리 코드가 전부 들어 있는 작은 상자.
# 어느 컴퓨터(회사 HCP 컨테이너 플랫폼 포함)에서 실행해도 똑같이 동작합니다.
#
# 회사 환경 대응 포인트
#   - 비루트(root 아님) 사용자로 실행 (보안 규정)
#   - 포트는 PORT 환경변수로 (플랫폼이 정해 주는 포트를 그대로 씀. 기본 8000)
#   - 앞단 프록시(SSO/로드밸런서)가 붙이는 X-Forwarded-* 헤더를 믿도록 --proxy-headers
#   - HEALTHCHECK 로 /health (DB 를 보지 않는 생존 확인) 를 주기적으로 두드림
#   - DB 접속 정보는 이미지에 넣지 않고 환경변수(DATABASE_URL 또는 PGHOST 등)로 받음
# ---------------------------------------------------------------------

# 1. 파이썬 3.12 가 설치된 가벼운(slim) 리눅스에서 시작
FROM python:3.12-slim

# 2. 상자 안에서 작업할 폴더
WORKDIR /srv

# 3. 라이브러리 먼저 설치 (코드보다 덜 바뀌므로 먼저 두면 다시 빌드할 때 빠릅니다)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 우리 코드와 편집기 HTML, DB 스키마·마이그레이션 복사
COPY app/ ./app/
COPY static/ ./static/
COPY sql/ ./sql/

# 5. 비루트 사용자 만들기 → 파일 주인을 그 사용자로 → 그 사용자로 전환
RUN useradd --create-home --uid 10001 app && chown -R app:app /srv
USER app

# 6. 서버가 쓰는 포트. 플랫폼이 PORT 환경변수를 주면 그 값을, 없으면 8000 을 씁니다.
#    PYTHONUNBUFFERED=1: 파이썬이 로그를 모아 뒀다 한꺼번에 내보내지 않고 바로바로 출력하게 합니다.
#    (컨테이너 로그 수집기가 실시간으로 볼 수 있고, 갑자기 꺼져도 마지막 로그가 사라지지 않습니다)
ENV PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

# 7. 생존 확인: 30초마다 /health 를 열어 봅니다. 3번 연속 실패하면 플랫폼이 상자를 다시 켭니다.
#    (curl 이 없는 slim 이미지라 파이썬으로 확인)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health', timeout=4)" || exit 1

# 8. 상자가 켜지면 실행할 명령: uvicorn 으로 FastAPI 앱 띄우기
#    --host 0.0.0.0            상자 바깥에서도 접속 허용
#    --proxy-headers           앞단 프록시가 넣는 X-Forwarded-For / X-Forwarded-Proto 를 믿음 (https 주소 계산 등)
#    --forwarded-allow-ips="*" 어느 프록시에서 오든 그 헤더를 믿음 (컨테이너 앞은 항상 회사 프록시이므로)
#    (sh -c 를 쓰는 이유: ${PORT} 환경변수를 실행 시점에 풀어 넣기 위해)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
