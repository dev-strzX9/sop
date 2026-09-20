# 백엔드 완전 가이드 — SOP Studio 서버

> 백엔드를 처음 맡은 사람을 위한 문서입니다.
> 프론트엔드 지식은 전혀 필요 없습니다. "프론트 = API를 호출하는 쪽" 정도로만 알면 됩니다.
>
> 읽는 순서 추천: **1장 → 2장 → 4장(라우터) → 9장(문법)**.
> 나머지는 필요할 때 찾아보세요.

---

## 목차

| 장 | 내용 |
|---|---|
| [1](#1장-백엔드란-무엇을-하는-곳인가) | 백엔드란 무엇을 하는 곳인가 |
| [2](#2장-전체-구조--요청-하나가-지나가는-길) | 전체 구조 — 요청 하나가 지나가는 길 |
| [3](#3장-데이터-모델--무엇을-저장하는가) | 데이터 모델 — 무엇을 저장하는가 |
| [4](#4장-라우터-상세) | 라우터 상세 (sops / versions / locks) |
| [5](#5장-지원-모듈-상세) | 지원 모듈 상세 (derive / refs / schemas / errors / db / config) |
| [6](#6장-동시성--두-사람이-동시에-건드릴-때) | 동시성 — 두 사람이 동시에 건드릴 때 |
| [7](#7장-오류-설계) | 오류 설계 |
| [8](#8장-실전--새-api-추가하기) | 실전 — 새 API 추가하기 |
| [9](#9장-문법-사전) | 문법 사전 |
| [10](#10장-치트시트) | 치트시트 |

---

# 1장. 백엔드란 무엇을 하는 곳인가

## 1.1 한 문장 정의

> **프론트가 보낸 HTTP 요청을 받아, 검증하고, DB를 다루고, JSON으로 답하는 프로그램.**

## 1.2 책임 범위

프론트와 백엔드는 **계약(contract)** 하나로만 연결됩니다. 그 계약은 4가지로 이루어집니다.

```
1. 경로(path)      /api/sops/{id}
2. 메서드(method)  GET / POST / PUT / PATCH / DELETE
3. 요청 모양       { "doc": {...}, "base_version_no": 3 }
4. 응답 모양       { "id": "...", "version_no": 4 }  +  상태코드 201
```

이 4가지만 지키면 **프론트가 React든 Vue든 그냥 HTML이든 백엔드는 상관하지 않습니다.**
반대로 백엔드 내부를 아무리 뜯어고쳐도 이 4가지가 그대로면 프론트는 아무것도 몰라도 됩니다.

| 누구 | 책임 |
|---|---|
| **프론트** | 화면 그리기, 사용자 입력 받기, API 호출, 받은 JSON 표시 |
| **백엔드 (당신)** | **데이터의 정확성, 권한, 동시성, 이력 보존, 오류 처리** |

핵심: **"데이터가 틀리면 그건 백엔드 책임"** 입니다. 프론트가 이상한 값을 보내도 백엔드가 막아야 합니다.
프론트의 검증은 "사용자 편의"이고, 백엔드의 검증은 "최후의 방어선"입니다.

## 1.3 HTTP 메서드의 의미

| 메서드 | 의미 | 이 프로젝트 예 |
|---|---|---|
| `GET` | 읽기 (아무것도 바꾸지 않음) | 목록, 열기, 버전 비교 |
| `POST` | 새로 만들기 | 새 문서 생성, 잠금 잡기 |
| `PUT` | 통째로 바꾸기/추가 | 새 버전 저장 |
| `PATCH` | 일부만 바꾸기 | SOP 번호만 변경 |
| `DELETE` | 지우기 | 문서 폐기, 잠금 해제 |

> ⚠️ **메서드는 "프론트 입장에서의 의미"입니다. 내부 구현은 달라도 됩니다.**
> 이 프로젝트의 `DELETE /api/sops/{id}` 는 실제로는 `UPDATE ... SET status='retired'` 를 합니다.
> SOP는 이력 보존이 원칙이라 행을 지우지 않기 때문입니다.

## 1.4 상태 코드

| 코드 | 이름 | 이 프로젝트에서 쓰는 경우 |
|---|---|---|
| `200` | OK | 정상 (읽기, 번호 변경, 잠금) |
| `201` | Created | 저장 성공 (새 문서 / 새 버전) |
| `400` | Bad Request | 문서 안 번호와 저장된 번호 불일치 |
| `404` | Not Found | 문서/버전 없음 |
| `409` | Conflict | 번호 중복, 버전 충돌 |
| `413` | Payload Too Large | 요청 20MB 초과 |
| `422` | Unprocessable | 문서 JSON 형식 오류 |
| `423` | Locked | 다른 사람이 편집 중 |
| `500` | Server Error | 예상 못 한 오류 |

**4xx = 요청한 쪽 잘못, 5xx = 서버 잘못.** 이 구분이 중요합니다.
500이 자주 뜬다면 그건 "예상 못 한 상황을 백엔드가 처리 안 했다"는 뜻입니다.

---

# 2장. 전체 구조 — 요청 하나가 지나가는 길

## 2.1 흐름도

```
  브라우저 (프론트)
      │
      │  PUT /api/sops/a1b2c3?  { "doc": {...}, "base_version_no": 3 }
      │  헤더: X-User: hong
      ▼
┌─────────────────────────────────────────────────────┐
│ ① uvicorn           app.py                          │
│    웹서버. 포트 8000에서 HTTP를 받아 파이썬으로 넘김      │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ② 미들웨어           app/main.py  log_and_limit      │
│    · Content-Length > 20MB  →  즉시 413              │
│    · 끝나면 "PUT /api/sops/.. -> 201 (85ms)" 로그     │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ③ CORS 미들웨어      (CORS_ORIGINS 설정 시에만)        │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ④ 라우팅            FastAPI                          │
│    경로+메서드로 실행할 함수를 찾음 → save_document     │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ⑤ 의존성 주입        Depends(...)                    │
│    current_user(deps.py) → "hong"                   │
│    get_conn(db.py)       → DB 연결 1개 빌림           │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ⑥ 요청 본문 검증     app/schemas.py  SaveRequest      │
│    JSON을 pydantic 모델로 → 틀리면 자동 422           │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ⑦ 라우터 함수        app/routers/sops.py             │
│    save_document()                                  │
│      ├ derive.validate_document()   문서 형식 검사     │
│      ├ common.get_document_or_404() 문서 존재 확인     │
│      └ _append_version()            ← 저장 본체       │
│            ├ FOR UPDATE 로 행 잠금                    │
│            ├ refs.resolve_references()  참조 정리     │
│            ├ derive.derive_flow_rows()  노드 추출     │
│            └ INSERT × 3 + UPDATE                    │
└─────────────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────────────┐
│ ⑧ 응답 직렬화        response_model=SaveResponse      │
│    모델에 정의된 필드만 골라 JSON으로                   │
└─────────────────────────────────────────────────────┘
      ▼
  브라우저   201  { "id": "...", "version_no": 4, "warnings": [] }
```

**어디서든 예외가 나면** → ⑦ 을 건너뛰고 `app/errors.py` 가 받아서 통일된 오류 JSON으로 바꿉니다.

## 2.2 파일 지도

```
sop/
├── app.py                  ① 시작 버튼 (uvicorn 실행)
├── app/
│   ├── main.py             ② 앱 조립 — 라우터 등록, 미들웨어, 헬스체크
│   ├── config.py           ⚙  환경변수 읽기
│   ├── db.py               🔌 DB 연결 풀
│   ├── deps.py             👤 "누가 요청했나"
│   ├── errors.py           ❗ 오류 형식 통일
│   ├── schemas.py          📋 요청/응답 모양  ← 프론트와의 계약서
│   ├── derive.py           🔍 문서 JSON 검증 + 값 추출 (순수 함수)
│   ├── refs.py             🔗 SOP 간 참조 관계
│   ├── routers/
│   │   ├── sops.py         📄 문서 목록/열기/저장/번호변경/폐기
│   │   ├── versions.py     📚 버전 목록/열기/비교  (읽기 전용)
│   │   ├── locks.py        🔒 편집 잠금
│   │   └── common.py       🧰 세 라우터 공용 함수
│   └── tools/
│       └── apply_schema.py 🛠 DB 테이블 생성 도구
├── sql/
│   ├── sop_schema.sql      🗄 전체 스키마 (새 설치용)
│   └── migrations/*.sql    🗄 기존 DB 변경용
└── static/
    └── SOP_STUDIO.html     🖥 프론트 (편집기) — 건드릴 일 없음
```

### 의존 방향

```
routers/  ──→  schemas, derive, refs, db, errors, deps
   │
refs      ──→  derive, schemas
derive    ──→  errors                    (DB를 모름 = 순수 함수)
db        ──→  config
```

**화살표가 한 방향입니다.** `derive.py` 는 DB를 모르고, `db.py` 는 라우터를 모릅니다.
이런 구조를 **계층 분리**라 하고, 덕분에 아래쪽(derive)을 테스트할 때 DB를 띄울 필요가 없습니다.

## 2.3 app/main.py — 조립 순서

```python
# 1. 설정 읽기
_settings = get_settings()

# 2. 앱 생성 + 수명주기(lifespan) 연결
app = FastAPI(title="SOP Studio API", version="2.0",
              lifespan=lifespan, root_path=_settings.root_path)

# 3. 오류 처리기 등록
install_error_handlers(app)

# 4. 미들웨어 (등록 순서 중요! 아래 참고)
@app.middleware("http")
async def log_and_limit(request, call_next): ...

if _settings.cors_origins:
    app.add_middleware(CORSMiddleware, ...)

# 5. 라우터 등록
API_GUARD = [Depends(current_user)]
app.include_router(sops.router,     prefix="/api", dependencies=API_GUARD)
app.include_router(versions.router, prefix="/api", dependencies=API_GUARD)
app.include_router(locks.router,    prefix="/api", dependencies=API_GUARD)

# 6. 헬스체크 2종
@app.get("/health")      # DB 안 봄 — 플랫폼 생존 확인용
@app.get("/api/health")  # DB 확인 — 죽었으면 503

# 7. 정적 파일 (편집기 HTML)
@app.get("/")
app.mount("/static", StaticFiles(...))
```

### 미들웨어 등록 순서의 함정

```python
# Starlette 은 "나중에 등록한 미들웨어가 가장 바깥"
```

`log_and_limit` 을 먼저, `CORSMiddleware` 를 나중에 등록했습니다. 그래서 실제 실행 순서는:

```
요청 →  CORS  →  log_and_limit  →  라우터
응답 ←  CORS  ←  log_and_limit  ←  라우터
```

CORS가 가장 바깥이라, `log_and_limit` 이 413으로 바로 되돌려보내는 응답에도 **CORS 헤더가 붙습니다**.
순서가 반대면 브라우저가 그 오류 내용을 읽지 못합니다.

### lifespan — 앱의 시작과 끝

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool(...)      # ← yield 앞: 앱 시작 시 1번
    yield                        # ← 여기서 앱이 돌아감
    await db.close_pool()        # ← yield 뒤: 앱 종료 시 1번
```

DB 연결 풀을 **요청마다 만들지 않고 앱 전체에 한 번** 만듭니다. 연결 여는 데 시간이 걸리기 때문입니다.

### 헬스체크가 2개인 이유

| 주소 | DB 확인 | 용도 |
|---|---|---|
| `/health` | ❌ | 컨테이너 플랫폼이 몇 초마다 두드림. **DB가 잠깐 흔들려도 서버를 죽이지 않기 위해** |
| `/api/health` | ✅ | 배포 후 사람이 직접 확인. DB 죽었으면 503 |

`/health` 가 DB를 확인하면, DB 재시작 중에 플랫폼이 "서버가 죽었다"고 판단해 컨테이너를 계속 재시작시킵니다.
그래서 **생존(liveness)과 준비(readiness)를 분리**합니다.

---

# 3장. 데이터 모델 — 무엇을 저장하는가

## 3.1 표 5개

```
sop_documents (문서)
   │  id(uuid, 영구)  sop_no(사람이 보는 번호, 바뀔 수 있음)
   │  name  area  status  current_version_id ──┐
   │                                            │ "지금 최신" 포인터
   ├──< sop_versions (버전 — 저장 1회 = 1행) <──┘
   │       │  version_no(1,2,3...)  content(jsonb, 편집기 JSON 통째)
   │       │  revision  owner  tags  change_note  saved_by  saved_at
   │       │
   │       ├──< flow_nodes  (순서도 상자 — content에서 뽑은 검색용 사본)
   │       └──< flow_edges  (순서도 연결선)
   │
   └──< sop_edit_locks (편집 잠금 — 문서당 최대 1행)
```

## 3.2 핵심 설계 원칙 4가지

### 원칙 1 — 편집기 복원은 `content` 하나로 끝난다

`sop_versions.content` 에 편집기가 보낸 JSON이 **통째로** 들어갑니다.
문서를 열 때는 이 컬럼 하나만 읽어서 그대로 프론트에 돌려줍니다.

> 왜? 프론트의 JSON 구조는 계속 바뀝니다. 백엔드가 그 구조를 하나하나 컬럼으로 나눠 놓으면
> 프론트가 필드 하나 추가할 때마다 DB 마이그레이션을 해야 합니다.
> **통째로 보관하면 프론트가 자유롭게 진화할 수 있습니다.**

### 원칙 2 — `flow_nodes` / `flow_edges` 는 "파생 사본"이다

저장할 때 서버가 `content` 안의 순서도를 풀어서 행으로 만들어 둡니다.
**편집기는 이 표를 절대 읽지 않습니다.** 오직 검색·통계·버전 비교용입니다.

```
"MES를 쓰는 SOP 전부 찾기"       → flow_nodes.systems 에 GIN 인덱스
"이 문서를 참조하는 SOP 찾기"     → flow_nodes.ref_document_id 에 인덱스
"v3 → v4 에서 뭐가 바뀌었나"      → flow_nodes 두 버전 비교
```

jsonb 안을 뒤지는 것보다 훨씬 빠릅니다.

> 이 패턴을 **비정규화(denormalization)** 또는 **읽기 모델(read model)** 이라 부릅니다.
> 원본은 하나, 조회용 사본은 별도. 사본이 깨져도 원본에서 다시 만들 수 있습니다.

### 원칙 3 — 버전 행은 불변(immutable)이다

저장할 때마다 **새 행을 추가**하고, 기존 행은 절대 고치지 않습니다.

```
저장 1회차 → sop_versions (version_no=1)  + flow_nodes 20행
저장 2회차 → sop_versions (version_no=2)  + flow_nodes 22행   ← 1번은 그대로 남음
저장 3회차 → sop_versions (version_no=3)  + flow_nodes 22행
              └ sop_documents.current_version_id 가 3번을 가리킴
```

SOP는 규제 문서라 "언제 누가 무엇을 바꿨나"가 남아야 합니다.
덕분에 **과거 버전 열기, 버전 비교, 되돌리기**가 공짜로 됩니다.

### 원칙 4 — 참조의 진짜 연결은 "번호"가 아니라 "id"다

순서도의 SOP 상자에는 3개 값이 있습니다:

| 컬럼 | 의미 |
|---|---|
| `ref_document_id` | 가리키는 문서의 **UUID = 진짜 연결** |
| `ref_sop_no` | 사람이 보는 번호 (표시용 캐시) |
| `ref_sop_name` | 사람이 보는 이름 (표시용 캐시) |

> **번호는 바뀝니다** (오타 수정, 체계 변경). 번호로 연결하면 번호를 바꾸는 순간 모든 참조가 끊깁니다.
> UUID는 절대 안 바뀌므로 UUID로 연결하고, 번호·이름은 **저장할 때마다 최신값으로 덮어씁니다.**

이 원칙 때문에 `PATCH /number` 가 버전을 전혀 건드리지 않아도 참조가 유지됩니다.

## 3.3 주요 제약과 인덱스

```sql
-- 제약 (DB가 강제하는 규칙)
sop_documents.sop_no              UNIQUE          -- 번호 중복 불가
sop_versions  (document_id, version_no)  UNIQUE   -- 같은 문서에 같은 버전번호 불가
flow_nodes    (version_id, instance_id, node_key) UNIQUE
area    CHECK IN ('', 'P','E','D','T','C')
status  CHECK IN ('draft','review','approved','retired')
node_type CHECK IN ('start','seq','decision','sop','end')

-- ON DELETE 동작
sop_versions.document_id  → ON DELETE CASCADE    -- 문서 지우면 버전도 같이
flow_nodes.version_id     → ON DELETE CASCADE
flow_nodes.ref_document_id → ON DELETE SET NULL  -- 참조 대상이 지워지면 연결만 끊김
sop_documents.current_version_id → ON DELETE SET NULL
```

> **CHECK 제약은 백엔드 코드와 반드시 같아야 합니다.**
> `derive.py` 의 `VALID_AREAS`, `NODE_TYPES` 가 SQL의 CHECK와 같은 값인지 항상 확인하세요.
> 어긋나면 파이썬은 통과시키는데 DB가 거부해서 500이 납니다.

주요 인덱스:
```sql
ix_documents_area_no        (area, sop_no)              -- 트리 정렬
ix_versions_doc             (document_id, version_no DESC) -- 버전 이력
ix_flow_nodes_ref_doc       (ref_document_id) WHERE NOT NULL  -- 부분 인덱스
ix_flow_nodes_systems       USING gin (systems)         -- JSON 안 검색
ix_documents_name_trgm      USING gin (name gin_trgm_ops) -- 한글 부분 검색
```

`WHERE ref_document_id IS NOT NULL` 같은 **부분 인덱스**는 NULL이 대부분인 컬럼에서 인덱스 크기를 확 줄입니다.

---

# 4장. 라우터 상세

## 4.0 라우터의 기본 문법

```python
router = APIRouter(tags=["sops"])          # ① 라우터 만들기

@router.get("/sops", response_model=list[DocumentSummary])   # ②
async def list_documents(                                     # ③
    status: str = "!retired",                                 # ④ 쿼리 파라미터
    conn: AsyncConnection = Depends(get_conn),                # ⑤ 의존성
):
    ...
```

| | 설명 |
|---|---|
| ① | `tags` 는 `/docs` 화면에서 묶어 보여줄 이름 |
| ② | HTTP 메서드 + 경로. `main.py`가 앞에 `/api` 를 붙임 |
| ③ | `async def` — DB 기다리는 동안 다른 요청 처리 가능 |
| ④ | 타입이 `str`이고 기본값이 있으면 **쿼리 파라미터** (`?status=all`) |
| ⑤ | `Depends(...)` 가 있으면 **의존성** — 프레임워크가 값을 넣어 줌 |

### 파라미터가 어디서 오는지 구분하는 규칙

```python
async def save_document(
    doc_id: UUID,                                    # ① 경로에 {doc_id}가 있으면 → 경로 파라미터
    body: SaveRequest,                               # ② pydantic 모델이면 → 요청 본문(JSON)
    q: str = "",                                     # ③ 나머지 기본타입 → 쿼리 파라미터
    user: str = Depends(current_user),               # ④ Depends → 의존성
    conn: AsyncConnection = Depends(get_conn),
):
```

FastAPI가 **타입과 위치를 보고 자동으로 판단**합니다. 이게 FastAPI의 가장 큰 장점입니다.

---

## 4.1 `sops.py` — 문서

> **파일 위치**: `app/routers/sops.py` (527줄)
> **담당**: 문서의 생성/조회/수정/폐기 전부. 이 프로젝트의 심장.

### 엔드포인트 요약

| # | 메서드 | 경로 | 함수 | 코드 |
|---|---|---|---|---|
| 1 | GET | `/api/sops` | `list_documents` | 200 |
| 2 | GET | `/api/sops/by-no/{sop_no}` | `open_document_by_no` | 200 |
| 3 | GET | `/api/sops/{doc_id}` | `open_document` | 200 |
| 4 | POST | `/api/sops` | `create_document` | **201** |
| 5 | PUT | `/api/sops/{doc_id}` | `save_document` | **201** |
| 6 | PATCH | `/api/sops/{doc_id}/number` | `rename_document` | 200 |
| 7 | DELETE | `/api/sops/{doc_id}` | `retire_document` | 200 |
| 8 | GET | `/api/sops/{doc_id}/referenced-by` | `list_referenced_by` | 200 |

### ⚠️ 경로 등록 순서

```python
@router.get("/sops/by-no/{sop_no}")   # ← 반드시 위
@router.get("/sops/{doc_id}")         # ← 아래
```

FastAPI는 **위에서부터 차례로** 매칭합니다.
순서가 반대면 `/api/sops/by-no/SOP-001` 요청이 `{doc_id}` 에 걸려서 `"by-no"` 를 UUID로 해석하려다 422가 납니다.

> **규칙: 고정 문자열 경로를 변수 경로보다 위에 둔다.**

---

### 4.1.1 목록 — `list_documents`

```python
@router.get("/sops", response_model=list[DocumentSummary])
async def list_documents(
    status: str = "!retired", q: str = "", area: str = "",
    conn: AsyncConnection = Depends(get_conn)
):
```

호출 예:
```
GET /api/sops                          전체(폐기 제외)
GET /api/sops?status=all               폐기 포함
GET /api/sops?status=approved          승인된 것만
GET /api/sops?q=ETCH                   번호/이름에 ETCH 포함
GET /api/sops?area=E&q=식각            AREA=E 이면서 "식각" 포함
```

#### 동적 WHERE 조립 — 백엔드의 기본기

```python
conditions = []
params = []

if status == "all":
    pass
elif status.startswith("!"):
    conditions.append("d.status <> %s")   # ← 문장 조각 (고정)
    params.append(status[1:])              # ← 값 (사용자 입력)
else:
    conditions.append("d.status = %s")
    params.append(status)

if q:
    conditions.append("(d.sop_no ILIKE %s OR d.name ILIKE %s)")
    like_pattern = "%" + escape_like(q) + "%"
    params.extend([like_pattern, like_pattern])   # %s 가 2개 → 값도 2개

where_sql = "WHERE " + " AND ".join(conditions) if conditions else ""
```

**🔴 가장 중요한 원칙: 사용자 입력은 절대 SQL 문자열에 붙이지 않는다.**

```python
# ❌ 절대 금지 — SQL 인젝션
sql = f"SELECT * FROM sop_documents WHERE status = '{status}'"
#   status 에 "x'; DROP TABLE sop_documents; --" 가 들어오면?

# ✅ 올바른 방법 — 자리표시자 + 값 분리
sql = "SELECT * FROM sop_documents WHERE status = %s"
await conn.execute(sql, [status])
```

자리표시자를 쓰면 DB 드라이버가 값을 **문장이 아닌 데이터로** 취급해서 안전합니다.

#### `escape_like` — 두 번째 방어선

```python
def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

`ILIKE` 에서 `%` = "아무 글자 여러 개", `_` = "아무 글자 하나"입니다.
사용자가 `SOP_ETCH` 로 검색하면 `_` 가 와일드카드로 해석돼서 `SOP-ETCH` 도 걸립니다.
앞에 `\` 를 붙이면 글자 그대로 찾습니다.

> 이건 보안 문제는 아니고 **검색 정확도** 문제입니다. 하지만 사용자는 "왜 이상한 게 나오지?"라고 느낍니다.

#### 최종 SQL

```sql
SELECT d.id, d.sop_no, d.name, d.area, d.status, d.updated_at,
       v.version_no,
       COALESCE(v.revision, '') AS revision,
       COALESCE(v.owner, '')    AS owner
  FROM sop_documents d
  LEFT JOIN sop_versions v ON v.id = d.current_version_id
 WHERE ...
 ORDER BY d.area, d.sop_no
```

| 요소 | 이유 |
|---|---|
| **`content` 없음** | 문서당 수백 KB(이미지 포함). 100개면 수십 MB → 서버 사망 |
| `LEFT JOIN` | 버전이 아직 없는 문서도 목록에 나오게 (INNER JOIN이면 사라짐) |
| `COALESCE(a,b)` | a가 NULL이면 b. 버전 없는 문서의 revision을 `''`로 |
| `ORDER BY area, sop_no` | 트리 정렬 순서. 인덱스 `ix_documents_area_no` 와 일치 |

> **🔑 목록 API의 철칙: 큰 컬럼은 절대 읽지 않는다.**
> 이 한 줄이 서버 성능의 80%를 좌우합니다.

```python
cur = await conn.execute(sql, params)
return await cur.fetchall()     # dict 목록 → response_model 이 알아서 변환
```

---

### 4.1.2 열기 — 두 입구, 한 몸통

```python
@router.get("/sops/by-no/{sop_no}", response_model=DocumentOpen)
async def open_document_by_no(sop_no: str, conn = Depends(get_conn)):
    cur = await conn.execute(
        "SELECT id, sop_no, current_version_id FROM sop_documents WHERE sop_no = %s", (sop_no,))
    return await load_document_open(conn, await cur.fetchone())

@router.get("/sops/{doc_id}", response_model=DocumentOpen)
async def open_document(doc_id: UUID, conn = Depends(get_conn)):
    cur = await conn.execute(
        "SELECT id, sop_no, current_version_id FROM sop_documents WHERE id = %s", (doc_id,))
    return await load_document_open(conn, await cur.fetchone())
```

**찾는 방법만 다르고 그 뒤는 똑같습니다.** 그래서 공통 부분을 함수로 뺐습니다.

#### `load_document_open` (`sops.py:71`)

```python
async def load_document_open(conn, doc_row: dict | None) -> DocumentOpen:
    # ① 없으면 404
    if doc_row is None or doc_row["current_version_id"] is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")

    # ② 현재 버전의 content 읽기
    cur = await conn.execute(
        "SELECT id, version_no, content FROM sop_versions WHERE id = %s",
        (doc_row["current_version_id"],))
    version_row = await cur.fetchone()
    if version_row is None:
        raise ApiError(404, "not_found", "문서의 현재 버전을 찾을 수 없습니다.")

    # ③ 번호 보정
    content = content_with_current_sop_no(version_row["content"], doc_row["sop_no"])

    # ④ 조립
    return DocumentOpen(
        id=doc_row["id"],
        sop_no=doc_row["sop_no"],
        version_no=version_row["version_no"],
        version_id=version_row["id"],
        lock=await find_active_lock(conn, doc_row["id"]),      # 지금 누가 편집 중?
        content=content,
        ref_docs=await load_ref_docs(conn, content),           # 참조 대상들의 현재 정보
    )
```

**`current_version_id IS NULL` 도 404로 취급합니다.** 문서 행은 있는데 버전이 하나도 없으면 열 게 없으니까요.

#### 번호 보정 — 미묘하지만 중요한 처리

```python
# app/routers/common.py:44
def content_with_current_sop_no(content: dict, sop_no: str) -> dict:
    if not isinstance(content, dict):
        return content
    sop = content.get("sop")
    if not isinstance(sop, dict) or sop.get("id") == sop_no:
        return content                      # 이미 같으면 그대로 (복사 안 함)
    fixed = dict(content)                   # 껍데기만 얕은 복사
    fixed["sop"] = dict(sop, id=sop_no)
    return fixed
```

**문제 상황:**
```
1. SOP-001 로 저장  → content.sop.id = "SOP-001"
2. PATCH /number 로 SOP-002 로 변경 → sop_documents.sop_no = "SOP-002"
                                      (content 는 안 건드림!)
3. 문서 열기 → content.sop.id 는 여전히 "SOP-001"
4. 편집기가 입력칸에 "SOP-001" 을 채움 → 사용자 혼란
```

**해결:** 응답을 만들 때만 맞춰서 내보냅니다. DB는 그대로 둡니다.

`dict(content)` 는 **얕은 복사(shallow copy)** 입니다. `blocks` 같은 큰 덩어리는 복사하지 않고 공유합니다.
`copy.deepcopy()` 를 썼다면 수백 KB를 매번 복사해서 느려집니다.

> 원본을 건드리지 않고 사본을 만드는 이 방식을 **불변(immutable) 처리**라 합니다.
> 실수로 DB에 반영되는 사고를 원천 차단합니다.

---

### 4.1.3 저장 — `_append_version` (가장 중요)

POST(새 문서)와 PUT(새 버전)이 **같은 몸통**을 씁니다.

```python
async def _append_version(
    conn, doc_id: UUID, body: SaveRequest, user: str, check_base_version: bool
) -> SaveResponse:
```

`check_base_version` 로 동작을 나눕니다:
- `True` (PUT) → 번호 일치 검사 + 버전 충돌 검사
- `False` (POST) → 건너뜀 (새 문서라 비교 대상이 없음)

> **파라미터로 동작을 나누는 이유**: 두 함수로 복사하면 나중에 저장 로직을 고칠 때 한쪽만 고치는 사고가 납니다.

#### 단계 a — 행 잠금

```python
cur = await conn.execute(
    "SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
doc_row = await cur.fetchone()
if doc_row is None:
    raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
```

**`FOR UPDATE`** = "이 행을 내 트랜잭션이 끝날 때까지 잠근다".
같은 문서를 동시에 저장하려는 다른 요청은 **여기서 줄을 섭니다.**

```
요청 A: FOR UPDATE ─── 작업 ─── COMMIT
요청 B:      └ 대기 ──────────────┘ → 여기서 통과
```

> 주석에 있는 대로, PostgreSQL은 `MAX()` 같은 집계함수와 `FOR UPDATE` 를 한 문장에 못 씁니다.
> 그래서 문서 행을 먼저 잠그고, 최대 버전 번호는 따로 구합니다.

#### 단계 b — 잠금 아래에서 재검사

```python
# b-1. 번호 일치 (PUT만)
if check_base_version and meta.sop_no != sop_no:
    raise ApiError(400, "sop_no_mismatch",
        f"저장된 SOP 번호({sop_no})와 문서 안의 번호({meta.sop_no})가 다릅니다.",
        stored_sop_no=sop_no, doc_sop_no=meta.sop_no,
        hint="번호 변경은 PATCH /api/sops/{id}/number 를 쓰세요")

# 최대 버전 번호
cur = await conn.execute(
    "SELECT COALESCE(MAX(version_no), 0) AS max_no FROM sop_versions WHERE document_id = %s",
    (doc_id,))
max_no = (await cur.fetchone())["max_no"]

# b-2. 버전 충돌 (PUT만)
if check_base_version and body.base_version_no is not None and body.base_version_no != max_no:
    raise ApiError(409, "version_conflict",
        f"다른 사람이 먼저 v{max_no} 을(를) 저장했습니다. 다시 불러온 뒤 저장하세요.",
        current_version_no=max_no)

new_version_no = max_no + 1
```

**왜 두 번 검사하나?**
`save_document` 가 트랜잭션 **밖에서** 이미 번호를 확인했지만, 그 사이 다른 요청의 `PATCH /number` 가 번호를 바꿨을 수 있습니다.
**잠금 아래에서 하는 검사만이 진짜 보장입니다.** 밖의 검사는 "빠른 실패"용입니다.

#### 낙관적 잠금(optimistic locking)

`base_version_no` 검사가 그것입니다:

```
1. hong 이 v3 을 연다   →  프론트가 base_version_no=3 을 기억
2. kim 이 v3 을 연다    →  프론트가 base_version_no=3 을 기억
3. kim 이 저장          →  max_no=3 == base 3  ✅  → v4 생성
4. hong 이 저장         →  max_no=4 != base 3  ❌  → 409 version_conflict
```

hong의 프론트는 409를 받고 "다른 사람이 먼저 저장했습니다. 다시 불러오시겠습니까?"를 띄웁니다.

**강제 저장**을 하려면 `base_version_no: null` 로 보내면 검사를 건너뜁니다.

> **비관적 잠금(pessimistic)** = 미리 문을 잠그기 = `locks.py` 의 편집 잠금
> **낙관적 잠금(optimistic)** = 일단 하고 충돌하면 거절 = `base_version_no`
>
> 이 프로젝트는 **둘 다** 씁니다. 잠금은 "예의"(안내용, 저장을 막지는 않음)이고, 버전 검사가 "진짜 방어"입니다.

#### 단계 c, d — 참조 정리 + 노드 추출

```python
refs_summary, ref_warnings = await resolve_references(conn, doc)   # doc 이 제자리에서 수정됨!
node_rows, edge_rows, row_warnings = derive_flow_rows(doc)
warnings = meta.warnings + ref_warnings + row_warnings
```

**순서가 중요합니다.** `resolve_references` 가 `doc` 안의 `sop_id`/`ref_document_id` 를 최신값으로 고치고,
그 **고쳐진** `doc` 에서 노드를 뽑아야 DB에도 최신값이 들어갑니다.

#### 단계 e — INSERT

```python
cur = await conn.execute(
    "INSERT INTO sop_versions (document_id, version_no, format, format_version, content, "
    "                          revision, owner, tags, change_note, saved_by) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, saved_at",
    (doc_id, new_version_no, meta.format, meta.format_version, Jsonb(doc),
     meta.revision, meta.owner, meta.tags, body.change_note, saved_by))
version_row = await cur.fetchone()
version_id = version_row["id"]
```

| 기법 | 설명 |
|---|---|
| `Jsonb(doc)` | 파이썬 dict → PostgreSQL jsonb. 이 래퍼가 없으면 드라이버가 타입을 모름 |
| `RETURNING id, saved_at` | INSERT 하면서 결과를 바로 받음. **SELECT 한 번을 아낌** |

#### `executemany` — 여러 행 한 번에

```python
node_params = []
for node in node_rows:
    node_params.append((version_id, node["instance_id"], ..., Jsonb(node["systems"]), ...))

if node_params:                          # 0개면 건너뜀
    async with conn.cursor() as cur:     # executemany 는 커서가 필요
        await cur.executemany(
            "INSERT INTO flow_nodes (version_id, instance_id, ...) VALUES (%s, %s, ...)",
            node_params)
```

노드가 50개면 `execute` 50번 = 왕복 50회. `executemany` 는 **왕복 1회**입니다.

> `if node_params:` 체크를 빼면 빈 리스트로 `executemany` 를 호출해 드라이버에 따라 오류가 납니다.

#### 폐기 문서 되살리기

```python
new_status = doc_row["status"]
if new_status == "retired":
    new_status = "draft"
    warnings.append(REVIVED_WARNING)   # "폐기됐던 문서를 다시 살렸습니다 (draft)"
```

폐기된 문서에 저장하면 자동으로 `draft` 로 되살립니다.
그대로 두면 **201 성공인데 트리에는 안 보이는** 이상한 상태가 됩니다.

#### 포인터 이동

```python
await conn.execute(
    "UPDATE sop_documents SET current_version_id = %s, name = %s, area = %s, "
    "status = %s, updated_at = now() WHERE id = %s",
    (version_id, meta.name, meta.area, new_status, doc_id))
```

이 `UPDATE` 가 **"최신 버전"을 바꾸는 순간**입니다. 이게 실행돼야 비로소 새 버전이 보입니다.

#### 로그

```python
log.info("saved %s v%s nodes=%s edges=%s refs=%s/%s/%s status=%s by %s",
    sop_no, new_version_no, len(node_rows), len(edge_rows),
    refs_summary.linked, refs_summary.pending, refs_summary.empty, new_status, saved_by)
```

> **`log.info("...%s", 값)` 처럼 쓰세요. `f"...{값}"` 이 아니라.**
> 로그 레벨이 INFO보다 높으면 문자열을 아예 만들지 않아 더 빠릅니다.

---

### 4.1.4 POST — `create_document`

```python
@router.post("/sops", response_model=SaveResponse, status_code=201)
async def create_document(body: SaveRequest, user = Depends(current_user), conn = Depends(get_conn)):
    # a. 문서 형식 검사 (틀리면 422)
    validate_document(body.doc)
    meta = document_meta(body.doc)

    # 사용자 이름을 한 번만 정한다
    who = body.saved_by.strip() or user

    # b. 번호 중복 검사 (1차)
    await _raise_if_sop_no_taken(conn, meta.sop_no)

    # c. 트랜잭션
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO sop_documents (sop_no, name, area, created_by) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (meta.sop_no, meta.name, meta.area, who))
            doc_id = (await cur.fetchone())["id"]
            response = await _append_version(conn, doc_id, body, who, check_base_version=False)
    except psycopg.errors.UniqueViolation:
        await _raise_if_sop_no_taken(conn, meta.sop_no)   # 2차 → 친절한 409
        raise

    # d. 트랜잭션 밖에서 잠금 경고
    response.warning = await _other_users_lock_warning(conn, doc_id, who)
    return response
```

#### `who` 를 한 번만 정하는 이유

```python
who = body.saved_by.strip() or user
```

이 값이 `created_by`, `saved_by`, 잠금 비교에 **모두 같은 값**으로 쓰여야 합니다.
각 자리에서 따로 계산하면 어딘가는 헤더 값, 어딘가는 본문 값이 들어가서 잠금이 엉킵니다.

#### 경쟁 조건 이중 방어

```
시각    요청 A                      요청 B
 t1     번호 검사 → 없음 ✅
 t2                                 번호 검사 → 없음 ✅
 t3     INSERT 성공
 t4                                 INSERT → UNIQUE 위반 💥
```

t4에서 그냥 두면 **500 Internal Error**가 납니다. 사용자는 영문을 모릅니다.
그래서 `UniqueViolation` 을 잡아서 다시 조회하고 **친절한 409 + 기존 문서 id**를 돌려줍니다:

```python
async def _raise_if_sop_no_taken(conn, sop_no: str) -> None:
    cur = await conn.execute("SELECT id, sop_no FROM sop_documents WHERE sop_no = %s", (sop_no,))
    existing = await cur.fetchone()
    if existing is not None:
        raise ApiError(409, "sop_no_taken",
            f"SOP 번호 {existing['sop_no']} 은(는) 이미 라이브러리에 있습니다. "
            f"그 문서의 새 버전으로 저장하려면 PUT /api/sops/{existing['id']} 를 쓰세요.",
            existing_id=str(existing["id"]), existing_sop_no=existing["sop_no"])
```

**오류에 `existing_id` 를 담아주는 것**이 좋은 API 설계입니다.
프론트는 이걸로 "그 문서의 새 버전으로 저장할까요?" → `PUT /api/sops/{existing_id}` 를 바로 할 수 있습니다.

> **일반 원칙: "안 됩니다"로 끝내지 말고 "대신 이렇게 하세요"를 주세요.**

#### 잠금 경고가 트랜잭션 밖인 이유

```python
response.warning = await _other_users_lock_warning(conn, doc_id, who)
```

이건 **안내일 뿐 저장을 막지 않습니다.** 트랜잭션 안에 넣으면 불필요하게 잠금 시간이 길어집니다.
트랜잭션은 **짧을수록 좋습니다.**

---

### 4.1.5 PUT — `save_document`

```python
@router.put("/sops/{doc_id}", response_model=SaveResponse, status_code=201)
async def save_document(doc_id: UUID, body: SaveRequest,
                        user = Depends(current_user), conn = Depends(get_conn)):
    validate_document(body.doc)
    meta = document_meta(body.doc)
    who = body.saved_by.strip() or user

    # 빠른 실패 (트랜잭션 밖)
    doc_row = await get_document_or_404(conn, doc_id)
    if meta.sop_no != doc_row["sop_no"]:
        raise ApiError(400, "sop_no_mismatch", ...)

    # 저장 본체
    async with conn.transaction():
        response = await _append_version(conn, doc_id, body, who, check_base_version=True)

    response.warning = await _other_users_lock_warning(conn, doc_id, who)
    return response
```

#### 왜 번호가 다르면 400인가?

```
편집기에서 번호를 SOP-001 → SOP-002 로 고치고 저장을 누르면?
  → 서버가 "번호를 바꾸겠다는 건가, 다른 문서로 저장하겠다는 건가?" 를 알 수 없음
```

그래서 **저장과 번호 변경을 분리**했습니다.
프론트는 번호가 바뀌었으면 `PATCH /number` 를 먼저 호출하고, 그다음 `PUT` 으로 저장합니다.

**한 API가 두 가지 일을 하게 만들지 마세요.** 애매한 상황이 반드시 생깁니다.

---

### 4.1.6 PATCH — `rename_document`

```python
@router.patch("/sops/{doc_id}/number", response_model=RenameResponse)
async def rename_document(doc_id: UUID, body: RenameRequest, user = Depends(current_user), conn = ...):
    who = body.user.strip() or user
    new_sop_no = body.sop_no.strip()

    # ① 형식 검사
    if new_sop_no == "" or re.match(SOP_NO_PATTERN, new_sop_no) is None:
        raise ApiError(422, "invalid_document",
            f"SOP 번호 '{new_sop_no}' 는 비어 있거나 허용되지 않는 글자가 있습니다.")

    try:
        async with conn.transaction():
            # ② 행 잠금 + 존재 확인
            cur = await conn.execute(
                "SELECT id, sop_no FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
            doc_row = await cur.fetchone()
            if doc_row is None:
                raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
            old_sop_no = doc_row["sop_no"]

            # ③ 남의 잠금이면 423
            cur = await conn.execute(
                "SELECT locked_by, expires_at FROM sop_edit_locks "
                "WHERE document_id = %s AND locked_by <> %s AND expires_at > now()", (doc_id, who))
            lock_row = await cur.fetchone()
            if lock_row is not None:
                raise ApiError(423, "locked",
                    f"{lock_row['locked_by']} 님이 편집 중이라 번호를 바꿀 수 없습니다.",
                    locked_by=lock_row["locked_by"], expires_at=to_utc_z(lock_row["expires_at"]))

            pending_old = 0
            if new_sop_no != old_sop_no:            # ④ 같은 번호면 아무것도 안 함
                # ⑤ 중복 검사
                cur = await conn.execute("SELECT id FROM sop_documents WHERE sop_no = %s", (new_sop_no,))
                taken = await cur.fetchone()
                if taken is not None:
                    raise ApiError(409, "sop_no_taken", ..., existing_id=str(taken["id"]))

                # ⑥ 따라오지 못할 참조 수를 미리 센다 (로그용)
                pending_old = await count_number_only_references(conn, doc_id, old_sop_no)

                # ⑦ 번호만 UPDATE
                await conn.execute(
                    "UPDATE sop_documents SET sop_no = %s, updated_at = now() WHERE id = %s",
                    (new_sop_no, doc_id))
    except psycopg.errors.UniqueViolation:
        ...   # 경쟁 조건 → 친절한 409

    referenced_by = await count_referenced_by(conn, doc_id, new_sop_no)
    return RenameResponse(id=doc_id, sop_no=new_sop_no, old_sop_no=old_sop_no,
                          referenced_by=referenced_by)
```

#### 핵심: 버전을 건드리지 않는다

```
sop_documents.sop_no      ← 바뀜
sop_documents.updated_at  ← 바뀜
sop_versions.*            ← 전혀 안 바뀜
```

**3장 원칙 4 덕분입니다.** 참조가 `ref_document_id`(UUID)로 걸려 있으니 번호가 바뀌어도 연결이 유지됩니다.
다른 문서를 다음에 열거나 저장할 때 자동으로 새 번호가 보입니다.

#### 예외: "번호만 적힌" 참조는 따라오지 못한다

```python
pending_old = await count_number_only_references(conn, doc_id, old_sop_no)
```

`ref_document_id` 가 NULL이고 `ref_sop_no` 에 글자만 있는 상자(= "미작성" 참조)는
번호가 바뀌면 **끊깁니다.** 이건 막을 방법이 없어서 **로그에만 남깁니다.**

```python
log.info("renamed %s -> %s (referenced_by=%s, 옛 번호를 글자로만 참조하던 문서=%s) by %s", ...)
```

> **막을 수 없는 문제는 최소한 기록으로 남기세요.** 나중에 "왜 참조가 끊겼지?"를 추적할 수 있습니다.

#### 같은 번호로 요청하면?

```python
if new_sop_no != old_sop_no:
    ...
```

아무것도 바꾸지 않고 200을 돌려줍니다. **멱등성(idempotent)** — 여러 번 불러도 결과가 같습니다.
프론트가 "번호 안 바뀌었는데 굳이 호출 안 해야지"를 신경 쓸 필요가 없어집니다.

---

### 4.1.7 DELETE — `retire_document`

```python
@router.delete("/sops/{doc_id}", response_model=StatusResponse)
async def retire_document(doc_id: UUID, conn = Depends(get_conn)):
    cur = await conn.execute(
        "UPDATE sop_documents SET status = 'retired', updated_at = now() "
        "WHERE id = %s RETURNING id, sop_no", (doc_id,))
    row = await cur.fetchone()
    if row is None:
        raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
    referenced_by = await count_referenced_by(conn, row["id"], row["sop_no"])
    return StatusResponse(id=row["id"], status="retired", referenced_by=referenced_by)
```

#### DELETE인데 UPDATE

SOP는 **규제 문서**입니다. "3년 전 그 절차가 뭐였나"에 답할 수 있어야 합니다.
그래서 행을 지우지 않고 `status` 만 바꿉니다. 이걸 **소프트 삭제(soft delete)** 라 합니다.

되살리기는 별도 API 없이 **그냥 저장하면** 됩니다 (`_append_version` 의 되살리기 로직).

#### `RETURNING` 으로 존재 확인

```python
"UPDATE ... WHERE id = %s RETURNING id, sop_no"
```

없는 문서면 `RETURNING` 이 아무것도 안 돌려줍니다 → `fetchone()` 이 `None` → 404.
**SELECT로 먼저 확인하고 UPDATE 하는 것보다 왕복이 1회 적고, 그 사이 삭제되는 경쟁 조건도 없습니다.**

#### `referenced_by` 를 주는 이유

이 문서를 참조하는 다른 문서가 5건이면, 폐기 후 그 5건은 "폐기된 문서를 가리킴" 상태가 됩니다.
프론트가 "5개 문서가 이 SOP를 참조합니다. 폐기하시겠습니까?"를 띄울 수 있게 개수를 줍니다.

---

### 4.1.8 GET — `list_referenced_by`

```python
@router.get("/sops/{doc_id}/referenced-by", response_model=list[ReferencedBy])
async def list_referenced_by(doc_id: UUID, conn = Depends(get_conn)):
    doc_row = await get_document_or_404(conn, doc_id)
    rows = await find_referenced_by(conn, doc_row["id"], doc_row["sop_no"])
    return [ReferencedBy(**row) for row in rows]
```

`ReferencedBy(**row)` — dict를 키워드 인자로 펼치는 문법입니다.
`{"id": 1, "sop_no": "A"}` → `ReferencedBy(id=1, sop_no="A")`.

실제 SQL은 `refs.py` 에 있습니다 (5.2절 참고).

---

## 4.2 `versions.py` — 버전 (읽기 전용)

> **파일**: `app/routers/versions.py` (200줄)
> **특징**: 이 파일은 **쓰기를 전혀 하지 않습니다.** 저장은 전부 `sops.py` 담당.

| 메서드 | 경로 | 함수 |
|---|---|---|
| GET | `/api/sops/{doc_id}/versions` | `list_versions` |
| GET | `/api/sops/{doc_id}/versions/{version_no}` | `open_version` |
| GET | `/api/sops/{doc_id}/versions/{a}/diff/{b}` | `diff_versions` |

> **읽기와 쓰기를 파일로 분리**하면 "저장 로직이 어디 있지?"를 찾을 때 헤매지 않습니다.

### 4.2.1 버전 목록

```python
@router.get("/sops/{doc_id}/versions", response_model=list[VersionSummary])
async def list_versions(doc_id: UUID, conn = Depends(get_conn)):
    await get_document_or_404(conn, doc_id)      # 없는 문서면 404
    cur = await conn.execute(
        "SELECT version_no, revision, saved_by, saved_at, change_note "
        "FROM sop_versions WHERE document_id = %s ORDER BY version_no DESC", (doc_id,))
    return await cur.fetchall()
```

- **`content` 제외** — 목록 API의 철칙 (4.1.1 참고)
- `ORDER BY version_no DESC` — 최신부터. 인덱스 `ix_versions_doc (document_id, version_no DESC)` 와 일치
- `await get_document_or_404(...)` — 반환값을 안 씁니다. **오직 404를 내기 위해** 호출합니다.
  이게 없으면 없는 문서에 빈 배열 `[]` 을 돌려줘서 "버전이 없는 문서"와 구분이 안 됩니다.

### 4.2.2 특정 버전 열기

```python
@router.get("/sops/{doc_id}/versions/{version_no}", response_model=DocumentOpen)
async def open_version(doc_id: UUID, version_no: int, conn = Depends(get_conn)):
    document = await get_document_or_404(conn, doc_id)
    cur = await conn.execute(
        "SELECT id, version_no, content FROM sop_versions "
        "WHERE document_id = %s AND version_no = %s", (doc_id, version_no))
    version = await cur.fetchone()
    if version is None:
        raise ApiError(404, "version_not_found", f"{version_no}번 버전이 없습니다.", version_no=version_no)
    ...
    return DocumentOpen(...)   # 최신 열기와 완전히 같은 모양
```

**응답 모양이 `GET /sops/{id}` 와 똑같습니다** (`DocumentOpen`).
프론트는 "최신을 열든 v2를 열든 처리 코드가 하나"면 됩니다.

> **비슷한 일을 하는 API는 응답 모양을 통일하세요.** 프론트 코드가 절반으로 줍니다.

오류 코드를 `not_found` 가 아니라 `version_not_found` 로 나눈 것도 포인트입니다.
프론트가 "문서가 없다" vs "그 버전이 없다"를 구분해서 다른 안내를 할 수 있습니다.

### 4.2.3 버전 비교 — `diff_versions`

이 프로젝트에서 **SQL이 아닌 파이썬 로직**이 가장 많은 함수입니다.

#### 준비 — dict로 인덱싱

```python
async def load_nodes_of_version(conn, doc_id: UUID, version_no: int) -> dict:
    cur = await conn.execute(
        "SELECT id FROM sop_versions WHERE document_id = %s AND version_no = %s",
        (doc_id, version_no))
    version = await cur.fetchone()
    if version is None:
        raise ApiError(404, "version_not_found", ...)

    cur = await conn.execute(
        "SELECT instance_id, node_key, node_type, name, role_owner, action, description, "
        "systems, manual, ref_sop_no, ref_sop_name "
        "FROM flow_nodes WHERE version_id = %s ORDER BY instance_id, node_key", (version["id"],))
    node_rows = await cur.fetchall()

    nodes_by_key = {}
    for node_row in node_rows:
        key = (node_row["instance_id"], node_row["node_key"])   # 튜플이 열쇠
        nodes_by_key[key] = node_row
    return nodes_by_key
```

**열쇠가 `(instance_id, node_key)` 튜플인 이유**:
한 문서에 순서도가 여러 장일 수 있습니다. `node_key` 만으로는 다른 장의 같은 이름 노드와 헷갈립니다.

**왜 dict인가?** 리스트로 비교하면 매번 전체를 훑어서 O(n²)입니다. dict는 열쇠로 바로 찾아 O(n)입니다.
노드 100개면 10,000번 vs 100번 — 100배 차이입니다.

#### 비교 로직

```python
added, removed, changed = [], [], []

# ① b에만 있음 = 추가됨
for key, node_b in nodes_b.items():
    if key not in nodes_a:
        added.append(node_summary(node_b))

# ② a에만 있음 = 삭제됨
for key, node_a in nodes_a.items():
    if key not in nodes_b:
        removed.append(node_summary(node_a))

# ③ 양쪽 다 있음 = 항목별 비교
for key, node_a in nodes_a.items():
    if key not in nodes_b:
        continue
    node_b = nodes_b[key]
    changed_fields = {}
    for field_name in COMPARE_FIELDS:
        if node_a[field_name] != node_b[field_name]:
            changed_fields[field_name] = {"from": node_a[field_name], "to": node_b[field_name]}
    if changed_fields:
        changed.append({
            "instance_id": node_b["instance_id"],
            "node_key": node_b["node_key"],
            "node_type": node_b["node_type"],
            "fields": changed_fields,
        })

return {"a": a, "b": b, "added": added, "removed": removed, "changed": changed}
```

#### 설계 판단 — 무엇을 "변경"으로 볼 것인가

```python
COMPARE_FIELDS = [
    "node_type", "name", "role_owner", "action", "description",
    "systems", "manual", "ref_sop_no", "ref_sop_name",
]
# position(좌표)과 font_size(글자 크기)는 일부러 뺐습니다.
```

**상자를 옮기거나 글자 크기만 바꾼 건 절차가 바뀐 게 아닙니다.**
이걸 변경으로 세면 "검토용 diff"가 좌표 변경으로 도배돼서 쓸모가 없어집니다.

> **이건 기술 판단이 아니라 업무 판단입니다.**
> 백엔드 일의 상당 부분이 이런 판단입니다. "기술적으로 다르다"와 "업무적으로 의미 있게 다르다"는 다릅니다.

---

## 4.3 `locks.py` — 편집 잠금

> **파일**: `app/routers/locks.py` (133줄)

| 메서드 | 경로 | 함수 | 용도 |
|---|---|---|---|
| POST | `/api/sops/{doc_id}/lock` | `acquire_lock` | 잠금 잡기 **+ 연장** |
| DELETE | `/api/sops/{doc_id}/lock` | `release_lock` | 잠금 풀기 |

### 4.3.1 작동 방식 — 하트비트

```
편집 시작  →  POST /lock  { ttl_sec: 120 }     → expires_at = now + 120초
   │
   ├ 60초 후  POST /lock (같은 요청)            → expires_at = now + 120초  (연장)
   ├ 60초 후  POST /lock                        → 연장
   │
편집 종료  →  DELETE /lock                     → 행 삭제
```

**만료 시각을 두는 이유**: 브라우저를 그냥 닫거나, 인터넷이 끊기거나, 컴퓨터가 꺼지면
`DELETE` 가 안 옵니다. 만료가 없으면 그 문서는 **영원히 잠깁니다.**

`ttl_sec` 기본 120초에 60초마다 연장 — 하트비트 한 번을 놓쳐도 버틸 여유를 둔 것입니다.

### 4.3.2 잠금 잡기

```python
@router.post("/sops/{doc_id}/lock", response_model=LockInfo)
async def acquire_lock(doc_id: UUID, body: LockRequest, conn = ..., user = Depends(current_user)):
    await get_document_or_404(conn, doc_id)
    who = body.user.strip() or user
    ttl_sec = body.ttl_sec

    async with conn.transaction():
        # ★ 핵심: 문서 행에서 먼저 줄을 세운다
        await conn.execute("SELECT id FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))

        cur = await conn.execute(
            "SELECT locked_by, expires_at, (expires_at > now()) AS is_active "
            "FROM sop_edit_locks WHERE document_id = %s FOR UPDATE", (doc_id,))
        existing = await cur.fetchone()

        # 남의 잠금이 살아 있으면 423
        if existing is not None and existing["is_active"] and existing["locked_by"] != who:
            raise ApiError(423, "locked", f"{existing['locked_by']} 님이 편집 중입니다.",
                locked_by=existing["locked_by"], expires_at=to_utc_z(existing["expires_at"]))

        if existing is None:
            cur = await conn.execute(
                "INSERT INTO sop_edit_locks (document_id, locked_by, expires_at) "
                "VALUES (%s, %s, now() + make_interval(secs => %s)) "
                "RETURNING locked_by, expires_at", (doc_id, who, ttl_sec))
        else:
            cur = await conn.execute(
                "UPDATE sop_edit_locks SET locked_by = %s, locked_at = now(), "
                "expires_at = now() + make_interval(secs => %s) "
                "WHERE document_id = %s RETURNING locked_by, expires_at", (who, ttl_sec, doc_id))
        lock_row = await cur.fetchone()

    return LockInfo(locked_by=lock_row["locked_by"], expires_at=lock_row["expires_at"])
```

#### 통과 규칙

| 상황 | 결과 |
|---|---|
| 잠금 없음 | ✅ 잡는다 (INSERT) |
| 내 잠금 | ✅ 연장 (UPDATE) |
| 남의 잠금인데 **만료됨** | ✅ 뺏는다 (UPDATE) |
| 남의 잠금이 **유효** | ❌ 423 locked |

#### ★ "없는 것은 잠글 수 없다" — 이 프로젝트에서 가장 미묘한 버그

```python
# 먼저 "문서 행" 에서 줄을 세운다(FOR UPDATE)
await conn.execute("SELECT id FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
```

**이 한 줄이 없으면 이런 일이 일어납니다:**

```
잠금 행이 아직 없는 문서에 두 사람이 동시에 첫 잠금 요청

요청 A: SELECT ... FOR UPDATE  → 행이 없음 (잠글 게 없으니 그냥 통과)
요청 B: SELECT ... FOR UPDATE  → 행이 없음 (역시 통과)
요청 A: INSERT  → 성공
요청 B: INSERT  → PRIMARY KEY 중복 💥 500 에러
```

`SELECT ... FOR UPDATE` 는 **존재하는 행만** 잠급니다. 없는 행은 잠글 수 없습니다.

**해결:** 항상 존재하는 `sop_documents` 행에서 먼저 줄을 세웁니다.
그러면 A가 트랜잭션을 끝낼 때까지 B는 대기하고, B가 들어올 때는 이미 잠금 행이 있어서 UPDATE 경로로 갑니다.

> **일반화: "이 행이 없을 수도 있다"면 그 행에 FOR UPDATE를 걸어봐야 소용없습니다.
> 항상 존재하는 부모 행(여기선 문서)에 걸어야 합니다.**

#### SQL 표현 2가지

```sql
(expires_at > now()) AS is_active
```
계산을 **DB에서** 합니다. 파이썬으로 가져와서 비교하면 파이썬 시계와 DB 시계가 달라 어긋날 수 있습니다.

```sql
now() + make_interval(secs => %s)
```
만료 시각을 **DB의 `now()`** 로 계산합니다. 서버가 여러 대여도 시계가 하나로 통일됩니다.
`=>` 는 SQL 함수 인자를 이름으로 넘기는 표기입니다.

> **🔑 시간 계산은 항상 DB에서. 파이썬의 `datetime.now()` 를 쓰지 마세요.**

### 4.3.3 잠금 풀기

```python
@router.delete("/sops/{doc_id}/lock", response_model=StatusResponse)
async def release_lock(
    doc_id: UUID,
    body: UnlockRequest | None = None,                    # 본문이 없을 수도 있음
    user_query: str = Query(default="", alias="user"),    # ?user=hong
    conn = Depends(get_conn),
    header_user: str = Depends(current_user),             # X-User 헤더
):
    await get_document_or_404(conn, doc_id)

    who = ""
    if body is not None:
        who = body.user.strip()
    if not who:
        who = user_query.strip()
    if not who:
        who = header_user

    await conn.execute(
        "DELETE FROM sop_edit_locks WHERE document_id = %s AND locked_by = %s", (doc_id, who))
    return StatusResponse(id=doc_id, status="unlocked")
```

#### 사용자 이름을 3곳에서 받는 이유

```
① 본문 JSON  { "user": "hong" }
② 쿼리       ?user=hong
③ 헤더       X-User: hong
```

**DELETE 요청에 본문을 못 싣는 클라이언트/프록시가 있습니다.**
특히 브라우저가 탭을 닫을 때 보내는 `navigator.sendBeacon` 은 본문 형식에 제약이 있습니다.
그래서 세 경로를 다 열어두고 우선순위를 정했습니다.

```python
user_query: str = Query(default="", alias="user")
```
`alias` = **주소에서는 `user`, 파이썬 변수는 `user_query`**.
`user` 라는 이름이 이미 헤더용으로 쓰이고 있어서 충돌을 피한 것입니다.

#### 멱등성 — 항상 200

```python
"DELETE FROM sop_edit_locks WHERE document_id = %s AND locked_by = %s"
```

- 이미 풀려 있으면 → 지워지는 행 0개 → 그래도 200
- 남의 잠금이면 → `locked_by` 가 안 맞아 0개 → 그래도 200 (남의 잠금은 안전하게 보존)

**왜 오류를 안 내나?** 브라우저 닫을 때 보내는 요청은 **재시도할 수 없습니다.**
실패하면 잠금이 남고, 만료까지 다른 사람이 못 엽니다. 그래서 "여러 번 불러도 안전"하게 만들었습니다.

> **DELETE는 멱등하게 설계하세요.** 같은 요청을 10번 보내도 결과가 같아야 합니다.

### 4.3.4 잠금은 "예의"다 — 중요

**저장 API(`PUT`)는 잠금을 확인하지 않습니다.** 다른 사람이 잠갔어도 저장은 됩니다.
대신 경고 문구만 붙습니다:

```python
response.warning = await _other_users_lock_warning(conn, doc_id, who)
# "kim 님이 이 문서를 편집 중입니다 (잠금 만료: 2026-09-20T05:12:00Z)"
```

**진짜 방어는 `base_version_no`(낙관적 잠금)입니다.**

| 장치 | 성격 | 막는 것 |
|---|---|---|
| 편집 잠금 | 예의 (advisory) | 동시 편집 **시도**를 줄임 |
| `base_version_no` | 강제 | 덮어쓰기를 **확실히** 막음 |

> 잠금만 믿으면 안 되는 이유: 만료되거나, 네트워크가 끊기거나, 브라우저가 죽으면 잠금은 사라집니다.
> **데이터 무결성은 잠금이 아니라 버전 검사가 보장합니다.**

---

# 5장. 지원 모듈 상세

## 5.1 `derive.py` — 문서 JSON 다루기 (385줄)

> **성격: 순수 함수 모음.** DB도, FastAPI도 모릅니다. 입력 → 출력만 합니다.
> 그래서 **테스트할 때 DB를 띄울 필요가 없습니다.**

### 역할 4가지

| 함수 | 역할 |
|---|---|
| `validate_document(doc)` | 최소 형식 검사. 틀리면 422 |
| `document_meta(doc)` | 번호·이름·AREA·개정·작성자·태그 추출 |
| `derive_flow_rows(doc)` | 순서도 → `flow_nodes`/`flow_edges` 행 |
| `iter_sop_nodes(doc)` | SOP 상자만 골라 반복 |

### 상수 — DB와 반드시 일치해야 함

```python
SOP_DOC_FORMAT = "sop-editor-mock"
VALID_AREAS = ("", "P", "E", "D", "T", "C")
NODE_TYPES = ("start", "seq", "decision", "sop", "end")
SOP_NO_PATTERN = r"^[A-Za-z0-9._-]+$"
INT4_MIN = -2_147_483_648
INT4_MAX = 2_147_483_647
```

> ⚠️ `VALID_AREAS`, `NODE_TYPES` 는 `sql/sop_schema.sql` 의 CHECK 제약과 **같아야 합니다.**
> 어긋나면 파이썬은 통과시키는데 DB가 거부해서 500이 납니다.

### 5.1.1 `validate_document` — 최소한만 검사

```python
def validate_document(doc) -> None:
    if not isinstance(doc, dict):
        raise _invalid("문서(doc)는 JSON 객체여야 합니다.")
    if doc.get("format") != SOP_DOC_FORMAT:
        raise _invalid(f"format 값이 '{SOP_DOC_FORMAT}' 이어야 합니다. ...")
    if not _is_int(doc.get("version")):
        raise _invalid(f"version 은 정수여야 합니다. ...")
    if not isinstance(doc.get("blocks"), list):
        raise _invalid("blocks 는 페이지 목록(배열)이어야 합니다.")

    sop_no = _text(_dict_or_empty(doc.get("sop")).get("id"))
    if sop_no == "":
        raise _invalid("SOP 번호(sop.id)가 비어 있습니다.")
    if re.match(SOP_NO_PATTERN, sop_no) is None:
        raise _invalid(f"SOP 번호 '{sop_no}' 에 허용되지 않는 글자가 있습니다. ...")
```

**딱 5가지만 검사합니다.** 문서 안의 모든 필드를 검사하지 않습니다.

> **설계 철학: "저장을 막아야 할 만큼 치명적인 것"만 422로 막고,
> 나머지 이상한 값은 경고(warnings)를 남기고 저장은 진행합니다.**

이유: 편집기는 계속 진화합니다. 서버가 필드를 하나하나 검사하면 프론트가 필드를 추가할 때마다 서버를 고쳐야 합니다.
그리고 사용자 입장에서 "작업 3시간 한 게 검증 실패로 저장이 안 됨"보다 "저장은 되고 경고가 뜸"이 훨씬 낫습니다.

### 5.1.2 방어적 헬퍼들

프론트가 보낸 JSON은 **무슨 값이든 들어올 수 있습니다.** 그래서 전부 방어합니다:

```python
def _text(value) -> str:
    return str(value or "").strip()          # None → ""

def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
    # ↑ 파이썬에서 True는 int(1)로 취급! 명시적으로 제외
```

#### 숫자 방어 — JSON의 함정

```python
def _to_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(number, float) and not math.isfinite(number):
        return None          # ← inf, nan, 1e999 차단
    return number
```

**`Infinity`와 `NaN`은 JSON 표준에 없습니다.** PostgreSQL의 jsonb도 거부합니다.
`float("1e999")` = `inf` 가 되므로 문자열로 들어와도 막아야 합니다.

```python
def _to_int_or_none(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < INT4_MIN or number > INT4_MAX:
        return None          # ← DB int 범위 밖이면 "integer out of range" 500
    return number
```

> **🔑 "DB가 거부할 값은 DB에 보내기 전에 걸러라."**
> 그래야 500 대신 깔끔한 경고가 됩니다. 이게 백엔드가 하는 일의 큰 부분입니다.

### 5.1.3 `derive_flow_rows` — 관대한 변환

```python
def derive_flow_rows(doc) -> tuple[list[dict], list[dict], list[str]]:
    node_rows, edge_rows, warnings = [], [], []

    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(blocks, list):
        return node_rows, edge_rows, warnings     # 이상하면 빈 결과

    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "flowchart":
            continue                              # 순서도 페이지만
        project = block.get("project")
        if not isinstance(project, dict):
            continue                              # 순서도 편집기가 아직 안 뜬 상태

        instance_id = _text(block.get("instanceId")) or f"flow_{block_index}"

        seen_node_keys: set[str] = set()           # 중복 방지
        seen_edge_keys: set[str] = set()

        for node_index, node in enumerate(_list_or_empty(project.get("nodes"))):
            row = _node_row(instance_id, node, node_index + 1, warnings)
            if row is None:
                continue                          # 이상한 노드는 건너뜀
            if row["node_key"] in seen_node_keys:
                warnings.append(f"... 노드 '{row['node_key']}' 중복")
                continue
            seen_node_keys.add(row["node_key"])
            node_rows.append(row)
        # edges 도 같은 방식
    return node_rows, edge_rows, warnings
```

**핵심 원칙 3가지:**

| 원칙 | 코드 |
|---|---|
| 이상한 항목은 **건너뛰고 경고** — 저장 자체는 막지 않음 | `if row is None: continue` |
| 중복 키는 첫 번째만 — DB UNIQUE 제약 대비 | `seen_node_keys` |
| `instanceId` 가 없으면 순번으로 대체 | `or f"flow_{block_index}"` |

> **왜 건너뛰어도 되나?** 원본 JSON은 `content` 에 통째로 보관됩니다.
> `flow_nodes` 는 검색용 사본일 뿐이라, 몇 개 빠져도 **편집기 복원에는 전혀 영향이 없습니다.**
> 검색 결과에만 안 나올 뿐입니다.

#### `_node_row` — 건너뛰는 3가지 경우

```python
def _node_row(instance_id, node, index, warnings):
    if not isinstance(node, dict):
        warnings.append(f"{instance_id}: 노드 {index}번째 항목을 건너뜀 (객체가 아님)")
        return None
    node_key = _text(node.get("node"))
    if node_key == "":
        warnings.append(f"... (node 키가 비어 있음)")
        return None
    node_type = _text(node.get("node_type"))
    if node_type not in NODE_TYPES:
        warnings.append(f"... (node_type '{node_type}' 는 지원하지 않음)")
        return None
    return { ... }
```

**경고 문구에 위치(`instance_id`, `index`, `node_key`)를 넣는 것**이 중요합니다.
"노드를 건너뛰었습니다"만 있으면 사용자가 어느 상자인지 찾을 수 없습니다.

#### 조건부 컬럼

```python
"ref_document_id": _ref_document_id(node, instance_id, node_key, warnings)
                   if node_type == "sop" else None,
```

`sop` 타입 노드만 참조를 가집니다. 다른 타입에 값이 있어도 무시합니다.

### 5.1.4 `iter_sop_nodes` — 제너레이터

```python
def iter_sop_nodes(doc):
    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(blocks, list):
        return
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "flowchart":
            continue
        project = block.get("project")
        if not isinstance(project, dict):
            continue
        instance_id = _text(block.get("instanceId")) or f"flow_{block_index}"
        for node in _list_or_empty(project.get("nodes")):
            if isinstance(node, dict) and _text(node.get("node_type")) == "sop":
                yield instance_id, _text(node.get("node")), node
                #     ↑ 원본 dict 그대로 — 고치면 doc 에 반영됨!
```

**`return` 이 아니라 `yield`** 를 쓰면 **제너레이터**가 됩니다. 값을 하나씩 만들어 내보냅니다.
전체 목록을 메모리에 만들지 않아 효율적입니다.

**⚠️ 핵심: 돌려주는 `node` 는 원본 dict 그대로입니다.**
받는 쪽에서 고치면 `doc` 에 **바로 반영**됩니다. `refs.resolve_references` 가 이 성질을 이용합니다.

> 파이썬에서 dict는 **참조로 전달**됩니다. 이걸 모르면 "왜 안 바뀌지?" 또는 "왜 바뀌었지?"로 헤맵니다.

---

## 5.2 `refs.py` — SOP 간 참조 (205줄)

### SOP 상자의 3개 값

```
ref_document_id : 가리키는 문서의 UUID  ← 진짜 연결 (번호 바뀌어도 안 끊김)
sop_id          : 표시용 번호 캐시
sop_name        : 표시용 이름 캐시
```

### 5.2.1 `resolve_references` — 규칙 4가지

저장할 때마다 상자 하나하나를 정리합니다:

| 규칙 | 상자 상태 | 서버가 하는 일 | 통계 |
|---|---|---|---|
| **1** | id 있고 문서 존재 | 번호·이름을 현재 값으로 덮어씀. 폐기 문서면 경고 | `linked` |
| **2** | id 있는데 문서 없음 | id를 null로, 글자는 유지. 경고 | `pending`/`empty` |
| **3** | id 없고 번호만 있음 | 번호로 찾아서 있으면 **승격**(id 채움) | `linked`/`pending` |
| **4** | 둘 다 없음 | 손대지 않고 개수만 셈 | `empty` |

#### 규칙 3 "승격"이 만드는 자연스러운 흐름

```
1. SOP-A 를 그리면서 "나중에 만들 SOP-B" 를 참조 상자에 번호만 적어 둠
     → 저장 시 pending (미작성). 경고 없음.
2. 나중에 SOP-B 를 실제로 만듦
3. SOP-A 를 다시 저장
     → 규칙 3 발동! 번호로 SOP-B 를 찾아서 ref_document_id 를 채움 → linked
```

**사용자가 아무것도 안 해도 저절로 연결됩니다.**

#### N+1 문제와 해결

```python
# ❌ 나쁜 방법 — 상자 50개면 DB 조회 50번
for node in sop_nodes:
    doc = await conn.execute("SELECT ... WHERE id = %s", (node["ref_document_id"],))

# ✅ 이 프로젝트의 방법 — 조회 2번 (id용, 번호용)
```

```python
# 1단계: 훑으면서 찾을 것들을 모은다
sop_nodes = list(iter_sop_nodes(doc))
ids_to_find, sop_nos_to_find = [], []
for _instance_id, _node_key, node in sop_nodes:
    ref_id = uuid_or_none(node.get("ref_document_id"))
    sop_no = str(node.get("sop_id") or "").strip()
    if ref_id is not None:
        ids_to_find.append(ref_id)
    elif sop_no:
        sop_nos_to_find.append(sop_no)

# 2단계: 조회 2번 (set()으로 중복 제거)
by_id = await _load_documents_by_ids(conn, list(set(ids_to_find)))
by_no = await _load_documents_by_sop_nos(conn, list(set(sop_nos_to_find)))

# 3단계: 메모리에서 규칙 적용
for instance_id, node_key, node in sop_nodes:
    ...
```

```python
async def _load_documents_by_ids(conn, ids: list[UUID]) -> dict:
    if not ids:
        return {}                                    # 빈 목록이면 조회 안 함
    cur = await conn.execute(
        "SELECT id, sop_no, name, status FROM sop_documents WHERE id = ANY(%s)", (ids,))
    return {row["id"]: row for row in await cur.fetchall()}
```

`WHERE id = ANY(%s)` 는 PostgreSQL에서 **배열 하나를 통째로** 넘기는 문법입니다.
`IN (%s, %s, %s, ...)` 처럼 개수에 맞춰 자리표시자를 만들 필요가 없습니다.

> **🔑 N+1 문제는 백엔드에서 가장 흔한 성능 문제입니다.**
> "반복문 안에서 DB를 조회하고 있다" → 거의 항상 잘못된 신호입니다.
> **모아서 한 번에 조회 → 메모리에서 매칭** 이 정석입니다.

#### 규칙 적용 코드

```python
for instance_id, node_key, node in sop_nodes:
    raw_ref = str(node.get("ref_document_id") or "").strip()
    ref_id = uuid_or_none(raw_ref)
    sop_no = str(node.get("sop_id") or "").strip()

    if ref_id is not None and ref_id in by_id:
        # 규칙 1
        found = by_id[ref_id]
        _apply_document(node, found)             # ← node 를 제자리에서 수정!
        summary.linked += 1
        if found["status"] == "retired":
            warnings.append(f"{instance_id}: SOP 상자 {node_key} 가 가리키는 "
                            f"{found['sop_no']} 은(는) 폐기된 문서입니다")

    elif raw_ref != "":
        # 규칙 2 — 끊긴 연결
        node["ref_document_id"] = None
        if sop_no: summary.pending += 1
        else:      empty_count += 1
        warnings.append(f"... 문서(id {raw_ref})를 찾을 수 없어 연결을 풀었습니다 (번호 글자는 그대로)")

    elif sop_no:
        # 규칙 3 — 승격 시도
        found = by_no.get(sop_no)
        if found is not None:
            _apply_document(node, found)
            summary.linked += 1
            if found["status"] == "retired": warnings.append(...)
        else:
            summary.pending += 1                 # 미작성 — 손대지 않음, 경고 없음

    else:
        # 규칙 4
        empty_count += 1

summary.empty = empty_count
if empty_count:
    warnings.append(f"참조 대상이 비어 있는 SOP 상자 {empty_count}개")
```

```python
def _apply_document(node: dict, found: dict) -> None:
    node["ref_document_id"] = str(found["id"])
    node["sop_id"] = found["sop_no"]      # 최신 번호로 덮어씀
    node["sop_name"] = found["name"]      # 최신 이름으로 덮어씀
```

**이 함수가 `doc` 를 제자리에서 고칩니다.** 그 `doc` 가 그대로 `content` 에 저장되므로,
다음에 문서를 열면 최신 번호·이름이 보입니다. **캐시 갱신이 저장할 때마다 자동으로** 일어납니다.

#### 경고 합산

```python
if empty_count:
    warnings.append(f"참조 대상이 비어 있는 SOP 상자 {empty_count}개")
```

빈 상자가 30개면 경고 30줄이 아니라 **1줄**입니다.
> **경고가 너무 많으면 아무도 안 읽습니다.** 같은 종류는 합치세요.

### 5.2.2 "이 문서를 참조하는 문서" 찾기

```python
async def find_referenced_by(conn, doc_id: UUID, sop_no: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT d.id, d.sop_no, d.name, d.area, d.status, v.version_no "
        "  FROM flow_nodes n "
        "  JOIN sop_documents d ON d.current_version_id = n.version_id "   # ★
        "  JOIN sop_versions v ON v.id = d.current_version_id "
        " WHERE (n.ref_document_id = %s OR (n.ref_document_id IS NULL AND n.ref_sop_no = %s)) "
        "   AND d.id <> %s "
        " GROUP BY d.id, d.sop_no, d.name, d.area, d.status, v.version_no "
        " ORDER BY d.area, d.sop_no",
        (doc_id, sop_no, doc_id))
    return await cur.fetchall()
```

**★ `JOIN sop_documents d ON d.current_version_id = n.version_id`**

이게 이 SQL의 핵심입니다. `flow_nodes` 에는 **모든 버전의 노드**가 쌓여 있습니다.
그냥 조회하면 3년 전 v1에서 참조했다가 지금은 지운 것까지 나옵니다.

`current_version_id` 로 조인하면 **현재 버전의 노드만** 걸립니다.

| 요소 | 이유 |
|---|---|
| `ref_document_id = %s` | id로 연결된 참조 |
| `OR (ref_document_id IS NULL AND ref_sop_no = %s)` | 번호만 적힌 참조도 포함 |
| `AND d.id <> %s` | 자기 자신 제외 |
| `GROUP BY` | 한 문서에 상자가 3개 있어도 **1건**으로 |

```python
async def count_number_only_references(conn, doc_id: UUID, sop_no: str) -> int:
    cur = await conn.execute(
        "SELECT count(DISTINCT d.id) AS total "
        "  FROM flow_nodes n JOIN sop_documents d ON d.current_version_id = n.version_id "
        " WHERE n.ref_document_id IS NULL AND n.ref_sop_no = %s AND d.id <> %s",
        (sop_no, doc_id))
    return (await cur.fetchone())["total"]
```

번호 변경 시 "따라오지 못할 참조"를 세는 함수입니다 (4.1.6 참고).

### 5.2.3 `load_ref_docs` — 문서 열 때 주는 참조 정보

```python
async def load_ref_docs(conn, content: dict) -> list[dict]:
    ids = []
    for _instance_id, _node_key, node in iter_sop_nodes(content):
        ref_id = uuid_or_none(node.get("ref_document_id"))
        if ref_id is not None and ref_id not in ids:
            ids.append(ref_id)                 # 중복 제거 + 순서 유지
    if not ids:
        return []
    cur = await conn.execute(
        "SELECT id, sop_no, name, area, status FROM sop_documents "
        "WHERE id = ANY(%s) ORDER BY area, sop_no", (ids,))
    return await cur.fetchall()
```

**폐기된 문서도 포함합니다.** 편집기가 "이 상자는 폐기된 문서를 가리킵니다"를 표시하려면 알아야 하니까요.

> `if ref_id not in ids` 는 리스트 탐색이라 O(n)입니다. 상자가 수천 개면 `set` 을 쓰는 게 낫지만,
> 실제로는 수십 개 수준이라 순서 유지가 더 중요합니다. **"최적화는 실제 규모를 보고"** 하세요.

---

## 5.3 `schemas.py` — 프론트와의 계약서 (198줄)

### pydantic 읽는 법

```python
class SaveRequest(BaseModel):
    doc: dict[str, Any]                   # 필수
    base_version_no: int | None = None    # 선택, 기본 None
    change_note: str = ""                 # 선택, 기본 ""
    saved_by: str = ""
```

| 표기 | 의미 |
|---|---|
| `이름: 타입` | **반드시 있어야 함** (없으면 422) |
| `이름: 타입 = 기본값` | 선택 (없으면 기본값) |
| `int \| None` | 정수이거나 `null` |
| `Field(default_factory=list)` | 기본값이 빈 리스트 |
| `Field(default=120, ge=10, le=3600)` | 기본 120, 10~3600 범위 (밖이면 422) |
| `dict[str, Any]` | 아무 JSON 객체 (내용 검사 안 함) |

#### `default_factory` 를 쓰는 이유

```python
warnings: list[str] = []                          # ❌ 모든 객체가 리스트 하나를 공유!
warnings: list[str] = Field(default_factory=list) # ✅ 객체마다 새 리스트
```

파이썬의 유명한 함정입니다. 기본값이 **클래스 정의 시점에 한 번만** 만들어지기 때문입니다.

### 시각 처리 — 항상 UTC + Z

```python
def to_utc_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_z, return_type=str, when_used="json")]
```

`datetime` 대신 `UtcDatetime` 을 쓰면 JSON 출력이 자동으로 `2026-09-20T05:12:00.123456Z` 가 됩니다.

> **왜 UTC로 통일하나?** DB가 KST로 설정돼 있든, 서버가 다른 시간대에 있든 **응답은 항상 같은 모양**이어야
> 프론트가 헷갈리지 않습니다. 시간대 변환은 화면에 그릴 때 한 번만 합니다.
>
> **🔑 시간은 저장도 전송도 UTC. 표시할 때만 현지 시간.** 예외 없습니다.

### 주요 모델

```python
class DocumentSummary(BaseModel):     # 목록 한 줄 — content 없음!
    id: UUID; sop_no: str; name: str; area: str; status: str
    version_no: int | None = None
    revision: str = ""; owner: str = ""
    updated_at: UtcDatetime

class DocumentOpen(BaseModel):        # 열기 응답
    id: UUID; sop_no: str; version_no: int; version_id: UUID
    lock: LockInfo | None = None      # 잠금 없으면 null
    content: dict[str, Any]           # 편집기 JSON 원본
    ref_docs: list[RefDoc] = Field(default_factory=list)

class SaveResponse(BaseModel):        # 저장 응답
    id: UUID; sop_no: str; version_id: UUID; version_no: int
    saved_at: UtcDatetime
    warnings: list[str] = Field(default_factory=list)   # 여러 건
    warning: str | None = None                          # 잠금 안내 1건
    refs: RefsSummary = Field(default_factory=RefsSummary)
```

> `warnings`(복수)와 `warning`(단수)이 따로인 건 다소 헷갈립니다.
> `warnings` = 저장 처리 중 생긴 여러 경고, `warning` = 잠금 안내 한 건.
> **새 API를 만들 땐 이런 이름 짓기를 피하세요.**

### `doc` 을 `dict[str, Any]` 로 받는 이유

```python
doc: dict[str, Any]     # 내용을 검사하지 않음
```

편집기 JSON 구조를 pydantic으로 정의하면, 프론트가 필드 하나 추가할 때마다 **서버를 고쳐야 합니다.**
그래서 통째로 받고, 필요한 몇 개만 `derive.py` 에서 골라 씁니다.

> **계약은 "서버가 실제로 쓰는 것"만 정의하세요.** 안 쓰는 걸 정의하면 결합도만 올라갑니다.

### `response_model` 의 3가지 효과

```python
@router.get("/sops", response_model=list[DocumentSummary])
```

1. **필터링** — 모델에 없는 필드는 응답에서 **제거**됩니다. 실수로 민감한 컬럼이 새어나가지 않습니다.
2. **변환** — `UUID` → 문자열, `datetime` → `...Z` 자동 변환
3. **문서화** — `/docs` 에 응답 예시가 자동 생성됩니다

---

## 5.4 `errors.py` — 오류 통일 (76줄)

### 모든 오류는 이 모양

```json
{ "error": { "code": "version_conflict",
             "message": "다른 사람이 먼저 v4 을(를) 저장했습니다.",
             "current_version_no": 4 } }
```

| 필드 | 용도 |
|---|---|
| `code` | **프로그램이 `if` 로 비교**하는 짧은 영문 이름 |
| `message` | **사람이 읽는** 설명 |
| 추가 필드 | 프론트가 다음 행동을 정하는 데 필요한 정보 |

> **`code` 와 `message` 를 분리하는 이유**: `message` 는 문구가 바뀔 수 있습니다.
> 프론트가 메시지 문자열로 분기하면 문구를 다듬는 순간 깨집니다. `code` 는 절대 안 바뀝니다.

### `ApiError`

```python
class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra
```

`**extra` 로 아무 추가 정보나 담을 수 있습니다:

```python
raise ApiError(409, "version_conflict", "...", current_version_no=4)
raise ApiError(423, "locked", "...", locked_by="kim", expires_at="2026-...")
raise ApiError(409, "sop_no_taken", "...", existing_id="a1b2...")
```

### 처리기 4개

```python
def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)                  # ① 우리가 의도적으로 던진 것
    async def handle_api_error(request, exc):
        return JSONResponse(status_code=exc.status_code,
                            content=error_body(exc.code, exc.message, **exc.extra))

    @app.exception_handler(RequestValidationError)    # ② pydantic 검증 실패
    async def handle_validation_error(request, exc):
        return JSONResponse(status_code=422,
            content=error_body("validation_error", "요청 형식이 올바르지 않습니다.",
                               details=exc.errors()))

    @app.exception_handler(StarletteHTTPException)    # ③ 404, 405 등 프레임워크 오류
    async def handle_http_error(request, exc):
        return JSONResponse(status_code=exc.status_code,
                            content=error_body("http_error", str(exc.detail)))

    @app.exception_handler(Exception)                 # ④ 예상 못 한 모든 것
    async def handle_unexpected(request, exc):
        log.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500,
                            content=error_body("internal_error", "서버 내부 오류가 발생했습니다."))
```

#### ③ — Starlette의 HTTPException을 잡는 이유

```python
from starlette.exceptions import HTTPException as StarletteHTTPException
```

FastAPI는 Starlette 위에서 돕니다. **"없는 주소(404)" 같은 오류는 Starlette 층에서** 만들어집니다.
`fastapi.HTTPException` 만 잡으면 그것들이 새어나가서 다른 모양의 JSON이 됩니다.
**부모 클래스를 잡아야 전부 걸립니다.**

#### ④ — 500에서 스택트레이스를 숨기는 이유

```python
log.exception("unhandled error: ...")      # 서버 로그에는 전부 남김
return JSONResponse(status_code=500,
    content=error_body("internal_error", "서버 내부 오류가 발생했습니다."))  # 응답엔 없음
```

**스택트레이스에는 파일 경로, 변수명, SQL 일부가 담깁니다.** 공격자에게 정보를 주는 셈입니다.
**로그에는 전부, 응답에는 최소한.** 보안의 기본입니다.

### 어디서든 던지면 된다

```python
# routers/common.py 깊은 곳에서
raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
```

이게 자동으로 404 JSON이 됩니다. 호출한 함수마다 오류를 되돌려 전달할 필요가 없습니다.

> **예외를 쓰는 이유**: 리턴값으로 오류를 전달하면 모든 호출 지점에서 `if error: return error` 를 써야 합니다.
> 예외는 **중간 단계를 건너뛰고** 처리기까지 날아갑니다.

---

## 5.5 `db.py` — 연결 풀 (137줄)

### 용어

| 용어 | 의미 |
|---|---|
| **연결(connection)** | 프로그램과 DB 사이의 전화선 하나. 여는 데 시간이 걸림 |
| **풀(pool)** | 전화선을 미리 몇 개 열어 두고 빌려 쓰는 창구 |
| **트랜잭션** | "이 작업들은 한 묶음" — 전부 성공하거나 전부 취소 |

### 풀 설정

```python
pool = AsyncConnectionPool(
    database_url,
    min_size=min_size,          # 항상 열어 둘 최소 개수 (기본 2)
    max_size=max_size,          # 최대 (기본 10)
    open=False,                 # 만들자마자 열지 않음
    kwargs=conn_kwargs,
    check=AsyncConnectionPool.check_connection,   # 빌려줄 때마다 살아있는지 확인
    max_lifetime=MAX_LIFETIME_SEC,                # 30분마다 새 연결로 교체
    timeout=connect_timeout,
)
await pool.open(wait=True, timeout=connect_timeout)   # 실제 접속될 때까지 대기
```

| 옵션 | 왜 필요한가 |
|---|---|
| `check=check_connection` | 방화벽/중계기가 오래 놀고 있는 연결을 끊어 버림 → 끊긴 연결을 자동 교체 |
| `max_lifetime=30분` | 연결을 너무 오래 쓰면 메모리 누수·서버 재시작 문제 → 주기적 갱신 |
| `wait=True` | **서버는 켜졌는데 DB 접속이 안 되는 상태**를 방지. 요청마다 500 나는 것보다 낫다 |

### 연결 옵션

```python
conn_kwargs = {
    "row_factory": dict_row,      # 결과를 {"컬럼": 값} dict로 받음
    "autocommit": True,           # 기본은 자동 저장
    "connect_timeout": connect_timeout,
    "prepare_threshold": prepare_threshold,
}
if session_options:
    conn_kwargs["options"] = session_options    # "-c timezone=UTC"
```

#### `row_factory=dict_row`

```python
row["sop_no"]      # ✅ 이렇게 쓸 수 있는 건 이 설정 덕분
row[1]             # ❌ 기본 설정이면 이렇게 써야 함 (순서가 바뀌면 깨짐)
```

#### `autocommit=True` + 명시적 트랜잭션

기본은 자동 저장(각 SQL이 즉시 반영)이고, 묶어야 할 때만 `async with conn.transaction():` 을 씁니다.
읽기만 하는 API에서 불필요한 트랜잭션 오버헤드를 없앱니다.

#### `prepare_threshold` — PgBouncer 대응

같은 SQL을 5번 실행하면 DB에 "미리 준비된 문장"으로 등록해서 조금 빨라집니다.
그런데 **PgBouncer(트랜잭션 풀링) 뒤에서는 연결이 요청마다 바뀌어** 그 등록이 사라집니다
→ `"prepared statement ... does not exist"` 오류.

그래서 `DB_PREPARE_THRESHOLD=0` 으로 끌 수 있게 했습니다.

> **회사 DB 앞에 PgBouncer가 있다면 이 설정을 기억하세요.** 안 그러면 간헐적 500에 시달립니다.

### 친절한 실패

```python
except PoolTimeout as error:
    log.error(
        "DB 에 %d초 안에 접속하지 못했습니다. 접속 정보: %s\n"
        "  확인할 것: 1) DATABASE_URL 값  2) DB 서버가 켜져 있는지  3) 방화벽/포트  4) 계정·비밀번호\n"
        "  원인: %s",
        connect_timeout, mask_password(database_url), error)
    await pool.close()
    pool = None
    raise
```

**`mask_password`** — 로그에 비밀번호가 찍히면 안 됩니다. `postgresql://user:****@host/db` 로 가립니다.

> **🔑 로그에 비밀번호·토큰·개인정보를 절대 남기지 마세요.** 로그는 여러 사람이 봅니다.

### `get_conn` — 의존성

```python
async def get_conn() -> AsyncIterator[AsyncConnection]:
    if pool is None:
        raise RuntimeError("DB 풀이 아직 열리지 않았습니다. open_pool() 을 먼저 호출하세요.")
    async with pool.connection() as conn:
        yield conn      # ← 빌려주고 멈춤. API 끝나면 여기로 돌아와 반납
```

`yield` 덕분에 **"빌려주기 → 쓰기 → 반납"이 한 함수**에 담깁니다.
API 함수가 예외로 끝나도 `async with` 가 반드시 반납합니다. **연결 누수가 원천 차단됩니다.**

---

## 5.6 `deps.py` — 사용자 식별 (32줄)

```python
def current_user(request: Request) -> str:
    header_name = get_settings().user_header      # 기본 "X-User"
    raw = request.headers.get(header_name) or ""
    name = unquote(raw).strip()                   # %ED%99%8D... → 홍길동
    return name or "anonymous"
```

### 한글 이름 처리

HTTP 헤더에는 원칙적으로 ASCII만 실을 수 있습니다.
프론트가 `encodeURIComponent("홍길동")` = `"%ED%99%8D%EA%B8%B8%EB%8F%99"` 로 보내면,
서버가 `unquote` 로 되돌립니다. 영문 이름은 바뀌는 게 없어 그대로입니다.

### 지금은 인증이 없다

```python
# main.py
API_GUARD = [Depends(current_user)]
app.include_router(sops.router, prefix="/api", dependencies=API_GUARD)
```

**모든 API가 이 함수를 거칩니다.** 지금은 헤더를 읽기만 하지만,
나중에 여기서 `raise ApiError(401, ...)` 를 던지면 **API 전체가 한 번에 보호됩니다.**

> **🔑 이런 "확장 지점"을 미리 만들어 두는 게 좋은 설계입니다.**
> 나중에 라우터 8개를 하나씩 고칠 필요가 없습니다.

회사 환경에서는 앞단 SSO 프록시가 사용자 이름을 헤더에 넣어 줍니다.
헤더 이름이 다르면 환경변수 `USER_HEADER` 로 바꿉니다.

---

## 5.7 `config.py` — 환경변수 (149줄)

```python
class Settings:
    def __init__(self):
        self.database_url        = resolve_database_url()
        self.db_session_options  = os.environ.get("DB_SESSION_OPTIONS", "-c timezone=UTC")
        self.db_connect_timeout  = _env_int("DB_CONNECT_TIMEOUT", 30)
        self.pool_min_size       = _env_int("DB_POOL_MIN", 2)
        self.pool_max_size       = _env_int("DB_POOL_MAX", 10)
        self.db_prepare_threshold= _env_prepare_threshold()
        self.root_path           = root_path              # "/sop" 같은 접두어
        self.user_header         = os.environ.get("USER_HEADER", "X-User")
        self.log_level           = os.environ.get("LOG_LEVEL", "INFO")
        self.cors_origins        = [...]
        self.static_dir          = os.environ.get("STATIC_DIR", "static")
        self.static_index        = os.environ.get("STATIC_INDEX", "SOP_STUDIO.html")
        self.max_content_bytes   = _env_int("MAX_CONTENT_MB", 20) * 1024 * 1024
```

### 왜 환경변수인가

**같은 코드가 내 PC / 테스트 서버 / 회사 운영 서버에서 다르게 동작해야 합니다.**
코드에 DB 주소를 적어 두면 환경마다 코드를 고쳐야 하고, 비밀번호가 git에 올라갑니다.

> **🔑 "설정은 코드가 아니라 환경에서." 12-factor app의 핵심 원칙입니다.**

### `ROOT_PATH` — 접두어 뒤에서 돌기

회사에서 `https://회사.com/sop/` 뒤에 서버를 붙이면, 서버는 자기가 `/sop` 아래 있는 걸 모릅니다.
`ROOT_PATH=/sop` 을 주면 `/docs` 주소 계산이 맞아떨어지고,
`main.py` 의 `inject_api_base` 가 HTML에 메타 태그를 심어 프론트도 접두어를 붙입니다:

```python
def inject_api_base(html: str, root_path: str) -> str:
    meta = f'<meta name="api-base" content="{root_path}">'
    match = _HEAD_TAG.search(html)
    if match is None:
        return meta + html
    end = match.end()
    return html[:end] + meta + html[end:]
```

---

# 6장. 동시성 — 두 사람이 동시에 건드릴 때

백엔드가 **가장 많이 실수하는 영역**입니다. 혼자 테스트할 땐 절대 안 나타납니다.

## 6.1 이 프로젝트의 3중 방어

```
1층  편집 잠금 (sop_edit_locks)     ← 예의. 동시 편집 "시도"를 줄임
2층  FOR UPDATE 행 잠금             ← 저장 처리를 한 명씩 순서대로
3층  base_version_no 검사           ← 덮어쓰기를 확실히 차단
```

## 6.2 `FOR UPDATE` — 비관적 잠금

```sql
SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE
```

**"이 행을 내 트랜잭션이 끝날 때까지 다른 트랜잭션이 못 건드리게 잠근다."**

```
시각   요청 A                           요청 B
 t1    BEGIN
 t2    SELECT ... FOR UPDATE  (잠금 획득)
 t3                                     BEGIN
 t4                                     SELECT ... FOR UPDATE  → 대기 🔒
 t5    MAX(version_no) = 3
 t6    INSERT version_no=4
 t7    COMMIT (잠금 해제)
 t8                                     → 통과. MAX = 4
 t9                                     INSERT version_no=5
```

**이게 없으면** A와 B가 동시에 `MAX=3` 을 읽고 둘 다 `version_no=4` 를 INSERT → UNIQUE 위반 500.

### 규칙 3가지

1. **반드시 트랜잭션 안에서** — `autocommit` 상태면 SELECT가 끝나는 순간 잠금도 풀립니다
2. **잠근 뒤에 다시 읽어라** — 잠금 전에 읽은 값은 이미 낡았을 수 있습니다
3. **잠금 순서를 통일하라** — A는 문서→잠금, B는 잠금→문서 순이면 **교착(deadlock)** 이 납니다

이 프로젝트는 항상 `sop_documents` → `sop_edit_locks` 순서입니다.

## 6.3 없는 행은 잠글 수 없다

`locks.py` 의 교훈 (4.3.2 참고):

```python
# ★ 항상 존재하는 부모 행에서 먼저 줄을 세운다
await conn.execute("SELECT id FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
# 그다음에야 없을 수도 있는 자식 행을 다룬다
cur = await conn.execute("SELECT ... FROM sop_edit_locks WHERE document_id = %s FOR UPDATE", ...)
```

## 6.4 낙관적 잠금 — `base_version_no`

```python
if body.base_version_no is not None and body.base_version_no != max_no:
    raise ApiError(409, "version_conflict", ..., current_version_no=max_no)
```

| 방식 | 장점 | 단점 |
|---|---|---|
| **비관적** (FOR UPDATE, 편집잠금) | 충돌이 아예 안 남 | 대기 발생, 잠금 관리 필요 |
| **낙관적** (버전 번호 비교) | 대기 없음, 단순 | 충돌 시 사용자가 다시 해야 함 |

**긴 작업(문서 편집 몇 시간)에는 낙관적**, **짧은 작업(DB 트랜잭션 몇 ms)에는 비관적**이 맞습니다.
이 프로젝트가 정확히 그렇게 쓰고 있습니다.

## 6.5 경쟁 조건 이중 방어

```python
await _raise_if_sop_no_taken(conn, meta.sop_no)      # 1차: 친절한 오류
try:
    async with conn.transaction():
        ... INSERT ...
except psycopg.errors.UniqueViolation:               # 2차: DB 제약이 최종 방어
    await _raise_if_sop_no_taken(conn, meta.sop_no)  # 다시 조회해서 친절한 409
    raise
```

**검사와 INSERT 사이에는 항상 틈이 있습니다.**

> **🔑 "애플리케이션 검사는 사용자 경험용, DB 제약이 진짜 방어선."**
> UNIQUE 제약을 안 걸고 파이썬 검사만 믿으면 언젠가 중복 데이터가 생깁니다.

## 6.6 트랜잭션은 짧게

```python
async with conn.transaction():
    response = await _append_version(...)        # 꼭 필요한 것만

response.warning = await _other_users_lock_warning(...)   # 트랜잭션 밖
```

트랜잭션이 길면 잠금 대기가 길어지고, 연결 풀이 고갈되고, 교착 확률이 올라갑니다.

**트랜잭션 안에서 하지 말아야 할 것:**
- 외부 API 호출
- 파일 읽기/쓰기
- 무거운 계산
- 불필요한 조회

---

# 7장. 오류 설계

## 7.1 오류 코드 전체 목록

| 코드 | HTTP | 언제 | 추가 필드 |
|---|---|---|---|
| `not_found` | 404 | 문서 없음 | |
| `version_not_found` | 404 | 그 버전 없음 | `version_no` |
| `sop_no_taken` | 409 | 번호 중복 | `existing_id`, `existing_sop_no` |
| `version_conflict` | 409 | 다른 사람이 먼저 저장 | `current_version_no` |
| `sop_no_mismatch` | 400 | 문서 안 번호 ≠ 저장된 번호 | `stored_sop_no`, `doc_sop_no`, `hint` |
| `locked` | 423 | 남이 편집 중 | `locked_by`, `expires_at` |
| `invalid_document` | 422 | 문서 형식 오류 | |
| `validation_error` | 422 | 요청 JSON 형식 오류 | `details` |
| `payload_too_large` | 413 | 20MB 초과 | |
| `static_not_found` | 404 | 편집기 HTML 없음 | |
| `http_error` | 4xx | 없는 주소 등 | |
| `internal_error` | 500 | 예상 못 한 오류 | |

## 7.2 좋은 오류의 3요소

```python
raise ApiError(409, "sop_no_taken",
    f"SOP 번호 {existing['sop_no']} 은(는) 이미 라이브러리에 있습니다. "
    f"그 문서의 새 버전으로 저장하려면 PUT /api/sops/{existing['id']} 를 쓰세요.",
    existing_id=str(existing["id"]), existing_sop_no=existing["sop_no"])
```

| 요소 | 이 예에서 |
|---|---|
| ① **무엇이 잘못됐나** | "번호가 이미 있습니다" |
| ② **어떻게 해결하나** | "PUT /api/sops/{id} 를 쓰세요" |
| ③ **필요한 데이터** | `existing_id` |

> **❌ 나쁜 오류**: `{"detail": "Conflict"}`
> **✅ 좋은 오류**: 위처럼. 프론트가 자동으로 다음 행동을 할 수 있습니다.

## 7.3 경고(warning) vs 오류(error)

이 프로젝트가 잘한 점: **저장을 막지 않고 경고만 주는 경우**를 넓게 잡았습니다.

| 상황 | 처리 |
|---|---|
| `format` 값이 틀림 | ❌ **422 오류** — 우리 문서가 아님 |
| SOP 번호 비어 있음 | ❌ **422 오류** — 식별이 불가능 |
| AREA가 `X` | ⚠️ 경고 + `''` 로 저장 |
| 노드 `node_type` 이 이상함 | ⚠️ 경고 + 그 노드만 건너뜀 |
| 참조 문서가 폐기됨 | ⚠️ 경고 + 저장 진행 |
| 좌표가 숫자가 아님 | ⚠️ 경고 + `0` 으로 |

**판단 기준:**
> **"이 값이 틀리면 데이터가 못 쓰게 되나?"** → 오류
> **"이상하지만 저장하고 나중에 고칠 수 있나?"** → 경고

사용자가 3시간 작업한 걸 사소한 검증 실패로 날리면 안 됩니다.

## 7.4 로그 남기기

```python
log.info("saved %s v%s nodes=%s edges=%s refs=%s/%s/%s status=%s by %s", ...)
log.info("renamed %s -> %s (referenced_by=%s, ...) by %s", ...)
log.exception("unhandled error: %s %s", request.method, request.url.path)
```

**남겨야 할 것**: 무엇이(SOP 번호), 언제(자동), 누가(`by %s`), 결과(개수/상태)
**남기면 안 되는 것**: 비밀번호, 토큰, 개인정보, 문서 전체 내용

```python
log.info("...%s", 값)      # ✅ 레벨이 안 맞으면 문자열을 안 만듦 (빠름)
log.info(f"...{값}")       # ❌ 항상 문자열을 만듦
```

---

# 8장. 실전 — 새 API 추가하기

## 8.1 예제: "문서 상태 변경" API 만들기

`draft → review → approved` 로 상태를 바꾸는 API를 추가해 봅시다.

### 1단계 — 계약 정하기

```
PATCH /api/sops/{doc_id}/status
요청:  { "status": "review", "user": "hong" }
응답:  200  { "id": "...", "status": "review", "referenced_by": null }
오류:  404 not_found / 422 invalid_status / 423 locked
```

### 2단계 — `schemas.py` 에 요청 모델

```python
class StatusChangeRequest(BaseModel):
    """PATCH /api/sops/{doc_id}/status 요청 본문."""
    status: str            # draft / review / approved / retired
    user: str = ""         # 비우면 X-User 헤더 값
```

응답은 기존 `StatusResponse` 를 재사용합니다.

### 3단계 — `derive.py` 에 허용값 상수

```python
VALID_STATUSES = ("draft", "review", "approved", "retired")   # SQL CHECK 와 동일해야 함
```

### 4단계 — `sops.py` 에 라우터

```python
@router.patch("/sops/{doc_id}/status", response_model=StatusResponse)
async def change_status(
    doc_id: UUID, body: StatusChangeRequest,
    user: str = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
):
    """
    문서의 상태를 바꿉니다 (draft / review / approved / retired).
    편집기에서 "검토 요청", "승인" 버튼을 누를 때 프론트가 호출합니다.
      - 허용값이 아니면 422 invalid_status
      - 다른 사람이 편집 중이면 423 locked
      - 문서가 없으면 404 not_found
    """
    who = body.user.strip() or user
    new_status = body.status.strip()

    # ① 형식 검사 (DB에 보내기 전에)
    if new_status not in VALID_STATUSES:
        raise ApiError(422, "invalid_status",
            f"상태 '{new_status}' 는 허용되지 않습니다. "
            f"({', '.join(VALID_STATUSES)} 중 하나여야 합니다)",
            allowed=list(VALID_STATUSES))

    async with conn.transaction():
        # ② 행 잠금 + 존재 확인 (common.py 재사용 대신 FOR UPDATE가 필요하므로 직접)
        cur = await conn.execute(
            "SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE", (doc_id,))
        doc_row = await cur.fetchone()
        if doc_row is None:
            raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")

        # ③ 남의 잠금 확인 (rename_document 와 같은 패턴)
        cur = await conn.execute(
            "SELECT locked_by, expires_at FROM sop_edit_locks "
            "WHERE document_id = %s AND locked_by <> %s AND expires_at > now()", (doc_id, who))
        lock_row = await cur.fetchone()
        if lock_row is not None:
            raise ApiError(423, "locked",
                f"{lock_row['locked_by']} 님이 편집 중이라 상태를 바꿀 수 없습니다.",
                locked_by=lock_row["locked_by"], expires_at=to_utc_z(lock_row["expires_at"]))

        # ④ 같은 상태면 아무것도 안 함 (멱등)
        if new_status != doc_row["status"]:
            await conn.execute(
                "UPDATE sop_documents SET status = %s, updated_at = now() WHERE id = %s",
                (new_status, doc_id))
            log.info("status %s: %s -> %s by %s",
                     doc_row["sop_no"], doc_row["status"], new_status, who)

    return StatusResponse(id=doc_id, status=new_status)
```

### 5단계 — import 추가

```python
from app.derive import SOP_NO_PATTERN, VALID_STATUSES, derive_flow_rows, document_meta, validate_document
from app.schemas import (..., StatusChangeRequest, ...)
```

### 6단계 — 테스트

```bash
python app.py
# 브라우저에서 http://localhost:8000/docs 열기 → 새 API가 자동으로 나타남 → "Try it out"
```

```bash
curl -X PATCH http://localhost:8000/api/sops/<UUID>/status \
  -H "Content-Type: application/json" \
  -H "X-User: hong" \
  -d '{"status": "review"}'
```

## 8.2 체크리스트

새 API를 만들 때 매번 확인하세요:

```
□ 계약(경로/메서드/요청/응답)을 먼저 정했는가
□ schemas.py 에 모델을 정의했는가 (응답도!)
□ response_model 을 지정했는가
□ 경로 등록 순서가 맞는가 (고정 > 변수)
□ 목록 API라면 큰 컬럼(content)을 제외했는가
□ 사용자 입력을 %s 자리표시자로 넘겼는가 (f-string ❌)
□ 없는 대상에 404를 내는가
□ 쓰기 API라면 트랜잭션으로 묶었는가
□ 동시 실행을 고려했는가 (FOR UPDATE 필요?)
□ 반복문 안에서 DB를 조회하고 있지 않은가 (N+1)
□ 오류에 "다음에 뭘 해야 하는지"를 담았는가
□ 멱등하게 만들 수 있는가
□ 로그를 남겼는가 (비밀정보 제외)
□ 허용값 상수가 SQL CHECK 제약과 같은가
□ /docs 에서 직접 눌러 봤는가
```

## 8.3 재사용할 수 있는 것들

새로 만들지 말고 **가져다 쓰세요**:

```python
from app.routers.common import get_document_or_404, find_active_lock, content_with_current_sop_no
from app.errors import ApiError
from app.db import get_conn
from app.deps import current_user
from app.schemas import StatusResponse, to_utc_z
from app.derive import validate_document, document_meta, SOP_NO_PATTERN
from app.refs import count_referenced_by, find_referenced_by
```

## 8.4 개발 명령어

```bash
# 서버 실행 (DB 필요)
python app.py
PORT=9000 python app.py

# DB 테이블 생성 (최초 1회)
python -m app.tools.apply_schema

# 내 PC에서 DB 없이 (내장 PostgreSQL 포함)
python -m app.tools.dev_server

# API 문서
open http://localhost:8000/docs

# 헬스체크
curl http://localhost:8000/health        # 프로세스 생존
curl http://localhost:8000/api/health    # DB 포함
```

---

# 9장. 문법 사전

## 9.1 `async` / `await`

```python
async def list_documents(...):
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return rows
```

**`await` = "이거 기다리는 동안 다른 요청을 처리해도 좋다"**

```
동기(sync):   [요청1 DB대기 2초][요청2 DB대기 2초][요청3 DB대기 2초]  = 6초
비동기(async): [요청1 ─┐
               요청2 ─┼─ 전부 DB 대기 중 ─── 2초
               요청3 ─┘                                            = 2초
```

**규칙:**
- `async def` 안에서만 `await` 를 쓸 수 있다
- `await` 를 빼먹으면 **코루틴 객체**가 반환돼서 이상하게 동작한다 (흔한 실수)
- DB/네트워크/파일을 건드리는 함수 앞엔 거의 다 붙는다
- 계산만 하는 함수(`derive.py` 의 함수들)는 `async` 가 필요 없다

## 9.2 `Depends` — 의존성 주입

```python
async def save_document(
    doc_id: UUID,
    body: SaveRequest,
    user: str = Depends(current_user),          # ← 실행 전에 current_user() 호출
    conn: AsyncConnection = Depends(get_conn),  # ← 실행 전에 get_conn() 호출
):
```

**"이 함수가 실행되기 전에 저 함수를 실행해서 결과를 넣어 줘."**

장점:
- 연결을 직접 열고 닫을 필요가 없음
- 테스트할 때 가짜로 바꿔 끼울 수 있음
- 여러 API가 같은 준비 작업을 공유

라우터 전체에 걸 수도 있습니다:
```python
app.include_router(sops.router, prefix="/api", dependencies=[Depends(current_user)])
```

## 9.3 `yield`

```python
async def get_conn():
    async with pool.connection() as conn:
        yield conn          # 여기서 넘겨주고 멈춤
        # API 함수가 끝나면 여기로 돌아와서 with 블록 종료 = 반납
```

`return` 은 끝나지만, `yield` 는 **잠깐 넘겨주고 나중에 돌아옵니다.**

제너레이터로도 쓰입니다:
```python
def iter_sop_nodes(doc):
    for node in nodes:
        yield instance_id, node_key, node    # 하나씩 만들어 내보냄
```

## 9.4 `async with` — 컨텍스트 매니저

```python
async with conn.transaction():
    ...        # 블록이 정상 종료 → COMMIT
               # 예외 발생      → ROLLBACK
```

**블록을 벗어날 때 반드시 정리 작업이 실행됩니다.** 예외가 나도 실행됩니다.

## 9.5 타입 힌트

```python
doc_id: UUID                      # UUID 타입
status: str = "!retired"          # 문자열, 기본값
lock: LockInfo | None = None      # LockInfo 이거나 None
rows: list[dict]                  # dict의 리스트
-> SaveResponse                   # 반환 타입
-> tuple[list[dict], list[dict], list[str]]   # 튜플 3개
```

파이썬은 타입 힌트를 **실행 시 검사하지 않습니다.** 하지만 **FastAPI는 이걸 읽어서 자동 변환·검증**을 합니다.
`doc_id: UUID` 라고 쓰면 주소의 문자열을 UUID로 바꿔 주고, 형식이 틀리면 422를 냅니다.

## 9.6 psycopg 패턴

```python
# 한 줄 조회
cur = await conn.execute("SELECT ... WHERE id = %s", (doc_id,))   # ← 튜플! 쉼표 필수
row = await cur.fetchone()          # 없으면 None
if row is None:
    raise ApiError(404, ...)

# 여러 줄
cur = await conn.execute("SELECT ...")
rows = await cur.fetchall()          # 없으면 []

# 값 하나
count = (await cur.fetchone())["total"]

# INSERT + 결과 받기
cur = await conn.execute("INSERT ... RETURNING id, saved_at", (...))
new_id = (await cur.fetchone())["id"]

# 여러 행 한 번에
async with conn.cursor() as cur:
    await cur.executemany("INSERT ... VALUES (%s, %s)", list_of_tuples)

# 배열 조건
"WHERE id = ANY(%s)", (ids,)         # ids 는 파이썬 리스트

# JSON 컬럼
Jsonb(python_dict)                   # dict → jsonb
```

**⚠️ 파라미터는 항상 튜플/리스트입니다.** `(doc_id,)` 의 쉼표를 빼면 그냥 괄호가 돼서 오류가 납니다.

## 9.7 SQL 표현

```sql
COALESCE(a, b)                  -- a가 NULL이면 b
COALESCE(MAX(version_no), 0)    -- 행이 없으면 0

now()                           -- DB의 현재 시각 (서버 시계 말고 이걸 쓰세요)
now() + make_interval(secs => 120)   -- 120초 뒤
(expires_at > now()) AS is_active    -- 계산을 DB에서

FOR UPDATE                      -- 행 잠금 (트랜잭션 안에서만 의미 있음)
RETURNING id, saved_at          -- INSERT/UPDATE/DELETE 하면서 결과 받기
id = ANY(%s)                    -- 배열 안에 있는가
ILIKE '%검색어%'                -- 대소문자 구분 없는 포함 검색
d.status <> %s                  -- 같지 않음
GROUP BY ...                    -- 중복 제거 (DISTINCT 대안)
```

## 9.8 파이썬 관용구

```python
str(value or "").strip()              # None 안전 문자열화
body.user.strip() or user             # 비어 있으면 대체값
dict(content)                         # 얕은 복사
dict(sop, id=sop_no)                  # 복사하면서 일부 교체
list(set(items))                      # 중복 제거
{row["id"]: row for row in rows}      # dict 컴프리헨션 (인덱싱)
[Model(**row) for row in rows]        # dict → 모델 변환
f"{a} / {b}"                          # f-string (SQL엔 절대 쓰지 말 것!)
isinstance(v, int) and not isinstance(v, bool)   # 진짜 정수인가
```

---

# 10장. 치트시트

## 10.1 전체 엔드포인트

```
GET    /health                                    프로세스 생존 (DB 안 봄)
GET    /api/health                                DB 포함 (죽으면 503)
GET    /                                          편집기 HTML
GET    /docs                                      API 문서 (Swagger)

GET    /api/sops?status=&q=&area=                 문서 목록
GET    /api/sops/by-no/{sop_no}                   번호로 열기
GET    /api/sops/{doc_id}                         id로 열기
POST   /api/sops                                  새 문서          201
PUT    /api/sops/{doc_id}                         새 버전          201
PATCH  /api/sops/{doc_id}/number                  번호 변경
DELETE /api/sops/{doc_id}                         폐기
GET    /api/sops/{doc_id}/referenced-by           나를 참조하는 문서

GET    /api/sops/{doc_id}/versions                버전 목록
GET    /api/sops/{doc_id}/versions/{no}           특정 버전 열기
GET    /api/sops/{doc_id}/versions/{a}/diff/{b}   버전 비교

POST   /api/sops/{doc_id}/lock                    잠금 잡기/연장
DELETE /api/sops/{doc_id}/lock                    잠금 풀기
```

모든 `/api/*` 요청은 `X-User` 헤더를 읽습니다 (없으면 `anonymous`).

## 10.2 "이럴 땐 여기를 보세요"

| 하고 싶은 것 | 파일 |
|---|---|
| 새 API 추가 | `app/routers/*.py` |
| 요청/응답 모양 변경 | `app/schemas.py` |
| 문서 JSON 검증 규칙 변경 | `app/derive.py` |
| 참조 규칙 변경 | `app/refs.py` |
| 오류 코드 추가 | 그냥 `raise ApiError(...)` |
| 인증 붙이기 | `app/deps.py` (한 곳만!) |
| DB 설정 변경 | `app/db.py`, `app/config.py` |
| 테이블 추가/변경 | `sql/migrations/00N_*.sql` |
| 요청 크기 제한 변경 | 환경변수 `MAX_CONTENT_MB` |
| 로그 레벨 변경 | 환경변수 `LOG_LEVEL` |

## 10.3 흔한 실수 TOP 10

| # | 실수 | 결과 | 예방 |
|---|---|---|---|
| 1 | SQL에 f-string 사용 | **SQL 인젝션** | `%s` + params |
| 2 | 목록에서 `content` 조회 | 서버 멈춤 | SELECT에 큰 컬럼 제외 |
| 3 | 반복문 안에서 DB 조회 | N+1, 느림 | 모아서 `ANY(%s)` 한 번 |
| 4 | 트랜잭션 없이 여러 INSERT | 반쪽 데이터 | `async with conn.transaction()` |
| 5 | `FOR UPDATE` 없이 읽고 쓰기 | 동시성 버그 | 쓰기 전 행 잠금 |
| 6 | 없는 행에 `FOR UPDATE` | 500 중복 오류 | 부모 행에 먼저 |
| 7 | `await` 빼먹음 | 코루틴 객체 반환 | 타입 힌트 확인 |
| 8 | 파라미터 튜플 쉼표 누락 `(x)` | 타입 오류 | `(x,)` |
| 9 | 응답에 스택트레이스 노출 | 보안 위험 | 로그에만 |
| 10 | 상수가 SQL CHECK와 불일치 | 간헐적 500 | 양쪽 동시 수정 |

## 10.4 디버깅

```bash
# 로그 레벨 올리기
LOG_LEVEL=DEBUG python app.py

# 요청 로그 형식
# 2026-09-20 14:23:01 INFO PUT /api/sops/a1b2 -> 201 (85 ms)

# 500이 나면 반드시 서버 로그를 보세요
# errors.py 의 log.exception 이 스택트레이스를 남깁니다
```

**증상별 원인:**

| 증상 | 의심할 곳 |
|---|---|
| 간헐적 500 | 동시성 (FOR UPDATE 누락) / PgBouncer prepared statement |
| 특정 요청만 느림 | N+1 / 큰 컬럼 조회 / 인덱스 없음 |
| 422가 계속 남 | schemas.py 모델과 프론트가 보내는 JSON 불일치 |
| 404인데 문서는 있음 | `current_version_id` 가 NULL |
| 저장은 됐는데 목록에 없음 | `status='retired'` |
| DB 접속 실패 | `DATABASE_URL` / 방화벽 / 풀 고갈 |

---

## 마지막으로 — 백엔드의 5가지 원칙

```
1. 계약을 먼저 정하고 코드를 쓴다
     경로 · 메서드 · 요청 · 응답. /docs 가 곧 명세서다.

2. 사용자 입력은 절대 믿지 않는다
     %s 자리표시자, 형식 검증, DB 제약. 세 겹으로 막는다.

3. 큰 데이터를 함부로 읽지 않는다
     목록에 content 를 넣는 순간 서버가 죽는다.

4. 동시에 두 사람이 들어온다고 가정한다
     FOR UPDATE, 트랜잭션, 버전 검사. 혼자 테스트하면 절대 안 나타난다.

5. 오류는 "다음에 뭘 해야 하는지"를 알려준다
     code + message + 필요한 데이터. 프론트가 자동으로 처리할 수 있게.
```

---

*이 문서는 `app/` 아래 코드를 기준으로 작성되었습니다. 코드가 바뀌면 이 문서도 함께 갱신하세요.*
