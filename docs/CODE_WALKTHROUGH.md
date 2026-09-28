# 코드 따라 읽기 — "이 함수는 언제, 무엇을, 어떻게" 로 읽는 백엔드

> 이 문서는 "함수 목록" 이 아니라 **"이럴 때 → 이 코드가 → 이렇게 움직인다"** 순서로 씁니다.
> 참고 문서: `docs/BACKEND_GUIDE.md` (전체 레퍼런스) · `docs/DB_SCHEMA.md` (표 구조) · `docs/SQL_BASICS.md` (SQL 기초)
>
> 코드 위치는 `파일:줄번호` 로 적었습니다. 줄 번호는 2026-09 기준이라 나중에 몇 줄씩 밀릴 수 있습니다.
> 그럴 땐 함수 이름으로 찾으세요.

---

## 목차

0. 읽는 법
1. `PUT` 이 뭔가요 — HTTP 요청은 "편지" 입니다
2. 전체 구조 — 식당 비유
3. 요청 하나가 지나가는 길 — "저장" 을 8단계로
4. `document_meta()` — 언제 불리고, 꺼낸 값을 어떻게 쓰나 (경우별)
5. 꺼내 둔 값은 나중에 누가 읽나
6. `if … raise` 는 어떻게 동작하나 — `else` 가 없어도 되는 이유
7. 오류가 나면 DB 는 어떻게 되나 — 트랜잭션과 롤백
8. 직접 따라가 보기

---

## 0. 읽는 법

이 서버는 "브라우저가 편지를 보내면 → 서버가 처리하고 → 답장한다" 를 반복하는 프로그램입니다.
그래서 코드를 읽는 가장 쉬운 방법은 **편지 한 통을 골라 끝까지 따라가는 것**입니다.
이 문서는 "문서 저장" 편지 한 통을 처음부터 끝까지 따라갑니다.

---

## 1. `PUT` 이 뭔가요 — HTTP 요청은 "편지" 입니다

브라우저(편집기 HTML)와 서버는 편지로 대화합니다. 편지 한 통은 세 부분입니다.

```
동사   주소                       본문(내용물)
PUT    /api/sops/3f2a-…           { "doc": {…편집기 JSON…}, "saved_by": "홍길동" }
```

- **동사** = 이 주소에 뭘 하고 싶은지. 종류는 5개뿐입니다.
- **주소** = 어느 문서에. `/api/sops/3f2a-…` 는 "id 가 3f2a 인 SOP 문서".
- **본문** = 같이 보내는 내용물. 읽기만 할 땐 없고, 저장할 땐 문서 JSON 이 들어갑니다.

### 동사 5개

| 동사 | 뜻 | 본문 | 우체국 비유 |
|---|---|---|---|
| `GET` | 보여줘 | 없음 | "3번 서류 열람하러 왔어요" |
| `POST` | 새로 만들어줘 | 있음 | "신규 접수요, 서류 여기 있어요" |
| `PUT` | 이걸로 통째로 바꿔줘 | 있음 | "기존 3번 서류, 이 새 버전으로 교체해 주세요" |
| `PATCH` | 일부만 고쳐줘 | 있음(작음) | "3번 서류 번호만 바꿔 주세요" |
| `DELETE` | 지워줘 | 없음 | "3번 서류 폐기해 주세요" |

### 이 프로젝트에서 실제로 오가는 편지

브라우저 쪽 코드 `static/SOP_STUDIO.html` 3641~3794행의 `api('동사', '주소', 본문)` 호출을 그대로 옮긴 표입니다.

