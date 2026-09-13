# SOP Studio 백엔드

## 1. 이게 뭔가요

브라우저 편집기(SOP Studio, `static/SOP_STUDIO.html`)에서 만든 SOP 문서를 **서버(PostgreSQL)에 저장하고 다시 불러오는** 프로그램입니다.
저장할 때마다 새 **버전**이 쌓이므로(1, 2, 3 …) 예전 내용을 언제든 다시 열 수 있고, 두 사람이 같은 문서를 동시에 고쳐도 덮어쓰기 사고가 나지 않습니다(편집 잠금 + 버전 충돌 검사). 순서도 안의 "SOP 상자"(다른 SOP 를 가리키는 상자)는 **문서 번호가 아니라 문서 id 로 연결**되어, 번호를 바꿔도 링크가 끊어지지 않습니다.
만든 재료: Python 3.11 이상, FastAPI(웹 API), PostgreSQL 15 이상(17 로 확인). 로그인 기능은 없고, 사용자 이름은 HTTP 헤더(기본 `X-User`)로 받습니다.

## 2. 폴더 구조

```
sop-backend/
  app/
    main.py              FastAPI 앱 출발점. DB 풀 열고 닫기, 요청 로그, 413 크기 제한, /health, 편집기 HTML 서빙(api-base meta 주입)
    config.py            환경변수 읽기 (DATABASE_URL / PGHOST… / ROOT_PATH / USER_HEADER 등 전부 여기)
    db.py                DB 연결 풀과 트랜잭션 도우미
    deps.py              "지금 요청한 사용자가 누구인지" (헤더 값을 unquote 해서 한글 이름 복원)
    errors.py            모든 오류 응답을 {"error": {"code", "message", ...}} 한 가지 모양으로 통일
    schemas.py           요청/응답 JSON 의 모양 (시각은 전부 UTC 'Z' 로 직렬화)
    derive.py            문서 JSON 형식 검사, 순서도 노드/연결선을 검색용 표로 뽑기
    refs.py              SOP 상자 참조 정리(저장 때), "이 문서를 참조하는 문서" 찾기
    routers/
      sops.py            문서 목록 / 열기 / 저장(POST·PUT) / 번호 변경(PATCH) / 폐기 / 복구 / referenced-by
      versions.py        버전 목록 / 특정 버전 열기 / 두 버전 비교(diff)
      locks.py           편집 잠금(한 번에 한 명만 편집)
      common.py          라우터들이 같이 쓰는 도우미(404 확인, 살아 있는 잠금, 열기 응답의 번호 보정)
    tools/
      dev_server.py      도커·PostgreSQL 설치 없이 내 PC 에서 서버 띄우기 (내장 PostgreSQL 사용)
      apply_schema.py    DB 에 표 만들기 / 기존 DB 갱신(마이그레이션). --dry-run 지원
      flow_editor.py     SOP_STUDIO.html 안에 접혀 있는 순서도 편집기를 꺼내고(extract) 다시 넣기(embed)
  sql/
    sop_schema.sql       DB 표 정의 전체 (새 설치용)
    migrations/001_…sql  이미 있는 DB 에 적용하는 부분 갱신 (멱등 = 여러 번 실행해도 안전)
  static/
    SOP_STUDIO.html      편집기 화면 (약 400KB. 순서도 편집기가 Base64 한 줄로 접혀 들어 있음)
    flow_editor.src.html 순서도 편집기의 읽을 수 있는 원본 (고친 뒤 flow_editor.py embed 로 반영)
  examples/sample_doc.json   curl 로 저장을 시험해 볼 작은 문서 (저장 요청 본문 모양 그대로)
  tests/                 자동 테스트 (pytest, 118건)
  Dockerfile / docker-compose.yml / .dockerignore   컨테이너로 포장·실행하는 설정
  requirements.txt / requirements-dev.txt / pyproject.toml   필요한 라이브러리 목록
  .env.example           환경변수 견본 (복사해서 .env 로)
  pgdata/                (실행하면 생김) 내 PC 용 내장 PostgreSQL 의 데이터 폴더. git 에 올라가지 않음
```

## 3. 내 PC 에서 실행 (도커 없이)

### 3-1. 파이썬 가상환경 만들기

이 프로젝트는 파이썬 3.11 이상이 필요합니다. **macOS 에 기본으로 들어 있는 python3 는 3.9 라서 실행되지 않습니다.**
Homebrew 로 설치한 3.12 를 콕 집어서 가상환경(.venv)을 만듭니다. (다른 PC 라면 3.11 이상 파이썬의 경로로 바꿔 적으면 됩니다)

```bash
cd sop-backend
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate            # ← 가상환경 활성화. 터미널을 새로 열 때마다 이 한 줄을 먼저 실행 (Windows: .venv\Scripts\activate)
pip install -r requirements-dev.txt  # 실행용 + 테스트용 라이브러리 전부 설치 (내장 PostgreSQL 인 pgserver 포함)
```

아래 문서의 파이썬 명령은 전부 "**.venv 활성화 후** `python ...`" 입니다. `python` 이 3.9 를 가리키면 활성화가 안 된 것입니다 (`python --version` 으로 확인).

### 3-2. 서버 켜기

```bash
python -m app.tools.dev_server               # http://localhost:8000
python -m app.tools.dev_server --port 9000   # 포트를 바꾸고 싶을 때
python -m app.tools.dev_server --reload      # 코드를 고치면 자동으로 다시 켜기 (개발용)
python -m app.tools.dev_server --host 0.0.0.0   # 같은 네트워크의 다른 PC 에서도 접속 허용
```

브라우저에서 http://localhost:8000 을 열면 편집기가 뜹니다. 끄기는 `Ctrl+C`.

**PostgreSQL 을 따로 설치한 게 아닙니다.** 파이썬 라이브러리 `pgserver` 안에 PostgreSQL 실행 파일이 통째로 들어 있어서, `dev_server` 가 그것을 이 폴더 안의 `pgdata/` 에 띄우고 표를 만든 뒤 API 서버를 켭니다. 서버를 끄면 내장 PostgreSQL 도 같이 멈춥니다.

