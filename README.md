# SOP Studio 서버 — 회사 배포용

SOP Studio 편집기(브라우저)와 그 문서를 PostgreSQL 에 버전 단위로 저장하는 FastAPI 서버입니다.
이 폴더는 **회사 환경(HCP 컨테이너 + 외부 PostgreSQL 17)에서 돌리는 데 필요한 것만** 담았습니다.
내 PC 용 내장 DB(pgserver)·테스트·편집기 소스 도구는 들어 있지 않습니다. (그건 `dev-sop/sop-backend` 에 있음)

```
sop/
  app.py          시작 버튼 — 컨테이너가 켜지면 이 파일이 실행됨 (ENTRYPOINT ["python", "app.py"])
  app/            서버 코드 (FastAPI)
  static/         편집기 화면 (SOP_STUDIO.html — 서버가 "/" 에서 내려줌)
  sql/            DB 표 설계도 (sop_schema.sql) + 변경분 (migrations/)
  requirements.txt  필요한 파이썬 라이브러리 (실행용만)
  Dockerfile      컨테이너 이미지 만드는 설명서
  .env.example    환경변수 견본 (전부 설명 있음)
```

## 1. 배포 순서 (처음 한 번)

1. **DB 준비** — DBA 에게 PostgreSQL 17 에 빈 데이터베이스 하나와 접속 계정을 받습니다.
   (`pg_trgm` 확장이 있으면 검색이 조금 빨라지지만, 없어도 동작합니다.)
2. **표 만들기** — 서버 코드가 있는 곳(컨테이너 안이든, 파이썬이 있는 관리 PC 든)에서 한 번 실행:
   ```bash
   pip install -r requirements.txt
   export DATABASE_URL='postgresql://계정:비밀번호@DB주소:5432/DB이름?sslmode=require'
   python -m app.tools.apply_schema --dry-run   # 무엇을 실행할지 미리 보기
   python -m app.tools.apply_schema             # 실제 적용 (표가 없으면 전체 설계도, 있으면 변경분만)
   ```
   같은 명령을 여러 번 실행해도 안전합니다. 나중에 `sql/migrations/` 에 파일이 늘어나면 다시 한 번 실행하면 됩니다.
3. **컨테이너 빌드·실행**
   ```bash
   docker build -t sop-studio .
   docker run -p 8000:8000 -e DATABASE_URL='postgresql://...' sop-studio
   ```
   HCP 에서는 이미지를 올린 뒤 아래 환경변수를 설정하고, 포트는 `PORT`(기본 8000)를 씁니다.
   컨테이너는 회사 규칙대로 `python app.py` 로 시작합니다(Dockerfile 의 ENTRYPOINT). 파이썬이 있는 PC 라면 같은 명령으로 바로 띄워 볼 수도 있습니다.
4. 브라우저에서 서비스 주소를 열면 문서 목록(홈)이 뜹니다.

## 2. 환경변수 (전부 `app/config.py` 가 읽음)

| 이름 | 필수 | 뜻 |
|---|---|---|
| `DATABASE_URL` | ✓ | DB 접속 주소. `postgresql://계정:비밀번호@주소:포트/DB이름?sslmode=require` |
| `PGHOST` `PGPORT` `PGUSER` `PGPASSWORD` `PGDATABASE` | | `DATABASE_URL` 대신 낱개로 줄 때 |
| `PORT` | | 서버 포트 (기본 8000) |
| `ROOT_PATH` | | 프록시가 `/sop` 같은 경로 접두어 뒤에 붙일 때 그 접두어 |
| `USER_HEADER` | | SSO 프록시가 사용자 이름을 넣어 주는 헤더 이름 (기본 `X-User`) |
| `CORS_ORIGINS` | | 편집기 HTML 을 다른 주소에서 띄울 때만. 쉼표 구분 |
| `DB_POOL_MIN` `DB_POOL_MAX` | | DB 연결 개수 (기본 2 / 10) |
| `DB_CONNECT_TIMEOUT` | | DB 접속 대기 초 (기본 30) |
| `DB_SESSION_OPTIONS` | | 기본 `-c timezone=UTC`. **PgBouncer(트랜잭션 풀링) 뒤면 빈 문자열로** |
| `DB_PREPARE_THRESHOLD` | | 기본 5. **PgBouncer 뒤면 0** |
| `MAX_CONTENT_MB` | | 저장 요청 최대 크기 (기본 20) |
| `LOG_LEVEL` | | 기본 INFO |
| `STATIC_DIR` `STATIC_INDEX` | | 편집기 HTML 위치 (기본 `static` / `SOP_STUDIO.html`) |

자세한 설명과 예시는 `.env.example` 에 있습니다.

## 3. 생존 확인 (probe)

| 주소 | 뜻 | 용도 |
|---|---|---|
| `GET /health` | 프로세스가 살아 있음 (DB 안 봄) → 항상 200 | liveness |
| `GET /api/health` | DB 까지 연결됨 → 200, DB 죽으면 503 | readiness |

## 4. 데이터는 어디에 있나

전부 회사 PostgreSQL 안에 있습니다. 컨테이너에는 데이터가 없으므로 컨테이너를 지우고 다시 띄워도 문서는 그대로입니다. 백업은 DB 백업으로 합니다.

## 5. 자주 보는 오류 code

| HTTP | code | 뜻 |
|---|---|---|
| 400 | `sop_no_mismatch` | 저장한 문서의 번호가 서버의 번호와 다름 → 번호 변경은 화면의 번호 칸을 고쳐 저장(자동으로 PATCH) |
| 409 | `sop_no_taken` | 이미 있는 번호로 새 문서를 만들려 함 |
| 409 | `version_conflict` | 다른 사람이 먼저 저장함 → 다시 불러온 뒤 저장 |
| 423 | `locked` | 다른 사람이 편집 중 |
| 413 | `payload_too_large` | 저장 요청이 `MAX_CONTENT_MB` 초과 |
| 422 | `invalid_document` | 문서 JSON 형식 오류 (메시지에 이유) |