| 화면에서 한 일 | 브라우저가 보내는 편지 | 서버에서 받는 함수 |
|---|---|---|
| 문서 목록 열기 | `GET /api/sops` | `sops.py:112 list_documents` |
| 번호로 문서 열기 | `GET /api/sops/by-no/SOP-001` | `sops.py:163` |
| id 로 문서 열기 | `GET /api/sops/{id}` | `sops.py:175` |
| **새 문서 첫 저장** | `POST /api/sops` + JSON | `sops.py:343 create_document` |
| **기존 문서 저장** | `PUT /api/sops/{id}` + JSON | `sops.py:385 save_document` |
| 번호 바꾸기 | `PATCH /api/sops/{id}/number` + 새 번호 | `sops.py:422` |
| 문서 폐기 | `DELETE /api/sops/{id}` | `sops.py:500` |
| 이 문서를 참조하는 문서 | `GET /api/sops/{id}/referenced-by` | `sops.py:518` |
| 편집 시작(잠금) | `POST /api/sops/{id}/lock` | `locks.py:31` |
| 편집 끝(잠금 해제) | `DELETE /api/sops/{id}/lock` | `locks.py:103` |
| 버전 이력 보기 | `GET /api/sops/{id}/versions` | `versions.py:32` |
| 옛 버전 열기 | `GET /api/sops/{id}/versions/{n}` | `versions.py:51` |
| 두 버전 비교 | `GET /api/sops/{id}/versions/{a}/diff/{b}` | `versions.py:145` |

서버 코드에서 `@router.put("/sops/{doc_id}")` 라고 적힌 줄이 **"PUT 동사로 이 주소에 편지가 오면 아래 함수를 실행해라"** 라는 뜻입니다.

### POST 와 PUT 이 왜 둘 다 "저장" 인가

브라우저 코드 `static/SOP_STUDIO.html:3650~3651` 이 답입니다.

```javascript
if (rec.id) return api('PUT',  '/api/sops/' + rec.id, {...});   // id 가 있다 = 이미 DB 에 있는 문서 → 교체
return            api('POST', '/api/sops',            {...});   // id 가 없다 = 처음 저장 → 신규
```

처음 저장하면 서버가 uuid 를 만들어 돌려주고, 브라우저는 그 뒤부터 그 id 로 PUT 을 보냅니다.

### 답장의 숫자 코드

| 코드 | 뜻 | 이 프로젝트에서 언제 |
|---|---|---|
| 200 / 201 | 됐음 / 새로 만들었음 | 정상 |
| 400 | 편지 내용이 이상함 | 문서 안 번호와 DB 번호가 다를 때 (`sop_no_mismatch`) |
| 404 | 그 주소에 아무것도 없음 | 없는 id 로 열 때 |
| 409 | 충돌 | 내가 연 뒤 남이 먼저 저장했을 때 (`version_conflict`), 번호 중복 (`sop_no_taken`) |
| 422 | 형식 불량 | `format` 값이 틀렸을 때 (`invalid_document`) |
| 423 | 잠겨 있음 | 남이 편집 중일 때 (`locked`) |

---

## 2. 전체 구조 — 식당 비유

```
브라우저 (편집기 HTML)          ← 손님. 주문서(JSON) 를 보내고 음식(응답) 을 받음
        │  HTTP 요청
        ▼
app/main.py                     ← 식당 문. 손님을 받고 "어느 창구로 가세요" 안내
        │
        ▼
app/routers/  (창구 3개)        ← 주문 접수. "무엇을 할지" 를 순서대로 지휘
   sops.py      문서 목록·열기·저장·번호변경·삭제        (창구 9개)
   versions.py  버전 이력·옛 버전 열기·비교              (창구 3개)
   locks.py     편집 잠금 잡기·풀기                      (창구 2개)
        │
        ├──▶ app/derive.py      ← 손질 담당. 주문서(JSON) 에서 필요한 재료만 골라냄. DB 를 모름
        ├──▶ app/refs.py        ← "다른 SOP 참조" 를 DB 에서 찾아 맞춰 줌
        ├──▶ app/schemas.py     ← 주문서·응답의 "모양" 정의 (필수 항목이 빠지면 여기서 거절)
        └──▶ app/db.py          ← 창고(PostgreSQL) 연결 관리
                    │
                    ▼
              PostgreSQL (표 5개)
```

### 역할 구분이 핵심