### 3-3. pgdata/ 폴더가 뭔가요

- 내장 PostgreSQL 의 **데이터 폴더**입니다. 저장한 SOP 문서·버전·잠금 기록이 전부 여기에 파일로 남습니다.
- 서버를 껐다 켜도 데이터는 그대로입니다. 켤 때 `sql/migrations/` 의 갱신 파일이 자동으로 적용됩니다(데이터는 지우지 않음).
- **완전히 초기화**하려면 서버를 끄고 `pgdata/` 폴더를 지우면 됩니다 (다음 실행 때 빈 DB 로 새로 만듦).
- `.gitignore` 와 `.dockerignore` 에 들어 있어 git 에도, 컨테이너 이미지에도 올라가지 않습니다.
- 다른 폴더를 쓰고 싶으면 `--data-dir 경로` 옵션을 줍니다.

### 3-4. 좀비 postgres 정리

터미널을 그냥 닫거나 강제 종료하면 내장 PostgreSQL 이 정리되지 못하고 남아 있을 수 있습니다. 증상: 다음에 켤 때 접속이 안 되거나, `Ctrl+C` 로 껐는데도 postgres 프로세스가 남아 있음.

```bash
pgrep -fl "postgres.*pgdata"      # 남아 있는 내장 PostgreSQL 찾기
kill <pid>                        # 그 번호를 끄기 (안 죽으면 kill -9 <pid>)
```

`dev_server` 는 켤 때 `pgdata/.handle_pids.json`(누가 쓰고 있는지 적어 둔 명단)에서 이미 죽은 프로세스 번호를 스스로 지우므로, 보통은 다시 켜기만 해도 정상으로 돌아옵니다.

### 3-5. pg_trgm 이 없다는 안내가 떠요

내장 PostgreSQL 에는 `pg_trgm`(한글 부분 검색을 빠르게 하는 확장)이 없습니다. 그래서 표를 만들 때 그 확장을 쓰는 **인덱스 2개(`ix_documents_name_trgm`, `ix_flow_nodes_action_trgm`)만 건너뛰고** 나머지를 전부 만듭니다. 검색 기능은 똑같이 동작하고 속도만 조금 다릅니다. 정상이니 무시해도 됩니다.

### 3-6. 정식 PostgreSQL 에 붙여서 실행하고 싶을 때

이미 PostgreSQL 이 있다면 내장 DB 대신 그쪽을 쓸 수 있습니다.

```bash
cp .env.example .env               # DATABASE_URL 등을 내 값으로 고침
set -a; source .env; set +a        # 현재 터미널에 환경변수 적용
python -m app.tools.apply_schema   # 표 만들기 (처음 한 번. 이미 있으면 마이그레이션만)
uvicorn app.main:app --reload      # 서버 켜기
```

## 4. 회사 환경 배포 (HCP 컨테이너 + 외부 PostgreSQL 17)

회사에서는 이 서버를 **컨테이너 하나**로 띄우고, DB 는 **이미 있는 외부 PostgreSQL 17** 을 씁니다. `pgdata/` 폴더는 내 PC 용 내장 DB 에서만 생기는 것이라 **회사 환경에는 생기지 않습니다** (컨테이너 이미지에도 들어가지 않음).

### 4-1. 환경변수 (app/config.py 가 읽는 것 전부)

전부 선택 사항이며, 없으면 기본값을 씁니다.

| 이름 | 뜻 | 기본값 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 접속 주소 한 줄. **있으면 최우선** | (없음) |
| `PGHOST` `PGPORT` `PGUSER` `PGPASSWORD` `PGDATABASE` | `DATABASE_URL` 이 없고 `PGHOST` 가 있으면 이 다섯 개로 접속 정보를 조립. 비밀번호에 `@` `/` 같은 특수문자가 있어도 그대로 적으면 됨 | 5432 / postgres / (없음) / sop |
| (둘 다 없음) | 로컬 개발용 기본값 `postgresql://postgres:postgres@localhost:5432/sop` | |
| `PGSSLMODE` `PGSSLROOTCERT` | SSL 접속. DB 드라이버(libpq)가 직접 읽는 표준 이름이라 코드에서 따로 다루지 않음 | |
| `DB_SESSION_OPTIONS` | 연결마다 붙이는 세션 옵션. 빈 문자열 `""` 이면 아예 안 붙임 (PgBouncer 대응) | `-c timezone=UTC` |
| `DB_CONNECT_TIMEOUT` | DB 접속을 기다리는 최대 시간(초). 넘으면 원인을 로그에 남기고 서버가 뜨지 않음 | `30` |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 연결 풀 최소/최대 개수 (DB 의 max_connections 를 넘지 않게) | `2` / `10` |
| `DB_PREPARE_THRESHOLD` | 같은 SQL 을 몇 번 실행하면 서버측 prepared statement 로 등록할지. `0` 또는 `""` 이면 아예 안 씀 (PgBouncer 대응) | `5` |
| `ROOT_PATH` | 서버가 `/sop` 같은 경로 접두어 뒤에서 돌 때 그 접두어. 앞의 `/` 는 없으면 붙이고 끝의 `/` 는 뗌 | `""` |
| `USER_HEADER` | 사용자 이름이 들어오는 HTTP 헤더 이름 (SSO 프록시가 넣어 주는 이름으로) | `X-User` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. 로그는 표준 출력(stdout)으로 나감 | `INFO` |
| `CORS_ORIGINS` | 편집기 HTML 을 다른 주소(사내 웹서버 등)에서 띄울 때 허용할 주소들(쉼표 구분). 같은 서버면 비움 | `""` |
| `STATIC_DIR` | 편집기 HTML 이 있는 폴더 | `static` |
| `STATIC_INDEX` | `/` 로 접속했을 때 보여 줄 파일 | `SOP_STUDIO.html` |
| `MAX_CONTENT_MB` | 저장 요청 한 건의 최대 크기(MB). 넘으면 413 | `20` |
| `PORT` | (Dockerfile 이 읽음) 컨테이너 안에서 서버가 열 포트 | `8000` |
| `TEST_DATABASE_URL` | (pytest 만 읽음) 테스트용 DB. 없으면 내장 PostgreSQL 사용. **운영 DB 절대 금지** | (없음) |
| `TEST_DB_ALLOW_DROP` | (pytest 만 읽음) `1` 이면 `TEST_DATABASE_URL` 에 `test` 글자가 없어도 실행 허용. 운영 DB 보호 장치를 끄는 것이므로 쓰지 않는 것을 권장 | (없음) |