- **`routers/` 는 지휘자**입니다. 순서를 정하고 SQL 을 실행합니다. 하지만 JSON 의 어느 키에 뭐가 있는지는 모릅니다.
- **`derive.py` 는 손질 담당**입니다. JSON 의 키 이름(`sop.id`, `studio.area`)을 아는 **유일한 파일**입니다. 대신 DB 를 전혀 모릅니다. 입력을 받아 출력만 돌려주는 순수 함수라, 프론트가 키 이름을 바꾸면 이 파일만 고치면 됩니다.
- **`meta` 는 그 둘 사이를 오가는 쟁반**입니다. 손질 담당이 골라낸 재료 7개를 담아 지휘자에게 건네고, 지휘자는 그걸 SQL 빈칸에 붓습니다.

### 파일 크기

| 파일 | 줄 수 | 역할 |
|---|---|---|
| `app/routers/sops.py` | 527 | 문서 관련 창구 9개 |
| `app/derive.py` | 385 | JSON 손질 |
| `app/refs.py` | 205 | SOP 참조 맞추기 |
| `app/routers/versions.py` | 200 | 버전 이력 창구 3개 |
| `app/schemas.py` | 198 | 요청·응답 모양 |
| `app/main.py` | 186 | 식당 문 |
| `app/config.py` | 149 | 환경변수 읽기 |
| `app/db.py` | 137 | DB 연결 풀 |
| `app/routers/locks.py` | 133 | 잠금 창구 2개 |
| `app/errors.py` | 76 | 오류 형식 |
| `app/routers/common.py` | 60 | 창구들이 같이 쓰는 도우미 |
| `app/deps.py` | 32 | 요청마다 "사용자 이름" 과 "DB 연결" 꺼내기 |

읽는다면 `sops.py` 의 `_append_version` 한 함수(200~310행)만 따라가면 저장의 전부가 보입니다.

---

## 3. 요청 하나가 지나가는 길 — "저장" 을 8단계로

브라우저가 `PUT /api/sops/{id}` 로 문서 JSON 을 보냈다고 합시다.

1. `main.py` 가 받아서 `sops.py` 의 `save_document` 함수로 넘깁니다. (`main.py:121` 에서 라우터 등록)
2. `save_document` 는 먼저 `derive.py` 의 `validate_document` 를 불러 "우리 문서 형식 맞아?" 를 확인합니다. 틀리면 422. (`sops.py:398`)
3. `derive.py` 의 `document_meta` 를 불러 JSON 에서 이름·구역·개정 같은 값 7개를 꺼냅니다. 이게 `meta` 입니다. (`sops.py:399`)
4. 문서가 있는지, 번호가 맞는지 확인합니다. (`sops.py:403~409`)
5. 트랜잭션을 열고 `_append_version` 으로 들어갑니다. (`sops.py:413`)
6. `_append_version` 안에서 `refs.py` 를 불러 순서도의 "다른 SOP 상자" 가 실제 어느 문서인지 DB 에서 찾아 채웁니다. (`sops.py:242`)
7. `derive.py` 의 `derive_flow_rows` 를 불러 순서도 노드·선을 표 행 모양으로 풀어냅니다. (`sops.py:245`)
8. SQL 을 실행합니다. `meta` 값을 INSERT 와 UPDATE 의 빈칸에 넣고, JSON 원본은 `content` 에 통째로 넣습니다. 결과와 경고 목록을 응답으로 돌려줍니다. (`sops.py:250~305`)

---

## 4. `document_meta()` — 언제 불리고, 꺼낸 값을 어떻게 쓰나

### 언제 불리나

**딱 두 가지 경우**입니다. 편집기에서 "라이브러리에 저장" 을 눌렀을 때, 그 문서가
- (경우 1) 처음 저장하는 새 문서인지 → `POST` → `create_document`
- (경우 2) 이미 있는 문서를 수정한 건지 → `PUT` → `save_document`