DB 접속 예시 — 회사 DB 는 보통 SSL 을 요구하므로 `?sslmode=require` 를 붙입니다.

```bash
# 방법 1) 주소 한 줄
DATABASE_URL="postgresql://sop_app:비밀번호@db.company.local:5432/sop?sslmode=require"

# 방법 2) 항목별 (DATABASE_URL 을 비우고)
PGHOST=db.company.local
PGPORT=5432
PGUSER=sop_app
PGPASSWORD=비밀번호
PGDATABASE=sop
PGSSLMODE=require
# PGSSLROOTCERT=/path/to/company-ca.crt   ← 회사 인증서로 서버를 검증해야 할 때만 (verify-ca / verify-full 과 함께)
```

서버 로그에는 비밀번호가 `****` 로 가려져 찍힙니다.

### 4-2. PgBouncer 뒤에서 쓸 때

DB 앞에 PgBouncer(트랜잭션 풀링)가 있으면 아래 둘을 같이 바꿉니다. PgBouncer 는 1.21 이상이어야 합니다(저장 때 노드/연결선을 한꺼번에 넣는 파이프라인 방식을 그 이하 버전은 못 받습니다).

```bash
DB_SESSION_OPTIONS=""        # options 파라미터를 거부하는 중계기 대응 (응답 시각은 어차피 UTC Z 로 나감)
DB_PREPARE_THRESHOLD=0       # "prepared statement ... does not exist" 오류 방지
```

### 4-3. 스키마 적용 (표 만들기)

`apply_schema` 도구가 DB 상태를 보고 알아서 고릅니다.

- 표가 하나도 없으면 → `sql/sop_schema.sql` 전체 실행 (새 설치)
- 표가 이미 있으면 → `sql/migrations/*.sql` 을 이름순으로 실행 (기존 DB 갱신. 전부 멱등이라 매번 실행해도 안전)
- `pg_trgm` 확장이 없거나 `CREATE EXTENSION` 권한이 없으면 → 그 줄만 빼고 실행 (검색 기능 동일. 나중에 DBA 가 `CREATE EXTENSION pg_trgm;` 을 해 준 뒤 다시 실행하면 인덱스가 생김)

먼저 `--dry-run` 으로 무엇을 할지 확인하고, 실제로 적용합니다.

```bash
# .venv 활성화 후, 환경변수(DATABASE_URL 등)를 회사 DB 로 맞춘 상태에서
python -m app.tools.apply_schema --dry-run     # 무엇을 실행할지 보여 주기만 함
python -m app.tools.apply_schema               # 실제 적용
python -m app.tools.apply_schema --url "postgresql://..."   # 주소를 직접 줄 때
```

컨테이너 이미지 안에서 실행해도 됩니다 (이미지에 `sql/` 이 들어 있음):

```bash
docker run --rm -e DATABASE_URL="postgresql://...?sslmode=require" sop-api:2.0 python -m app.tools.apply_schema --dry-run
docker run --rm -e DATABASE_URL="postgresql://...?sslmode=require" sop-api:2.0 python -m app.tools.apply_schema
```

`psql` 을 직접 쓰고 싶다면: 새 DB 는 `psql "$DATABASE_URL" -f sql/sop_schema.sql`, 기존 DB 는 `psql "$DATABASE_URL" -f sql/migrations/001_ref_document_id.sql` (번호순으로 하나씩).

### 4-4. 컨테이너 빌드·실행

```bash
docker build -t sop-api:2.0 .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql://sop_app:비밀번호@db.company.local:5432/sop?sslmode=require" \
  -e ROOT_PATH="" -e USER_HEADER="X-User" -e LOG_LEVEL="INFO" \
  sop-api:2.0
```

Dockerfile 이 해 두는 것: 비루트 사용자(`app`, uid 10001)로 실행 / `PORT` 환경변수로 포트 결정 / 앞단 프록시의 `X-Forwarded-*` 헤더를 믿도록 `--proxy-headers` / `HEALTHCHECK` 로 30초마다 `/health` 확인 / `.dockerignore` 덕분에 `pgdata/`, `.env`, `tests/` 는 이미지에 들어가지 않음.

### 4-5. 생존 확인 주소 (probe)

| 주소 | 무엇을 보나 | 용도 |
|---|---|---|
| `GET /health` | 프로세스가 살아 있는지만. DB 는 안 봄. 항상 `{"ok": true}` | 플랫폼의 liveness probe / Dockerfile HEALTHCHECK. DB 가 잠깐 흔들려도 컨테이너를 죽이지 않음 |
| `GET /api/health` | 서버 + DB. 정상이면 200 `{"ok": true, "db": "up"}`, DB 가 죽어 있으면 **503** `{"ok": false, "db": "down"}` | readiness probe, 배포 뒤 첫 확인, 모니터링(`curl -f`) |

### 4-6. ROOT_PATH — 경로 접두어 뒤에서 돌 때

회사 게이트웨이가 `https://portal.company.com/sop/...` 처럼 접두어를 붙여 넘겨주면 `ROOT_PATH=/sop` 을 줍니다. 그러면

- FastAPI 가 `/docs` 등 자기 주소 계산을 접두어에 맞춥니다.
- 서버가 편집기 HTML 을 내려줄 때 `<head>` 바로 뒤에 `<meta name="api-base" content="/sop">` 한 줄을 **끼워 넣습니다** (ROOT_PATH 가 비어 있으면 `content=""`).
- 편집기는 모든 API 주소를 `apiUrl(path)` 한 함수로 만드는데, 이 meta 가 있으면 그 접두어를 앞에 붙입니다. 그래서 HTML 을 고치지 않고 환경변수만으로 접두어 대응이 됩니다.