목록 보기·문서 열기·잠금·삭제·번호 변경 때는 **불리지 않습니다.** `grep document_meta app/` 로 확인하면 `sops.py` 세 곳(`:204`, `:356`, `:399`)뿐입니다.

### 무엇을 꺼내나 — 두 경우 모두 같음

편집기가 보낸 JSON 이 이렇다고 합시다.

```json
{ "sop": { "id": "SOP-001", "name": "에칭 공정" },
  "studio": { "area": "E", "revision": "A", "owner": "홍길동", "tags": ["식각", ""] },
  "format": "sop-editor-mock", "version": 1,
  "blocks": [ …수백 KB… ] }
```

`document_meta()` (`derive.py:190`) 는 이 7개를 꺼냅니다.

```
sop_no = "SOP-001"     name = "에칭 공정"    area = "E"
revision = "A"         owner = "홍길동"      tags = ["식각"]   ← 빈 문자열 하나 버림
format = "sop-editor-mock"   format_version = 1   warnings = []
```

꺼내면서 정리도 합니다.
- `area` 가 허용 목록(`P/E/D/T/C`)에 없으면 → `''` 로 바꾸고 `warnings` 에 한 줄
- `tags` 는 빈 문자열을 버리고 문자열만 남김
- `version` 이 정수가 아니면 → `0`

담는 상자는 `DocumentMeta` 라는 `@dataclass` 입니다. (`derive.py:36`)

### 경우 1. 새 문서를 처음 저장할 때 — `create_document` (`sops.py:343`)

1. `meta.sop_no` 로 "SOP-001 이 이미 있나?" 검사합니다. 있으면 409 로 거절합니다. (`:362`)
2. `meta.sop_no`, `meta.name`, `meta.area` 세 개로 **문서 행을 새로 만듭니다.** (`:370`)
   ```sql
   INSERT INTO sop_documents (sop_no, name, area, created_by)
   VALUES ('SOP-001', '에칭 공정', 'E', '홍길동') RETURNING id
   ```
   여기서 받은 id 가 앞으로 이 문서의 영구 신분증입니다.
3. 그 id 를 들고 공통 본체 `_append_version` 으로 들어갑니다. (`:373`, `check_base_version=False`)

### 경우 2. 이미 있는 문서를 수정해서 저장할 때 — `save_document` (`sops.py:385`)

1. `meta.sop_no` 를 DB 에 저장된 번호와 **비교만** 합니다. 다르면 400 으로 거절합니다. (`:404`)
   사용자가 편집기에서 번호 칸을 고쳐 버린 경우를 막는 검사입니다. 번호 변경은 `PATCH /number` 전용입니다.
2. 문서 행은 **새로 만들지 않습니다.** 이미 있으니까요. 그래서 경우 1 의 INSERT 가 없습니다.
3. 기존 id 를 들고 공통 본체로 들어갑니다. (`:413`, `check_base_version=True`)

### 공통. `_append_version` (`sops.py:200~310`) — 두 경우 모두 여기서 끝납니다

1. 문서 행을 `FOR UPDATE` 로 잠급니다. 같은 문서를 동시에 저장하는 두 요청이 줄을 서게 됩니다. (`:209`)
2. `meta.sop_no` 를 한 번 더 비교합니다. (`:217`) 경우 2 에서만 켜집니다. 경우 1 은 방금 만든 행이라 검사를 끕니다.
3. 최대 버전 번호를 찾고, 경우 2 라면 "내가 열었던 버전" 과 같은지 검사합니다. 다르면 409. (`:227~239`) → 6장 참고
4. `meta.warnings` 를 참조 경고·노드 경고와 합쳐 둡니다. (`:246`)
5. `meta.format`, `format_version`, `revision`, `owner`, `tags` 로 **버전 행을 새로 만듭니다.** (`:250`)
   ```sql
   INSERT INTO sop_versions (document_id, version_no, format, format_version, content, revision, owner, tags, …)
   VALUES (id, 다음번호, 'sop-editor-mock', 1, {JSON 통째}, 'A', '홍길동', {'식각'}, …)
   ```
   경우 1 이면 version_no 는 1, 경우 2 면 기존 최대값 + 1 입니다.
   `content` 에는 meta 가 아니라 **JSON 원본이 통째로** 들어갑니다 (`Jsonb(doc)`).