주의: HTML 파일을 서버를 거치지 않고(파일로 직접, 또는 다른 웹서버에서) 열면 meta 가 없어 접두어 없이 `/api/...` 를 부릅니다. 그 경우엔 `CORS_ORIGINS` 도 같이 설정해야 합니다.

### 4-7. USER_HEADER — SSO 연동

로그인 기능이 없으므로 "누가 저장/잠금했는지" 는 요청 헤더에서 읽습니다. 회사 SSO 프록시가 사용자 이름을 예컨대 `X-Auth-User` 헤더에 넣어 준다면 `USER_HEADER=X-Auth-User` 로 바꾸기만 하면 됩니다. 헤더가 없으면 `anonymous` 로 기록됩니다.
서버는 헤더 값을 `urllib.parse.unquote` 로 한 번 풀어서 씁니다(편집기가 한글 이름을 `%ED%99%8D…` 로 보내기 때문. 8절 참고). 영문 이름은 바뀌는 글자가 없어 그대로입니다. SSO 가 넣는 값에 `%` 가 들어 있다면 풀린 형태로 기록된다는 점만 알아 두세요.
모든 API 는 `app/deps.py` 의 `current_user` 한 함수를 거치므로(`API_GUARD`), 나중에 "허용되지 않은 사용자면 401" 을 그 함수에서 던지면 API 전체가 한 번에 보호됩니다.

## 5. docker compose 로 통째로 시험하기 (선택)

Docker Desktop 이 있으면 내 PC 에서 "PostgreSQL 17 + API 서버" 두 상자를 한 번에 켤 수 있습니다.

```bash
docker compose up            # 처음엔 이미지를 만드느라 몇 분. 켜지면 http://localhost:8000
docker compose down          # 끄기 (데이터는 도커 볼륨에 남음)
docker compose down -v       # DB 데이터까지 지우고 처음부터
docker compose run --rm api python -m app.tools.apply_schema   # 스키마가 바뀌었을 때 마이그레이션 적용
```

DB 가 **처음** 만들어질 때만 `sql/sop_schema.sql` 이 자동 실행됩니다. 이미 만들어진 DB 에 표 모양이 바뀐 것을 반영하려면 위의 `apply_schema` 를 쓰거나 `down -v` 로 새로 만듭니다. 회사 환경에서는 이 파일의 `db` 상자를 쓰지 않고 `api` 상자만 올리면서 환경변수로 회사 DB 를 가리키면 됩니다.

## 6. API 한눈에

모든 API 는 `/api` 로 시작하고 JSON 을 주고받습니다. 시각은 ISO 8601 UTC 로 끝에 `Z` 가 붙습니다(예: `2026-09-11T02:30:00Z`). `{doc_id}` 는 서버가 붙여 준 문서 id(UUID)이고, `{sop_no}` 는 사람이 보는 SOP 번호(`SOP-ETCH-001`)입니다.

| 메서드 | 경로 | 하는 일 | 성공 | 실패 code |
|---|---|---|---|---|
| GET | `/health` | 프로세스 생존 확인 (DB 안 봄) | 200 | |
| GET | `/api/health` | 서버·DB 확인. DB 죽으면 503 `{"ok":false,"db":"down"}` | 200 | (503, code 없음) |
| GET | `/api/sops?status=!retired&q=&area=` | 문서 목록(라이브러리 트리). `content` 없음. `status`: `all` / `!retired`(기본) / 특정 상태 | 200 | |
| GET | `/api/sops/by-no/{sop_no}` | 번호로 최신 버전 열기 (`content`, `lock`, `ref_docs` 포함) | 200 | `not_found` |
| GET | `/api/sops/{doc_id}` | id 로 최신 버전 열기 | 200 | `not_found` |
| POST | `/api/sops` | **새 문서 만들기** (버전 1). 번호가 이미 있으면 409 와 함께 `existing_id` | 201 | `sop_no_taken` `invalid_document` |
| PUT | `/api/sops/{doc_id}` | **기존 문서에 새 버전 추가**. 폐기된 문서였으면 draft 로 되살림 | 201 | `not_found` `version_conflict` `sop_no_mismatch` `invalid_document` |
| PATCH | `/api/sops/{doc_id}/number` | **SOP 번호만 변경** (버전은 손대지 않음). body `{"sop_no": "새번호", "user": ""}` | 200 | `not_found` `locked` `sop_no_taken` `invalid_document` |
| DELETE | `/api/sops/{doc_id}` | 폐기(`status=retired`). 행은 지우지 않음. 응답에 `referenced_by`(참조하는 문서 수) | 200 | `not_found` |
| GET | `/api/sops/{doc_id}/referenced-by` | 이 문서를 (현재 버전 순서도에서) 참조하는 다른 문서 목록 | 200 | `not_found` |
| GET | `/api/sops/{doc_id}/versions` | 버전 목록 (최신부터, `content` 없음) | 200 | `not_found` |
| GET | `/api/sops/{doc_id}/versions/{version_no}` | 특정 버전 열기 (열기 응답과 같은 모양) | 200 | `not_found` `version_not_found` |
| GET | `/api/sops/{doc_id}/versions/{a}/diff/{b}` | 두 버전의 순서도 노드 차이 `{added, removed, changed}` | 200 | `not_found` `version_not_found` |
| POST | `/api/sops/{doc_id}/lock` | 편집 잠금 잡기 / 연장. body `{"user": "", "ttl_sec": 120}` (10~3600초) | 200 | `not_found` `locked` |
| DELETE | `/api/sops/{doc_id}/lock` | 내 잠금 풀기. 누구인지는 body `user` → `?user=` → 헤더 순. 남의 잠금이면 아무 일 없이 200 | 200 | `not_found` |

어느 주소든 공통으로 `payload_too_large`(413), `validation_error`(422), `http_error`(없는 주소 404·허용 안 된 메서드 405), `internal_error`(500) 이 날 수 있습니다. 전체 목록은 10-1절.

### 저장 요청·응답

POST 와 PUT 의 본문은 같습니다. `examples/sample_doc.json` 이 이 모양입니다.

```json
{ "doc": { ...편집기 문서 JSON 통째... }, "base_version_no": 3, "change_note": "Step 4 추가", "saved_by": "" }
```

- `doc.sop.id` 가 SOP 번호입니다. 비어 있거나 영문·숫자·`-` `_` `.` 이외의 글자가 있으면 422 `invalid_document`.
- `base_version_no` = 내가 열었을 때의 버전 번호. 그 사이 다른 사람이 저장했으면 409 `version_conflict`. `null` 이면 검사하지 않습니다(강제 저장). POST 는 무시합니다.
- `saved_by` 를 비우면 사용자 헤더 값이 들어갑니다.
- PUT 에서 `doc.sop.id` 가 서버에 저장된 번호와 다르면 400 `sop_no_mismatch`. 번호를 바꾸려면 PATCH `/number` 를 먼저 부릅니다.

응답(201):

```json
{ "id": "3f2a…", "sop_no": "SOP-ETCH-001", "version_id": "…", "version_no": 2, "saved_at": "2026-09-11T02:30:00Z",
  "warnings": ["폐기됐던 문서를 다시 살렸습니다 (draft)"], "warning": null,
  "refs": { "linked": 3, "pending": 1, "empty": 0 } }
```

- `warnings`: 저장은 됐지만 알아 둘 것(건너뛴 노드, 끊긴 참조, 되살림 등). 편집기가 토스트로 보여 줍니다.
- `warning`: 다른 사람이 잠금 중인데 저장한 경우 그 안내 한 줄.
- `refs`: SOP 상자 참조 정리 결과(연결됨 / 미작성 / 비어 있음 개수. 7절).

### curl 로 시험해 보기

서버가 http://localhost:8000 에 떠 있다고 가정합니다.

```bash
# 1) 살아 있나
curl http://localhost:8000/health          # {"ok":true}
curl http://localhost:8000/api/health      # {"ok":true,"db":"up"}

# 2) 새 문서 만들기 (POST). 응답의 id 를 잘 적어 둡니다
curl -X POST http://localhost:8000/api/sops \
     -H "Content-Type: application/json" -H "X-User: hong" \
     -d @examples/sample_doc.json
# → 201 {"id":"3f2a...","sop_no":"SOP-ETCH-001","version_no":1,...}
# 같은 명령을 한 번 더 보내면 → 409 {"error":{"code":"sop_no_taken","existing_id":"3f2a...",...}}

# 3) 그 문서에 새 버전 쌓기 (PUT). DOC_ID 는 2) 에서 받은 id. base_version_no 는 지금 최신 번호(1)
DOC_ID=3f2a...
curl -X PUT http://localhost:8000/api/sops/$DOC_ID \
     -H "Content-Type: application/json" -H "X-User: hong" \
     -d "$(python -c "import json; d=json.load(open('examples/sample_doc.json')); d['base_version_no']=1; print(json.dumps(d))")"
# → 201 version_no 2.  같은 명령(base_version_no=1)을 다시 보내면 → 409 version_conflict {"current_version_no":2}

# 4) 목록 / 열기
curl http://localhost:8000/api/sops
curl http://localhost:8000/api/sops/by-no/SOP-ETCH-001
curl http://localhost:8000/api/sops/$DOC_ID

# 5) 번호 변경 (버전은 그대로). 다른 사람이 잠금 중이면 423
curl -X PATCH http://localhost:8000/api/sops/$DOC_ID/number \
     -H "Content-Type: application/json" -H "X-User: hong" -d '{"sop_no":"SOP-ETCH-001A"}'
# → 200 {"id":"...","sop_no":"SOP-ETCH-001A","old_sop_no":"SOP-ETCH-001","referenced_by":0}

# 6) 이 문서를 참조하는 문서
curl http://localhost:8000/api/sops/$DOC_ID/referenced-by

# 7) 편집 잠금 (같은 요청을 주기적으로 보내면 연장)
curl -X POST http://localhost:8000/api/sops/$DOC_ID/lock -H "Content-Type: application/json" -d '{"user":"hong","ttl_sec":120}'
# 다른 사람이 잡고 있으면 → 423 {"error":{"code":"locked","locked_by":"kim","expires_at":"..."}}
curl -X DELETE "http://localhost:8000/api/sops/$DOC_ID/lock?user=hong"

# 8) 폐기 → 목록에서 사라짐 → 복구
curl -X DELETE http://localhost:8000/api/sops/$DOC_ID          # {"id":"...","status":"retired","referenced_by":0}
curl "http://localhost:8000/api/sops?status=retired"           # 여기엔 보임

# 9) 버전 목록 / 특정 버전 / 비교
curl http://localhost:8000/api/sops/$DOC_ID/versions
curl http://localhost:8000/api/sops/$DOC_ID/versions/1
curl http://localhost:8000/api/sops/$DOC_ID/versions/1/diff/2
```

## 7. 참조 · 번호 변경 · 하이퍼링크 동작

### 7-0. 새 문서 만들기 · 저장 · 버전