6. 순서도 노드·선 행을 `flow_nodes` / `flow_edges` 에 넣습니다. (`:271`, `:287`)
7. `meta.name`, `meta.area` 로 **문서 행을 덮어씁니다.** (`:302`)
   ```sql
   UPDATE sop_documents SET current_version_id = 새버전id, name = '에칭 공정', area = 'E', status = …, updated_at = now()
   WHERE id = id
   ```
   경우 1 에서는 방금 넣은 값과 같아서 실질 변화가 없습니다. 경우 2 에서는 사용자가 이름이나 구역을 바꿨다면 여기서 반영됩니다.
8. 합쳐 둔 warnings 를 응답에 실어 보냅니다. DB 에는 안 갑니다.

### 두 경우의 차이 한 표

| meta 값 | 새 문서 (경우 1) | 수정 저장 (경우 2) |
|---|---|---|
| `sop_no` | 중복 검사 후 문서 행에 씀 | 저장된 번호와 같은지 검사만 함 |
| `name`, `area` | 문서 행 INSERT, 그 뒤 UPDATE 로 한 번 더 | UPDATE 로 덮어씀 |
| `revision`, `owner`, `tags`, `format`, `format_version` | 버전 1 행에 씀 | 새 버전 행에 씀 |
| `warnings` | 응답으로만 | 응답으로만 |

### 구체 예

경우 2 에서 사용자가 이름을 "에칭 공정" → "에칭 공정 v2" 로, 개정을 "A" → "B" 로 바꿔 저장하면
- `name` 은 문서 행에서 **덮어써져** 옛 이름이 사라집니다.
- `revision` 은 **새 버전 행**에 "B" 로 들어가면서, 옛 버전 행에는 "A" 가 그대로 남습니다.

옛 이름이 궁금하면 그 버전의 `content->'sop'->>'name'` 을 보면 됩니다. `content` 에는 언제나 전부 들어 있습니다.

---

## 5. 꺼내 둔 값은 나중에 누가 읽나

`document_meta` 는 **쓸 때** 만 씁니다. 읽을 때는 이미 컬럼에 들어간 값을 SQL 로 바로 읽습니다.

왜 굳이 따로 빼 두냐면, `content` 는 문서 하나가 수백 KB(이미지 포함)라 목록 화면에서 문서 100개의 이름을 보여 주려고 100개를 전부 열면 수십 MB 를 읽게 됩니다. 그래서 작은 값만 별도 컬럼에 복사해 두고 그 컬럼만 읽습니다.

비유하면, 책(content)은 창고에 통째로 보관하고 책 제목·저자·분류만 카드에 적어 서랍에 둡니다. "E 구역 책 목록 보여줘" 는 카드 서랍만 뒤지면 되고, 창고에는 실제로 읽을 때만 갑니다.

| 화면/기능 | 읽는 컬럼 | 코드 |
|---|---|---|
| 문서 목록 (검색·구역 필터·정렬) | `sop_no`, `name`, `area`, `status`, `revision`, `owner` | `sops.py:152` |
| 버전 이력 목록 | `version_no`, `revision`, `saved_by`, `saved_at`, `change_note` | `versions.py:40` |
| "이 문서를 참조하는 문서" | `sop_no`, `name`, `area`, `status` | `refs.py:153` |

세 쿼리 모두 `content` 를 읽지 않습니다. 이름 검색은 `d.name ILIKE`, 구역 필터는 `d.area = %s` 로, 뽑아 둔 컬럼이 있어야 가능합니다.

> ⚠️ `tags` 는 뽑아서 `sop_versions.tags` 에 넣기는 하는데 **읽어 가는 코드가 없습니다.**
> 목록에도 안 나오고 검색에도 안 씁니다. "언젠가 태그 검색을 하려고" 미리 저장만 하는 상태입니다.

---

## 6. `if … raise` 는 어떻게 동작하나 — `else` 가 없어도 되는 이유

`sops.py:233~239`:

```python
if check_base_version and body.base_version_no is not None and body.base_version_no != max_no:
    raise ApiError(
        409, "version_conflict",
        f"다른 사람이 먼저 v{max_no} 을(를) 저장했습니다. 다시 불러온 뒤 저장하세요.",
        current_version_no=max_no,
    )
new_version_no = max_no + 1
```

**질문: 여기서 오류가 나면 `new_version_no = max_no + 1` 도 실행되나?**
**답: 아니요.** `raise` 가 실행되면 그 아래 줄은 실행되지 않습니다.

`raise` 는 "여기서 함수를 즉시 중단하고 오류를 위로 던져라" 입니다. 파이썬은 그 순간 이 함수를 빠져나가 호출한 쪽으로 올라갑니다.

그러니 `if` 문은 이렇게 읽으면 됩니다.
**"충돌이면 여기서 끝. 충돌이 아니면 다음 줄로 내려가 새 번호를 만든다."**

`else` 가 없는데도 그렇게 동작하는 이유가 바로 `raise` 때문입니다. `raise` 대신 `print` 같은 것만 있었다면 그 다음 줄이 계속 실행됐을 겁니다.

이 패턴(검사해서 틀리면 `raise`, 맞으면 그냥 다음 줄)을 **"빠른 실패(early return / guard clause)"** 라고 부르며, 이 저장소의 거의 모든 검사가 이 모양입니다. `else` 를 안 쓰니 정상 흐름이 왼쪽에 쭉 붙어 읽기 쉽습니다.

### `if` 조건 세 개를 풀어 읽기

`check_base_version and body.base_version_no is not None and body.base_version_no != max_no`

| 조건 | 뜻 | 거짓이면 |
|---|---|---|
| `check_base_version` | PUT 인가? (POST 는 False) | 새 문서라 충돌 검사 자체를 안 함 |
| `body.base_version_no is not None` | 브라우저가 "내가 열었던 버전 번호" 를 보냈나? | 안 보냈으면(강제 저장) 검사 건너뜀 |
| `body.base_version_no != max_no` | 그 번호가 지금 최신과 다른가? | 같으면 아무도 끼어들지 않은 것 |

세 조건이 **모두** 참일 때만 409 입니다. `and` 는 하나라도 거짓이면 전체가 거짓입니다.

---

## 7. 오류가 나면 DB 는 어떻게 되나 — 트랜잭션과 롤백

6장의 `raise` 가 실행됐을 때 실제로 벌어지는 일입니다.

1. `_append_version` 의 `raise ApiError(409, …)` 에서 함수가 중단됩니다. `new_version_no` 이후의 INSERT, UPDATE 는 전혀 실행되지 않습니다.
2. 오류는 호출자인 `save_document` 의 `async with conn.transaction():` 블록(`sops.py:413`)을 빠져나갑니다. 이때 트랜잭션이 **자동 ROLLBACK** 됩니다. `FOR UPDATE` 로 잡았던 문서 행 잠금도 풀립니다. 이 시점까지 DB 에 쓴 것은 없으니 되돌릴 것도 없습니다.
3. 오류는 계속 올라가 `app/errors.py:53` 의 오류 처리기(`handle_api_error`)에 잡히고, 브라우저에는 409 응답과 함께 `{ "error": { "code": "version_conflict", "message": "다른 사람이 먼저 v3 을(를) 저장했습니다…", "current_version_no": 3 } }` 가 돌아갑니다.
4. 브라우저 코드(`static/SOP_STUDIO.html:3613 api()`)는 `res.ok` 가 아니므로 `Error` 를 던지고, 화면은 `err.status === 409` 를 보고 사용자에게 다시 불러오라고 안내합니다.