- 편집 중에는 헤더의 **[⌂ 홈]** 으로 문서를 닫고(잠금 해제) 목록으로 돌아갈 수 있습니다. 저장 안 한 변경이 있으면 먼저 물어봅니다.
- **첫 화면은 문서 목록(홈)** 입니다. 서버에 접속하면 편집기 대신 저장된 문서가 카드로 나열되고, 카드를 누르거나 왼쪽 트리에서 고르면 그때 편집 화면이 열립니다. 이 기기에 임시 문서가 남아 있으면 홈 위쪽에 **[이어서 작성]** 이 보입니다.
- **새 문서**: 라이브러리 패널의 **[+ 새 문서]** 를 누르면 열려 있던 문서를 닫고(편집 잠금도 풀림) 빈 문서(표지 1장 + 순서도 1장)로 시작합니다. 라이브러리에 저장하지 않은 변경이 있으면 먼저 물어봅니다. SOP 번호와 이름을 적고 **[현재 문서를 라이브러리에 저장]** 을 누르면 서버에 새 문서로 만들어지고(`POST /api/sops`, 버전 1) 트리에 바로 나타납니다. AREA 를 고르지 않으면 트리의 "미지정" 아래에 들어갑니다.
- **저장할 때마다 버전이 하나씩 쌓입니다** (v1, v2, v3 …). 이 번호는 시스템이 자동으로 매기는 "몇 번째 저장본" 이라, 오타 하나 고치고 저장해도 올라갑니다. 문서 프로그램의 자동 저장 이력과 같은 것이고, 어느 시점의 순서도든 그대로 꺼내 볼 수 있게 하는 안전장치입니다(한 버전이 보통 수 KB 라 많이 쌓여도 부담 없음).
- **개정 번호(Rev 1.0, 1.1 …)는 별개**입니다. 표지에 적는 공식 개정 번호는 사람이 "개정 번호" 칸에 직접 입력하며, 절차가 실제로 바뀌었을 때만 올립니다. Rev 1.0 을 만드는 동안 열 번 저장하면 v1~v10 이 쌓이지만 개정 번호는 계속 1.0 입니다. 두 버전 사이에 무엇이 바뀌었는지는 `GET /api/sops/{id}/versions/{a}/diff/{b}` 로 볼 수 있습니다.

### 7-1. SOP 상자의 세 가지 상태

순서도의 "SOP 상자" 에는 세 값이 있습니다: `ref_document_id`(가리키는 문서의 id — **진짜 연결**), `sop_id`(번호 — 표시용), `sop_name`(이름 — 표시용). 저장할 때마다 서버(`app/refs.py`)가 상자 하나하나를 정리하고 개수를 `refs` 로 알려 줍니다.

| 상태 | 상자 안의 값 | 서버가 저장 때 하는 일 | 편집기 표시 |
|---|---|---|---|
| **연결됨** (`linked`) | `ref_document_id` 가 있고 그 문서가 존재 | 번호·이름 글자를 그 문서의 **현재** 번호·이름으로 덮어씀. 그 문서가 폐기됐으면 경고 1건 | `연결됨 ✓ 번호 — 이름` / 폐기됐으면 점선+붉은색 `폐기된 문서를 가리킴` |
| **미작성** (`pending`) | id 는 없고 번호만 적혀 있는데 라이브러리에 그 번호가 없음 | 손대지 않음 (경고 없음). 나중에 그 번호의 문서가 생기면 **다음 저장 때 자동으로 연결됨으로 승격** | 점선+회색 `미작성 (라이브러리에 없는 번호)` |
| **비어 있음** (`empty`) | 번호도 id 도 없음 | 저장은 되고 "참조 대상이 비어 있는 SOP 상자 N개" 경고 1건 | 점선+회색 `참조 대상이 비어 있습니다` |

그 밖에: id 는 있는데 그런 문서가 없으면(지워졌거나 UUID 형식이 아님) 연결을 풀어 `null` 로 만들고 글자는 그대로 두며 경고 1건. 번호만 적었는데 라이브러리에 그 번호가 **있으면** 편집기는 `연결 대기 (저장하면 연결됩니다)` 로 표시하고, 저장 때 서버가 id 를 채워 연결됨으로 승격합니다. 자기 자신을 가리키는 상자도 허용합니다. DB 조회는 저장 1회당 최대 2번(id 목록 한 번, 번호 목록 한 번)입니다.

### 7-2. "번호는 저장하지 않고 조회한다"

상자에 적힌 번호·이름은 **캐시(사본)** 일 뿐이고, 진짜 연결은 id 입니다. 그래서

- 문서를 열면 서버가 `ref_docs`(이 문서의 상자들이 가리키는 문서들의 **지금** 번호·이름·상태)를 함께 내려주고, 편집기는 부모 창이 넘겨준 라이브러리 목록(`__flowSyncLibrary`)을 기준으로 상자를 그립니다. 저장된 글자가 옛 번호여도 화면에는 현재 번호가 보입니다.
- 저장하면 서버가 글자도 현재 값으로 다시 써 넣으므로 DB 도 점점 최신으로 따라옵니다.
- 편집기의 속성창에서 SOP ID 는 자유 입력이지만 라이브러리 목록(datalist)에서 고를 수도 있고, 목록의 번호와 정확히 일치하면 id 와 이름이 자동으로 채워집니다.
- 순서도를 파일로 내보내기 직전에도 상자의 번호·이름을 라이브러리 기준 최신값으로 한 번 더 맞춥니다.

### 7-3. 하이퍼링크 (더블클릭으로 열기)

순서도에서 SOP 상자를 **더블클릭**하거나 속성창의 [열기] 를 누르면 편집기(iframe)가 부모 창의 `__studioOpenSopRef({id, sop_no})` 를 불러 그 문서를 엽니다. id 가 있으면 id 로, 없으면 번호로 엽니다. 연결됨·폐기됨·연결 대기 상태에서만 열 수 있고, 미작성·비어 있음은 열 것이 없습니다. 라이브러리 패널의 "이 문서를 참조하는 문서 N건" 접이식 목록에서도 항목을 눌러 그 문서로 갈 수 있습니다.

### 7-4. 번호를 바꾸면 무엇이 바뀌고 무엇이 안 바뀌나

편집기에서 번호 입력칸을 고친 뒤 저장하면 "SOP 번호를 A → B 로 변경할까요?" 라고 물은 다음 PATCH `/number` → PUT 순서로 보냅니다.

**바뀌는 것**
- 문서의 현재 번호(`sop_documents.sop_no`)와 갱신 시각. 목록·트리에 새 번호가 보임.
- 문서를 여는 모든 응답(최신 버전, 과거 버전 모두)에서 `content.sop.id` 를 현재 번호로 보정해서 내려줌. 편집기도 문서를 연 뒤 번호 입력칸을 `rec.sop_no` 로 덮어씀 (옛 글자가 남아 "번호를 되돌릴까요?" 라고 묻는 일이 없게).
- 이 문서를 **id 로 연결**한 다른 문서의 상자는 다음에 열거나 저장할 때 자동으로 새 번호를 봄.

**안 바뀌는 것**
- 버전(`sop_versions`)은 한 줄도 손대지 않음. DB 의 과거 버전 `content` 안에는 **옛 번호가 그대로** 남아 있음(기록 보존. 위의 보정은 응답을 만들 때만).
- 옛 번호를 **글자로만** 적어 둔(미작성) 다른 문서의 상자는 새 번호를 따라오지 못하고 그대로 미작성으로 남음. 그 문서를 열어 새 번호로 고쳐 저장하면 연결됨. 이 개수는 서버 로그에 남고, 편집기의 확인 문구에도 그대로 적혀 있음.

### 7-5. 편집 중에는 번호 변경이 막히는 이유

**다른 사람**이 그 문서의 편집 잠금을 잡고 있으면 PATCH `/number` 는 423 `locked` 입니다(본인 잠금은 허용). 그 사람의 화면에는 옛 번호가 들어 있어서, 번호가 바뀐 뒤 그 사람이 저장하면 400 `sop_no_mismatch` 로 실패하고 누구 말이 맞는지 알 수 없게 됩니다. 그래서 편집 중인 문서의 번호는 편집이 끝나(잠금이 풀리거나 만료되어) 한 사람만 만지는 상태에서 바꾸게 합니다.

### 7-6. 폐기한 문서를 저장하면 되살아남

폐기(`retired`)된 문서에 PUT 으로 새 버전을 저장하면 서버가 `draft` 로 되살리고 `warnings` 에 `"폐기됐던 문서를 다시 살렸습니다 (draft)"` 를 넣어 줍니다(편집기가 토스트로 표시). 그대로 두면 저장은 됐는데 트리에 영영 안 보이는 상태가 되기 때문입니다. 폐기할 때는 응답의 `referenced_by` 로 "이 문서를 참조하는 문서가 N건 있다" 를 알려 주고, 그 문서들의 상자는 `폐기된 문서를 가리킴` 으로 표시됩니다.

## 8. 사용자 이름

- 편집기를 처음 열면 **한 번만** "라이브러리에 표시할 이름을 입력하세요" 라고 묻고, 브라우저의 `localStorage` 키 `sop-studio-user` 에 저장합니다. 이후로는 묻지 않습니다.
- 라이브러리 패널 위쪽에 현재 이름이 보이고, 옆의 **[이름 바꾸기]** 를 누르면 저장해 둔 이름을 지우고 새로고침해서 다시 묻습니다.
- 이 이름이 저장 기록(`saved_by`), 편집 잠금(`locked_by`), 임시 저장의 주인으로 쓰입니다.
- **한글 이름도 됩니다.** HTTP 헤더에는 원칙적으로 영문·숫자만 실을 수 있어서 편집기는 `encodeURIComponent(이름)` 으로 감싸 `X-User: %ED%99%8D%EA%B8%B8%EB%8F%99` 처럼 보내고, 서버(`app/deps.py`)가 `unquote` 로 `홍길동` 으로 되돌려 기록합니다. curl 로 한글 이름을 보내려면 같은 방식으로 인코딩합니다:

```bash
curl -H "X-User: $(python -c 'import urllib.parse; print(urllib.parse.quote("홍길동"))')" http://localhost:8000/api/sops
```

- 회사 SSO 를 붙이면 프록시가 넣어 주는 헤더 이름을 `USER_HEADER` 로 지정합니다(4-7절). 그 경우 편집기가 보내는 `X-User` 는 무시되고 SSO 값이 쓰입니다.

## 9. 개발자용: 테스트 · 편집기 소스 수정

### 9-1. 테스트

```bash
# .venv 활성화 후
python -m pytest -q        # 118 passed
```

- `TEST_DATABASE_URL` 이 **없으면** 내장 PostgreSQL(`pgserver`)이 임시 폴더에 임시 DB 를 띄워 테스트합니다. 별도 설치가 필요 없고, `pg_trgm` 이 없어 인덱스 2개를 건너뛰는 대체 경로로 도는 것이 정상입니다.
- **있으면** 그 DB 를 씁니다. 테스트가 표를 지우고 다시 만들므로 주소에 `test` 라는 글자가 없으면 실행을 거부합니다(정말 그 주소가 맞다면 `TEST_DB_ALLOW_DROP=1` 을 함께 주면 통과하지만, 보호 장치를 끄는 것이니 권장하지 않습니다). 운영 DB 는 절대 넣지 마세요.

### 9-2. 편집기(순서도) 소스 수정 절차

`static/SOP_STUDIO.html` 안에는 순서도 편집기 HTML 전체가 Base64 로 접혀 **한 줄**(`const EMBEDDED_FLOW_B64 = "…"`, 약 15만 글자)로 들어 있습니다. 그 줄은 사람이 고칠 수 없으니 아래 순서로 합니다.

```bash
# .venv 활성화 후
python -m app.tools.flow_editor extract   # 1) 그 줄을 풀어 static/flow_editor.src.html 로 저장 (이미 있으면 생략 가능)
#   2) static/flow_editor.src.html 을 고친다
python -m app.tools.flow_editor embed     # 3) 원본을 다시 접어 그 한 줄만 바꿔 넣음 (다른 줄은 한 글자도 안 건드림)
python -m app.tools.flow_editor check     # 4) 넣은 결과를 다시 풀었을 때 원본과 완전히 같은지 확인
```

`SOP_STUDIO.html` 의 Base64 줄을 직접 편집하지 마세요. 그 파일의 나머지 부분(라이브러리 패널 등)은 보통 파일처럼 고치면 됩니다.

## 10. 문제 해결

### 10-1. 오류 응답 형식과 code 표

모든 오류는 같은 모양입니다. `code` 는 프로그램이 구분하는 이름, `message` 는 사람이 읽는 한국어 설명입니다.

```json
{ "error": { "code": "version_conflict", "message": "다른 사람이 먼저 v4 을(를) 저장했습니다. 다시 불러온 뒤 저장하세요.", "current_version_no": 4 } }
```