### 트랜잭션이 왜 중요한가

`_append_version` 은 표 4개에 씁니다. `sop_versions` INSERT → `flow_nodes` INSERT → `flow_edges` INSERT → `sop_documents` UPDATE.
만약 세 번째에서 오류가 나면? 트랜잭션이 없으면 버전 행과 노드 행은 남고 문서의 "현재 버전" 포인터는 옛것을 가리키는 **반쪽짜리 저장**이 됩니다.
`async with conn.transaction():` 안에서 하면 하나라도 실패할 때 **전부 취소**되어 저장 전 상태로 돌아갑니다. 그래서 4개 INSERT/UPDATE 가 전부 그 블록 안에 있습니다.

### 오류 종류별로 어디서 멈추나

| 오류 | 어디서 `raise` | DB 에 쓴 것 | 트랜잭션 |
|---|---|---|---|
| 422 형식 불량 | `validate_document` (`sops.py:398`) | 없음 | 아직 열지도 않음 |
| 400 번호 불일치 | `save_document:404` 또는 `_append_version:217` | 없음 | 후자는 열렸다가 롤백 |
| 409 버전 충돌 | `_append_version:233` | 없음 | 열렸다가 롤백 |
| 409 번호 중복 (POST) | `create_document:362` 또는 `UniqueViolation` (`:375`) | 없음 | 후자는 INSERT 됐다가 롤백 |
| DB 자체 오류 (연결 끊김 등) | 어디서든 | 일부 | 롤백 |

---

## 8. 직접 따라가 보기

서버를 띄우고(`python app.py`) 아래를 순서대로 해 보면 4장·6장·7장이 눈에 보입니다.

```bash
# 1. 새 문서 저장 (경우 1). 응답의 id 를 적어 두세요.
curl -s -X POST localhost:8000/api/sops -H 'Content-Type: application/json' -H 'X-User: test' \
  -d '{"doc":{"format":"sop-editor-mock","version":1,"blocks":[],"sop":{"id":"SOP-TEST","name":"테스트"},"studio":{"area":"E","revision":"A"}}}'

# 2. 같은 번호로 또 POST → 409 sop_no_taken (경우 1 의 1단계)
#    (같은 명령 다시 실행)

# 3. 수정 저장 (경우 2). {id} 는 1 의 응답값. base_version_no=1 이면 성공, revision 이 B 로 새 버전 행에 들어감
curl -s -X PUT localhost:8000/api/sops/{id} -H 'Content-Type: application/json' -H 'X-User: test' \
  -d '{"doc":{"format":"sop-editor-mock","version":1,"blocks":[],"sop":{"id":"SOP-TEST","name":"테스트 v2"},"studio":{"area":"E","revision":"B"}},"base_version_no":1}'

# 4. base_version_no=1 로 한 번 더 → 409 version_conflict (6장의 raise). 최신은 이미 2 이므로.
#    (같은 명령 다시 실행)

# 5. 번호를 바꿔서 PUT → 400 sop_no_mismatch (경우 2 의 1단계)
#    sop.id 를 "SOP-OTHER" 로 바꿔 실행

# 6. 버전 이력 → revision 이 A, B 두 줄. name 은 목록에서 "테스트 v2" 하나만 (덮어쓰기)
curl -s localhost:8000/api/sops/{id}/versions -H 'X-User: test'
curl -s 'localhost:8000/api/sops?q=SOP-TEST' -H 'X-User: test'
```

psql 로 직접 보려면:

```sql
SELECT sop_no, name, area, current_version_id FROM sop_documents WHERE sop_no = 'SOP-TEST';
SELECT version_no, revision, owner, tags, saved_by FROM sop_versions
 WHERE document_id = (SELECT id FROM sop_documents WHERE sop_no = 'SOP-TEST') ORDER BY version_no;
```

첫 표에는 줄이 하나(덮어쓰기), 둘째 표에는 버전마다 줄이 하나(추가)인 것이 4장의 핵심입니다.