| 상태 | code | 언제 | 함께 오는 값 / 대처 |
|---|---|---|---|
| 400 | `sop_no_mismatch` | PUT 에서 `doc.sop.id` 가 서버에 저장된 번호와 다름 | `stored_sop_no`, `doc_sop_no`. 번호를 바꾸려면 PATCH `/number` 먼저 |
| 404 | `not_found` | 그 id/번호의 문서가 없음 (버전이 하나도 없는 문서 포함) | |
| 404 | `version_not_found` | 그 문서에 그 번호의 버전이 없음 | `version_no` |
| 404 | `static_not_found` | `/` 로 접속했는데 편집기 HTML 파일이 없음 | `STATIC_DIR` / `STATIC_INDEX` 확인 |
| 404·405 | `http_error` | 없는 주소, 허용 안 된 메서드 등 프레임워크가 내는 오류 | 주소 오타, `ROOT_PATH` 접두어 불일치 의심 |
| 409 | `sop_no_taken` | POST 의 번호가 이미 있음 / PATCH 의 새 번호를 다른 문서가 씀 | `existing_id`. 그 id 로 PUT 하면 새 버전으로 저장됨 |
| 409 | `version_conflict` | 내가 연 뒤 다른 사람이 먼저 저장함 | `current_version_no`. 다시 불러온 뒤 저장. 정말 덮어써야 하면 `base_version_no: null` |
| 413 | `payload_too_large` | 요청이 `MAX_CONTENT_MB`(기본 20MB) 초과 | 이미지 줄이기 또는 한도 올리기 |
| 422 | `invalid_document` | `format` 이 `sop-editor-mock` 이 아님, `version` 정수 아님, `blocks` 배열 아님, SOP 번호가 비었거나 허용 안 되는 글자(영문·숫자·`-` `_` `.` 만 가능) | |
| 422 | `validation_error` | 요청 JSON 자체가 모양에 안 맞음(필드 누락, 타입 틀림) | `details` 에 위치 |
| 423 | `locked` | 다른 사람이 편집 잠금 중 (잠금 잡기, 번호 변경) | `locked_by`, `expires_at`. 만료되면 자동 해제 |
| 500 | `internal_error` | 예상 못 한 서버 오류. 자세한 내용은 서버 로그에만 | |
| 503 | (없음) | `/api/health` 에서 DB 접속 실패 `{"ok":false,"db":"down"}` | DB 주소·방화벽·비밀번호 확인 |

### 10-2. 자주 겪는 문제

**서버가 뜨자마자 "DB 에 30초 안에 접속하지 못했습니다" 하고 꺼져요.**
`DATABASE_URL`(또는 `PGHOST` 등) 값, DB 서버가 켜져 있는지, 방화벽/포트, 계정·비밀번호를 순서대로 확인하세요. 로그에 접속 정보가 비밀번호만 가려진 채 찍히니 그 값이 의도한 것인지 보면 됩니다. 기다리는 시간은 `DB_CONNECT_TIMEOUT` 으로 조절합니다.

**`no pg_hba.conf entry for host ... SSL off` 라고 나와요.**
회사 DB 가 SSL 접속을 요구하는 것입니다. `DATABASE_URL` 끝에 `?sslmode=require` 를 붙이거나 `PGSSLMODE=require` 를 주세요.

**`prepared statement "_pg3_0" does not exist` 가 나요.**
PgBouncer(트랜잭션 풀링) 뒤에서 도는 경우입니다. `DB_PREPARE_THRESHOLD=0` 과 `DB_SESSION_OPTIONS=""` 를 같이 주세요(4-2절).

**편집기 화면은 뜨는데 라이브러리가 비어 있고 "라이브러리 목록을 불러오는 중…" 만 나와요.**
편집기가 API 를 못 부르는 것입니다. 브라우저 개발자 도구(F12) 네트워크 탭에서 `/api/sops` 요청이 404 면 `ROOT_PATH` 접두어가 게이트웨이 설정과 다른 것이고, CORS 오류면 HTML 을 다른 주소에서 띄운 것이니 `CORS_ORIGINS` 를 주세요. 서버를 거쳐 `/` 로 열어야 `api-base` meta 가 주입됩니다.

**저장이 409 로 실패해요.**
내가 문서를 연 뒤에 다른 사람이 같은 문서를 먼저 저장한 것입니다. 편집기가 "서버의 최신 내용을 다시 불러올까요?" 라고 묻습니다. [취소] 하면 지금 화면은 그대로 두니, 원본 JSON 으로 내려받아 두었다가 옮겨 적을 수 있습니다.

**번호를 바꾸려는데 "○○ 님이 편집 중이라 번호를 바꿀 수 없습니다" 라고 해요.**
7-5절. 그 사람이 편집을 끝내거나 잠금이 만료(기본 120초, 편집기가 주기적으로 연장)될 때까지 기다리세요.

**내 PC 에서 포트 8000 이 이미 사용 중이래요.**
`python -m app.tools.dev_server --port 9000` 처럼 다른 포트로 띄우세요.

**내 PC 에서 서버가 안 뜨거나 DB 접속이 안 돼요 (dev_server).**
3-4절의 좀비 postgres 정리를 먼저 해 보고, 그래도 안 되면 서버를 끈 뒤 `pgdata/` 를 지우고 다시 켜세요(데이터는 사라집니다).

**`python` 이 3.9 라고 나와요 / `ModuleNotFoundError` 가 나요.**
가상환경이 활성화되지 않은 것입니다. `source .venv/bin/activate` 를 먼저 실행하세요. 가상환경을 만든 적이 없으면 3-1절.

**스키마(sql/)를 바꿨는데 반영이 안 돼요.**
새 표 모양은 `sql/sop_schema.sql`(새 설치용)과 `sql/migrations/NNN_*.sql`(기존 DB 용, 멱등) 양쪽에 넣고, `python -m app.tools.apply_schema` 를 실행합니다. 내 PC 의 `dev_server` 는 켤 때 자동으로 적용하고, docker compose 는 `docker compose run --rm api python -m app.tools.apply_schema` 로 적용합니다.
