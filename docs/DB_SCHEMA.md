# SOP Studio — DB 스키마 해설서

> 대상: 백엔드 담당자. 프론트 코드 지식은 필요 없습니다.
> 원본 파일: `sql/sop_schema.sql` (새 설치용 전체 스키마), `sql/migrations/*.sql` (기존 DB 변경분)
> 이 문서는 "표가 왜 이렇게 생겼는지 / 어떤 코드가 언제 읽고 쓰는지" 를 설명합니다.
> API·라우터 설명은 `docs/BACKEND_GUIDE.md` 를 보세요.

> 🧭 **"이 함수는 언제 불리고 어떻게 움직이나" 가 궁금하면 `docs/CODE_WALKTHROUGH.md`** — 저장 요청 한 통을 처음부터 끝까지 따라갑니다.
> 📘 **SQL 이 처음이라면 `docs/SQL_BASICS.md` 를 먼저 읽으세요.**
> `SELECT` / `JOIN` / `FOR UPDATE` / 트랜잭션 / 인덱스를 이 저장소의 실제 문장으로 처음부터 설명합니다.
> 그걸 읽고 오면 아래 내용이 전부 읽힙니다.

---

## 0. 한 장 요약

```
sop_documents  (SOP 한 건 = 한 행, 영구)
   │  id                 ← 진짜 신분증 (절대 안 바뀜)
   │  sop_no             ← 사람이 보는 번호 (바뀔 수 있음)
   │  current_version_id ─────────────┐  "지금 최신" 포인터
   │                                  │
   ├─1:N─ sop_versions  (저장 1회 = 1행, 불변) ◄┘
   │         │  content jsonb   ← 편집기 JSON 통째. 복원의 유일한 근거
   │         ├─1:N─ flow_nodes  (순서도 상자. content 에서 뽑아낸 검색용 사본)
   │         └─1:N─ flow_edges  (순서도 연결선. 지금은 쓰기만 하고 읽는 곳 없음)
   │
   └─1:1─ sop_edit_locks  (편집 잠금. 있을 수도 없을 수도)
```

표는 **5개**입니다. 외우는 순서는 위 그림 그대로:
문서 → 버전 → 노드/연결선 → 잠금.

---

## 1. 표를 이렇게 나눈 이유 (설계 원칙 4가지)

### 원칙 1 — 복원은 `sop_versions.content` 하나로 끝난다

편집기가 보낸 JSON 을 **통째로** `content` 에 넣습니다. 문서를 열 때는
`content` 만 그대로 돌려줍니다. 컬럼으로 쪼갠 값에서 JSON 을 다시 조립하지 않습니다.

왜? 쪼갰다가 다시 합치면 **반드시 어딘가 빠집니다**. 편집기에 새 필드가 하나
추가될 때마다 백엔드 컬럼도 따라 늘려야 하고, 안 늘리면 그 값은 저장했다가
열면 사라집니다. `content` 통짜 저장은 그 문제가 구조적으로 안 생깁니다.

```python
# app/routers/sops.py:82 — 문서 열기는 이게 전부
"SELECT id, version_no, content FROM sop_versions WHERE id = %s"
```

### 원칙 2 — `flow_nodes` / `flow_edges` 는 "파생 사본"이다

`content` 안에 순서도 노드가 다 들어 있는데 왜 표로 또 풉니까?
**jsonb 안을 뒤지는 검색은 느리고 SQL 이 지저분해지기 때문**입니다.

"MES 를 쓰는 SOP 전부", "이 문서를 참조하는 SOP", "v3 과 v5 의 노드 차이" 같은
질문은 평평한 표에서 `WHERE` 한 줄이면 끝납니다.

중요한 규칙: **파생 사본은 진실이 아닙니다.** 둘이 어긋나면 언제나 `content` 가
맞습니다. 그래서 파생 표는 편집기에 절대 돌려주지 않고, 저장할 때마다
새 버전 id 로 새로 만들어 넣습니다(기존 행 UPDATE 없음).

### 원칙 3 — 버전 행은 불변(immutable)

`sop_versions` 의 행은 **한 번 만들면 절대 고치지 않습니다.** 저장할 때마다
`version_no` 가 1 늘어난 새 행이 추가됩니다. `flow_nodes` / `flow_edges` 도
새 `version_id` 로 새로 들어갑니다.

덕분에:
- 버전 이력이 자동으로 남음 (`GET /versions`)
- 과거 버전 열기 / 버전 간 diff 가 그냥 됨
- "저장하다 반쯤 망가진 문서" 가 원리적으로 안 생김 — 실패하면 새 행이 아예 안 생길 뿐

대가는 용량입니다. 이미지가 든 문서는 버전당 수백 KB. 이력 보존이 SOP의
법적 요구라 받아들인 비용입니다.

### 원칙 4 — 참조는 번호가 아니라 id 로 건다

순서도의 "SOP 상자"(다른 SOP 를 가리키는 노드)는 값을 3개 가집니다.

| 값 | 성격 | 역할 |
|---|---|---|
| `ref_document_id` | uuid, FK | **진짜 연결.** 번호가 바뀌어도 안 끊김 |
| `ref_sop_no` | text | 표시용 번호 캐시 |
| `ref_sop_name` | text | 표시용 이름 캐시 |

`SOP-ETCH-001` 이 `SOP-ETCH-010` 으로 바뀌어도 `ref_document_id` 는 그대로라
연결이 살아 있습니다. 표시용 글자는 저장할 때마다 서버가 최신값으로 덮어씁니다.

`ref_document_id` 가 NULL 이면 **"번호만 적혀 있고 아직 그 문서가 없음"**(미작성)
이라는 뜻이고, 나중에 그 번호로 문서를 만들면 다음 저장 때 자동으로 연결됩니다
(`app/refs.py` 의 규칙 3 = 승격).

---

## 2. 표별 상세

### 2.1 `sop_documents` — 문서 (영구 신분)

```sql
CREATE TABLE sop_documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sop_no             text NOT NULL UNIQUE,
    name               text NOT NULL DEFAULT '',
    area               text NOT NULL DEFAULT '' CHECK (area IN ('', 'P','E','D','T','C')),
    status             text NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','review','approved','retired')),
    current_version_id uuid,          -- FK 는 sop_versions 생성 뒤에 ALTER 로 추가
    created_by         text NOT NULL DEFAULT '',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
```

| 컬럼 | 역할 | 언제 바뀌나 |
|---|---|---|
| `id` | 영구 신분증. 모든 FK 가 이걸 가리킴 | 절대 안 바뀜 |
| `sop_no` | 사람이 읽는 번호. 트리 정렬 키. **UNIQUE** | `PATCH /api/sops/{id}/number` |
| `name` | SOP 이름 | 저장할 때마다 `content` 의 값으로 갱신 |
| `area` | 적용 AREA (`P`/`E`/`D`/`T`/`C`/`''`) | 저장할 때마다 갱신 |
| `status` | `draft` → `review` → `approved`, 폐기는 `retired` | 폐기 API, 폐기된 문서에 저장 시 `draft` 로 부활 |
| `current_version_id` | "지금 최신" 포인터 | 저장할 때마다 새 버전 id 로 이동 |
| `created_by` / `created_at` | 최초 작성자·시각 | 최초 INSERT 때만 |
| `updated_at` | 마지막 변경 시각 | 저장 / 번호변경 / 폐기 |

**왜 `current_version_id` 포인터를 두었나?**
없어도 `MAX(version_no)` 로 구할 수 있지만, 문서를 열 때마다 서브쿼리 + 정렬이
필요합니다. 포인터 하나면 `JOIN` 한 번으로 끝납니다. "최신"은 가장 자주 하는
질문이라 그만한 값어치가 있습니다.

**왜 FK 를 나중에 `ALTER` 로 붙였나?**
`sop_documents` → `sop_versions` → `sop_documents` 순환 참조라, 표를 만드는
시점에는 상대가 아직 없기 때문입니다.

```sql
ALTER TABLE sop_documents
    ADD CONSTRAINT fk_documents_current_version
    FOREIGN KEY (current_version_id) REFERENCES sop_versions(id) ON DELETE SET NULL;
```

**`ON DELETE SET NULL` 인 이유**: 버전 행이 지워져도 문서 행은 살아야 합니다.
포인터만 NULL 이 되고, 문서는 "버전 없는 문서" 로 목록에 계속 나옵니다.

**`CHECK` 제약을 쓴 이유**: `status` 값 검사를 파이썬에도 쓸 수 있지만, DB 제약은
**어떤 경로로 들어와도** 막습니다 (psql 직접 UPDATE, 마이그레이션 스크립트 등).
값 종류가 고정된 컬럼은 애플리케이션이 아니라 DB 가 지키는 게 맞습니다.

**삭제가 없는 이유**: `DELETE FROM sop_documents` 를 하는 코드는 **한 줄도 없습니다.**
폐기는 `status = 'retired'` 로 바꿀 뿐입니다. SOP 는 이력 보존이 원칙이고,
지웠다가는 다른 문서의 `ref_document_id` 가 연쇄로 NULL 이 됩니다.

---

### 2.2 `sop_versions` — 버전 (저장 1회 = 1행, 불변)

```sql
CREATE TABLE sop_versions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    uuid NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
    version_no     int  NOT NULL,
    format         text NOT NULL,          -- "sop-editor-mock"
    format_version int  NOT NULL,
    content        jsonb NOT NULL,         -- ★ 편집기 JSON 통째
    revision       text NOT NULL DEFAULT '',
    owner          text NOT NULL DEFAULT '',
    tags           text[] NOT NULL DEFAULT '{}',
    change_note    text NOT NULL DEFAULT '',
    saved_by       text NOT NULL DEFAULT '',
    saved_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no)
);
```

| 컬럼 | 역할 |
|---|---|
| `document_id` | 어느 문서의 버전인가 |
| `version_no` | 1, 2, 3… 사람이 읽는 순번. **문서 안에서만 유일** |
| `format` / `format_version` | 편집기 JSON 의 형식 이름·판. 나중에 JSON 구조가 바뀌면 이걸 보고 변환 |
| `content` | **진실의 원본.** 이것만 있으면 문서를 완전히 복원 |
| `revision` / `owner` / `tags` | `content` 안에서 꺼내 놓은 검색·표시용 사본 |
| `change_note` | 저장 시 남긴 변경 사유 (API 본문의 `change_note`) |
| `saved_by` / `saved_at` | 누가 언제 저장했나 |

**`UNIQUE (document_id, version_no)`** 는 단순 중복 방지가 아니라
**동시 저장 방어의 마지막 그물**입니다. 두 요청이 동시에 `MAX(version_no)+1` 을
계산해 같은 번호를 쓰려 하면, 늦은 쪽이 여기서 막힙니다. (실제로는 앞단에서
`SELECT ... FOR UPDATE` 로 줄을 세우므로 여기까지 잘 안 옵니다.)

**`ON DELETE CASCADE`**: 문서가 지워지면 버전도 같이 사라집니다. 지금은 문서를
지우는 코드가 없으니 실질적으로는 "수동 정리 시 안전장치" 역할입니다.

**`revision` / `owner` / `tags` 를 왜 또 꺼내 두나?**
`content` 안에도 있지만, 목록 화면에서 문서 수백 건의 개정번호를 보여주려고
수백 KB짜리 `content` 를 읽을 수는 없습니다. 목록 SQL이 `content` 를 절대
읽지 않는 것은 의도적입니다:

```python
# app/routers/sops.py:152 — 목록은 content 를 건드리지 않는다
"SELECT d.id, d.sop_no, d.name, d.area, d.status, d.updated_at, "
"       v.version_no, COALESCE(v.revision,'') AS revision, COALESCE(v.owner,'') AS owner "
"  FROM sop_documents d LEFT JOIN sop_versions v ON v.id = d.current_version_id"
```

`LEFT JOIN` 인 이유: 아직 버전이 하나도 없는 문서도 목록에 나와야 합니다.
`COALESCE` 인 이유: 그 경우 `v.revision` 이 NULL 이라 `''` 로 메웁니다.

---

### 2.3 `flow_nodes` — 순서도 상자 (파생)

```sql
CREATE TABLE flow_nodes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id  uuid NOT NULL REFERENCES sop_versions(id) ON DELETE CASCADE,
    instance_id text NOT NULL,       -- 순서도 블록 id (문서에 순서도가 여러 장일 때 구분)
    node_key    text NOT NULL,       -- "seq_3", "decision_1", "sop_2"
    node_type   text NOT NULL CHECK (node_type IN ('start','seq','decision','sop','end')),
    name        text NOT NULL DEFAULT '',
    role_owner  text NOT NULL DEFAULT '',
    action      text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    systems     jsonb NOT NULL DEFAULT '[]',
    manual      jsonb NOT NULL DEFAULT '[]',
    ref_sop_no   text NOT NULL DEFAULT '',
    ref_sop_name text NOT NULL DEFAULT '',
    ref_document_id uuid REFERENCES sop_documents(id) ON DELETE SET NULL,
    position    jsonb NOT NULL DEFAULT '{}',   -- {"x":120,"y":90}
    font_size   int,
    UNIQUE (version_id, instance_id, node_key)
);
```

**`version_id` 에 매달린다는 점이 핵심입니다.** `document_id` 가 아닙니다.
버전마다 노드 행 전체가 따로 존재하고, 옛 버전 노드도 그대로 남습니다.
그래서 버전 간 diff 가 가능합니다.

**이것 때문에 생기는 함정 하나**: "현재 노드" 만 보려면 **반드시**
`current_version_id` 로 조인해야 합니다. 안 그러면 모든 버전의 노드가 섞입니다.

```sql
-- 맞음
JOIN sop_documents d ON d.current_version_id = n.version_id
-- 틀림 (v1~v9 노드가 전부 나옴)
JOIN sop_versions v ON v.id = n.version_id JOIN sop_documents d ON d.id = v.document_id
```

**`UNIQUE (version_id, instance_id, node_key)`**: 한 버전의 한 순서도 안에서
노드 키는 유일합니다. `node_key` 만으로는 부족합니다 — 한 문서에 순서도가
여러 장이면 각 장에 `seq_1` 이 따로 있을 수 있기 때문입니다.
그래서 diff 코드도 `(instance_id, node_key)` 튜플을 열쇠로 씁니다:

```python
# app/routers/versions.py:127
key = (node_row["instance_id"], node_row["node_key"])
nodes_by_key[key] = node_row
```

**컬럼별 용도**

| 컬럼 | 어떤 노드 타입에서 쓰나 |
|---|---|
| `name` | `start` / `end` 의 표시 이름 |
| `role_owner`, `action`, `description` | `seq`(담당·작업), `decision`(질문) |
| `systems` | `seq` — `[{"name":"MES","menus":["Lot 조회"]}]` |
| `manual` | `seq` — 수동 작업 목록 |
| `ref_sop_no` / `ref_sop_name` / `ref_document_id` | `sop` 노드 전용 |
| `position` / `font_size` | 화면 좌표·글자 크기 (**diff 에서 제외**) |

`position` 과 `font_size` 가 diff 비교 대상에서 빠진 이유: 상자를 5px 옮긴 것은
"내용이 바뀐 개정" 이 아니기 때문입니다. `app/routers/versions.py` 의
`COMPARE_FIELDS` 에서 의도적으로 뺐습니다.

**`ref_document_id` 의 `ON DELETE SET NULL`**: 가리키던 문서가 사라지면 자동으로
NULL 이 됩니다. 즉 "깨진 참조" 가 남지 않고 "미작성 참조" 로 강등됩니다.
`ref_sop_no` 글자는 남아 있으므로, 나중에 그 번호로 문서를 다시 만들면
다음 저장 때 자동 재연결됩니다.

---

### 2.4 `flow_edges` — 순서도 연결선 (파생)

```sql
CREATE TABLE flow_edges (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id  uuid NOT NULL REFERENCES sop_versions(id) ON DELETE CASCADE,
    instance_id text NOT NULL,
    edge_key    text NOT NULL,        -- "edge_5"
    source_key  text NOT NULL,        -- 출발 노드의 node_key
    target_key  text NOT NULL,        -- 도착 노드의 node_key
    source_port text NOT NULL DEFAULT '',   -- top/right/bottom/left
    target_port text NOT NULL DEFAULT '',
    condition   text NOT NULL DEFAULT '',   -- decision 분기 조건 (Yes/No/…)
    line_type   text NOT NULL DEFAULT 'orthogonal',
    route       jsonb NOT NULL DEFAULT '{}',
    UNIQUE (version_id, instance_id, edge_key)
);
```

구조는 `flow_nodes` 와 같습니다. `source_key` / `target_key` 는 FK 가 아니라
**그냥 text** 입니다 — 같은 버전 안의 `node_key` 를 가리키지만 DB 제약은 없습니다.
(FK 로 걸려면 `(version_id, instance_id, node_key)` 복합키를 참조해야 하고,
노드보다 연결선이 먼저 들어오는 순서 문제가 생깁니다.)

> ⚠️ **현재 상태: 쓰기 전용.**
> 저장할 때마다 `INSERT` 되지만 (`app/routers/sops.py:287`),
> **이 표를 읽는 엔드포인트가 하나도 없습니다.** diff 도 노드만 비교합니다.
> 순서도 복원은 `content` 로 하므로 기능상 문제는 없지만, 지금은 순수 비용입니다.
> 연결선 diff 나 "이 노드에서 갈 수 있는 곳" 같은 기능을 만들 계획이 없다면
> 지워도 되는 후보입니다.

---

### 2.5 `sop_edit_locks` — 편집 잠금

```sql
CREATE TABLE sop_edit_locks (
    document_id uuid PRIMARY KEY REFERENCES sop_documents(id) ON DELETE CASCADE,
    locked_by   text NOT NULL,
    locked_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
```

**`document_id` 가 곧 PK** 입니다. 별도 `id` 가 없습니다 — 문서당 잠금은
최대 하나이므로 문서 id 자체가 유일 식별자입니다. 덕분에
"이 문서 잠겼나?" 가 PK 조회 한 번입니다.

**`expires_at` 이 진짜 잠금 조건입니다.** 행이 있다고 잠긴 게 아닙니다.
모든 조회에 `expires_at > now()` 가 붙습니다:

```python
# app/routers/common.py:38
"SELECT locked_by, expires_at FROM sop_edit_locks WHERE document_id = %s AND expires_at > now()"
```

왜 이렇게? 브라우저를 그냥 닫으면 잠금 해제 요청이 안 옵니다. 만료 시각이
없으면 그 문서는 **영원히** 잠깁니다. TTL 을 두면 시간이 알아서 풀어 줍니다.
편집 중에는 하트비트가 주기적으로 `expires_at` 을 미룹니다.

**`ON DELETE CASCADE`**: 문서가 사라지면 잠금도 사라집니다. 당연한 정리입니다.

---

## 3. 동작별 DB 흐름

### 3.1 저장 (`POST` 새 문서 / `PUT` 새 버전)

`app/routers/sops.py` 의 `_append_version()` 하나가 둘 다 처리합니다.
전부 **하나의 트랜잭션** 안에서 일어납니다.

```
a. SELECT id, sop_no, status FROM sop_documents WHERE id=%s FOR UPDATE
     └ 같은 문서를 동시에 저장하려는 다른 요청은 여기서 줄을 선다

b. SELECT COALESCE(MAX(version_no),0) FROM sop_versions WHERE document_id=%s
     └ FOR UPDATE 와 한 문장에 못 써서 따로 조회 (PostgreSQL 제약)
     └ base_version_no 와 다르면 409 version_conflict

c. refs.resolve_references()  →  SELECT ... WHERE id = ANY(%s)      (한 번)
                                  SELECT ... WHERE sop_no = ANY(%s)  (한 번)
     └ 상자가 100개여도 조회는 2번. N+1 회피

d. derive.derive_flow_rows()  →  DB 안 씀 (순수 파이썬)

e. INSERT INTO sop_versions ... RETURNING id, saved_at
   executemany INSERT INTO flow_nodes ...     (노드 0개면 생략)
   executemany INSERT INTO flow_edges ...     (연결선 0개면 생략)
   UPDATE sop_documents SET current_version_id=…, name=…, area=…, status=…, updated_at=now()
```

**`FOR UPDATE` 를 맨 앞에 두는 이유**: 잠그지 않으면 두 요청이 같은 `MAX+1` 을
읽어 같은 `version_no` 로 INSERT 하려 하고, 한쪽이 UNIQUE 위반으로 500 이 납니다.
문서 행을 먼저 잠그면 한 명씩 통과합니다.

**`MAX()` 를 따로 조회하는 이유**: PostgreSQL 은 집계함수와 `FOR UPDATE` 를
한 문장에 쓸 수 없습니다. 어쩔 수 없이 두 문장입니다.

**폐기된 문서에 저장하면**: `status` 가 `retired` → `draft` 로 되살아나고
경고 한 줄이 붙습니다. 안 그러면 "저장은 됐는데 트리에 안 보이는" 상태가 됩니다.

### 3.2 문서 열기

```sql
-- 1) 문서 행 (id 또는 sop_no 로)
SELECT id, sop_no, current_version_id FROM sop_documents WHERE id = %s;
-- 2) 그 포인터가 가리키는 버전의 content
SELECT id, version_no, content FROM sop_versions WHERE id = %s;
-- 3) 참조 대상들의 "지금" 정보 (refs.load_ref_docs)
SELECT id, sop_no, name, area, status FROM sop_documents WHERE id = ANY(%s) ORDER BY area, sop_no;
-- 4) 지금 누가 잠갔나 (common.find_active_lock)
SELECT locked_by, expires_at FROM sop_edit_locks WHERE document_id = %s AND expires_at > now();
```

3번이 필요한 이유: `content` 안의 `sop_name` 은 **저장 시점의 캐시**입니다.
그 사이 참조 대상의 이름이 바뀌었거나 폐기됐을 수 있어, 편집기에 "지금 값" 을
따로 알려 줍니다.

### 3.3 잠금 잡기

```sql
BEGIN;
  SELECT id FROM sop_documents WHERE id = %s FOR UPDATE;          -- ★ 먼저 문서 행
  SELECT locked_by, expires_at, (expires_at > now()) AS is_active
    FROM sop_edit_locks WHERE document_id = %s FOR UPDATE;
  -- 남의 잠금이 살아 있으면 → 423 locked (롤백)
  -- 없으면 INSERT, 내 것이거나 만료됐으면 UPDATE
COMMIT;
```

**왜 문서 행을 먼저 잠그나 (★)**: `SELECT ... FOR UPDATE` 는 **없는 행을 잠글 수
없습니다.** 잠금 행이 아직 없는 문서에 두 사람이 동시에 첫 잠금을 걸면,
둘 다 "없음" 을 읽고 둘 다 INSERT 로 가서 늦은 쪽이 중복 오류(500)를 냅니다.
문서 행은 **항상 존재하므로** 그걸 먼저 잠그면 확실히 한 명씩 통과합니다.

이건 PostgreSQL 을 쓸 때 반복해서 만나는 함정입니다. 기억해 두세요:
**"없을 수도 있는 행" 을 잠그려면, 항상 있는 부모 행을 먼저 잠근다.**

### 3.4 번호 변경 (`PATCH /number`)

```sql
BEGIN;
  SELECT id, sop_no FROM sop_documents WHERE id=%s FOR UPDATE;   -- 404 검사
  SELECT locked_by, expires_at FROM sop_edit_locks
   WHERE document_id=%s AND locked_by<>%s AND expires_at>now();  -- 남이 편집중이면 423
  SELECT id FROM sop_documents WHERE sop_no=%s;                  -- 이미 쓰는 번호면 409
  -- 옛 번호를 "글자로만" 가리키던 문서 수를 세어 둔다 (로그용)
  UPDATE sop_documents SET sop_no=%s, updated_at=now() WHERE id=%s;
COMMIT;
```

**버전 행은 건드리지 않습니다.** 옛 버전의 `content` 안에는 옛 번호가 그대로
남습니다 — 그게 맞습니다. "그때는 그 번호였다" 가 이력이니까요.
현재 번호는 문서를 열 때 `common.content_with_current_sop_no()` 가 갈아 끼웁니다.

**`ref_document_id` 로 연결된 참조는 아무것도 안 해도 됩니다** — id 가 안 바뀌니까요.
다음 저장 때 표시용 글자만 새 번호로 덮어써집니다.
반면 **번호 글자로만 가리키던(미작성) 참조는 따라오지 못합니다.** 그래서 몇 건인지
세어서 로그에 남깁니다.

### 3.5 "이 문서를 참조하는 다른 문서"

```sql
SELECT d.id, d.sop_no, d.name, d.area, d.status, v.version_no
  FROM flow_nodes n
  JOIN sop_documents d ON d.current_version_id = n.version_id   -- ★ 현재 버전만
  JOIN sop_versions  v ON v.id = d.current_version_id
 WHERE (n.ref_document_id = %s OR (n.ref_document_id IS NULL AND n.ref_sop_no = %s))
   AND d.id <> %s                                               -- 자기 자신 제외
 GROUP BY d.id, d.sop_no, d.name, d.area, d.status, v.version_no
 ORDER BY d.area, d.sop_no;
```

- `★` 조인으로 옛 버전 노드를 자동 배제
- `OR` 로 **연결된 참조**(id)와 **미작성 참조**(번호 글자)를 둘 다 잡음
- `GROUP BY` 는 한 문서가 같은 SOP 를 여러 상자로 가리켜도 1건만 나오게 함

이 쿼리가 `flow_nodes` 표의 **존재 이유**입니다. `content` jsonb 를 뒤져서
같은 걸 하려면 훨씬 느리고 훨씬 못생긴 SQL 이 됩니다.

---

## 4. 인덱스

| 인덱스 | 대상 | 실제로 쓰이나 |
|---|---|---|
| `ix_documents_area_no` | `(area, sop_no)` | ✅ 목록 `ORDER BY d.area, d.sop_no` |
| `ix_documents_status` | `status` | ✅ 목록 상태 필터 |
| `ix_documents_name_trgm` | `name` GIN trgm | ✅ 이름 부분검색 (`ILIKE`) |
| `ix_versions_doc` | `(document_id, version_no DESC)` | ✅ 버전 이력, `MAX(version_no)` |
| `ix_versions_content` | `content` GIN | ❌ `content` 로 필터하는 쿼리 없음 |
| `ix_flow_nodes_version` | `version_id` | ✅ diff 의 노드 조회 |
| `ix_flow_nodes_type` | `node_type` | ❌ 해당 `WHERE` 없음 |
| `ix_flow_nodes_role` | `role_owner` | ❌ 해당 `WHERE` 없음 |
| `ix_flow_nodes_ref` | `ref_sop_no` (부분) | ✅ 미작성 참조 조회 |
| `ix_flow_nodes_ref_doc` | `ref_document_id` (부분) | ✅ 연결된 참조 조회 |
| `ix_flow_nodes_action_trgm` | `action` GIN trgm | ❌ 해당 `WHERE` 없음 |
| `ix_flow_nodes_systems` | `systems` GIN | ❌ 해당 `WHERE` 없음 |
| `ix_flow_edges_version` | `version_id` | ❌ `flow_edges` 를 읽는 곳 없음 |
| `ix_flow_edges_source` | `(version_id, source_key)` | ❌ 위와 같음 |
| `ix_edit_locks_expires` | `expires_at` | ⚠️ 잠금 조회는 PK 로 함. 만료 일괄정리 작업이 생기면 유용 |

**읽어야 할 점**: ❌ 표시된 인덱스는 **읽기를 빠르게 하지 않으면서 쓰기를 느리게
합니다.** 저장할 때마다 노드 수백 개를 INSERT 하는데, 인덱스 하나당 그만큼의
갱신 비용이 붙습니다. 특히 `ix_versions_content` (GIN on jsonb) 는 수백 KB JSON
전체를 토큰화하므로 저장 비용의 상당 부분을 차지합니다.

"나중에 쓸지도 모르니까" 만든 인덱스들인데, 인덱스는 **필요해진 날 1분이면
만들 수 있습니다.** 지금 두면 매 저장마다 대가를 치릅니다.

**부분 인덱스(`WHERE …`)는 좋은 예입니다.**
`ix_flow_nodes_ref_doc` 는 `ref_document_id IS NOT NULL` 인 행만 담습니다.
대부분의 노드는 SOP 참조가 아니므로 인덱스가 작고 빠릅니다.

---

## 5. 편집기 JSON ↔ 컬럼 대응표

`content` 통째 저장이 원칙이지만, 검색·목록용으로 몇 개만 꺼내 둡니다.
꺼내는 일은 `app/derive.py` 의 `document_meta()` / `derive_flow_rows()` 가 합니다.

| 편집기 JSON 경로 | 컬럼 |
|---|---|
| `doc.sop.id` | `sop_documents.sop_no` |
| `doc.sop.name` | `sop_documents.name` |
| `doc.studio.area` | `sop_documents.area` |
| `doc.studio.revision` | `sop_versions.revision` |
| `doc.studio.owner` | `sop_versions.owner` |
| `doc.studio.tags[]` | `sop_versions.tags` |
| `doc.format` / `doc.version` | `sop_versions.format` / `format_version` |
| **`doc` 전체** | **`sop_versions.content`** |
| `doc.blocks[flowchart].instanceId` | `flow_nodes.instance_id` |
| `…nodes[].node` / `.node_type` | `flow_nodes.node_key` / `node_type` |
| `…nodes[].role_owner` / `.action` | `flow_nodes.role_owner` / `action` |
| `…nodes[].systems` / `.manual` | `flow_nodes.systems` / `manual` |
| `…nodes[].sop_id` / `.sop_name` | `flow_nodes.ref_sop_no` / `ref_sop_name` (표시용) |
| `…nodes[].ref_document_id` | `flow_nodes.ref_document_id` (**진짜 연결**) |
| `…nodes[].x`, `.y` | `flow_nodes.position` |
| `…edges[].edge`/`.source`/`.target` | `flow_edges.edge_key`/`source_key`/`target_key` |
| `…edges[].condition` / `.line_type` | `flow_edges.condition` / `line_type` |

### 5-1. `DocumentMeta` — "꺼낸 값" 을 담는 바구니

편집기가 보낸 JSON(`doc`)은 수백 KB 짜리 큰 덩어리입니다. 그중 **표 컬럼에 따로 넣을 값 7개** 만
`app/derive.py` 의 `document_meta(doc)` 가 꺼내서 `DocumentMeta` 라는 작은 상자에 담습니다.

```python
@dataclass
class DocumentMeta:
    sop_no: str            # doc.sop.id        → sop_documents.sop_no
    name: str              # doc.sop.name      → sop_documents.name
    area: str              # doc.studio.area   → sop_documents.area
    revision: str          # doc.studio.revision → sop_versions.revision
    owner: str             # doc.studio.owner  → sop_versions.owner
    tags: list[str]        # doc.studio.tags   → sop_versions.tags
    format: str            # doc.format        → sop_versions.format
    format_version: int    # doc.version       → sop_versions.format_version
    warnings: list[str]    # 꺼내다 이상한 게 있으면 한 줄씩 (DB 에는 안 들어감)
```

`document_meta()` 는 꺼내면서 **정리** 도 합니다.
- `area` 가 허용 목록(`VALID_AREAS`)에 없으면 → `''` 로 바꾸고 `warnings` 에 한 줄
- `tags` 는 빈 문자열을 버리고 문자열만 남김
- `version` 이 정수가 아니면 → `0`

#### 어디서 만들고, 어디서 쓰나

`app/routers/sops.py` 에서 세 번 만듭니다 — POST(새 문서) · PUT(새 버전) · `_append_version`(둘의 공통 본체).

```
편집기 JSON (doc)
   │
   ├─ document_meta(doc) ──► meta  (7개 값 + warnings)
   │                          │
   │                          ├─ meta.sop_no   ─► 번호 검사 (PUT: 저장된 번호와 다르면 400)
   │                          ├─ meta.sop_no / name / area ─► INSERT sop_documents   (POST, 최초 1회)
   │                          ├─ meta.format / format_version / revision / owner / tags
   │                          │                 ─► INSERT sop_versions  (저장할 때마다 새 행)
   │                          ├─ meta.name / area ─► UPDATE sop_documents (저장할 때마다 덮어쓰기)
   │                          └─ meta.warnings  ─► 응답 JSON 의 warnings (DB 에는 안 감)
   │
   └─ Jsonb(doc) ────────────► sop_versions.content   (통째로, meta 와는 별개)
```

#### 값별로 "어디에, 언제" 저장되나

| meta 필드 | 저장되는 곳 | 언제 |
|---|---|---|
| `sop_no` | `sop_documents.sop_no` | **최초 생성(POST) 때만.** PUT 에서는 검사용으로만 쓰고 쓰지 않음 — 번호 변경은 `PATCH /number` 전용 |
| `name` | `sop_documents.name` | 저장할 때마다 **덮어쓰기** |
| `area` | `sop_documents.area` | 저장할 때마다 **덮어쓰기** |
| `revision` | `sop_versions.revision` | 저장할 때마다 **새 행** |
| `owner` | `sop_versions.owner` | 저장할 때마다 **새 행** |
| `tags` | `sop_versions.tags` (`text[]`) | 저장할 때마다 **새 행** |
| `format` / `format_version` | `sop_versions.format` / `format_version` | 저장할 때마다 **새 행** |
| `warnings` | **DB 에 저장 안 됨** | 응답 `SaveResponse.warnings` 로만 나감 |

핵심은 두 줄입니다.
- `sop_documents` 쪽 값은 **덮어쓰기** — 항상 "지금" 값만 남습니다. (이름을 바꾸면 옛 이름은 사라짐)
- `sop_versions` 쪽 값은 **새 행 추가** — 옛 값은 옛 버전 행에 그대로 남습니다. (원칙 3 불변)

옛 이름이 궁금하면 그 버전의 `content->'sop'->>'name'` 을 보면 됩니다. `content` 에는 언제나 전부 들어 있으니까요.

---

## 6. 스키마 적용과 마이그레이션

### 도구

```bash
python -m app.tools.apply_schema
```

`app/tools/apply_schema.py` 가 하는 일:

1. `SELECT to_regclass('sop_documents')` 로 **표가 이미 있는지** 확인
2. 없으면 → `sql/sop_schema.sql` 전체 실행 (새 설치)
3. 있으면 → `sql/migrations/*.sql` 을 **이름순으로** 실행 (기존 DB)
4. `pg_trgm` 확장을 못 만들면(권한 없음 등) `strip_trgm()` 이 해당 줄과
   `gin_trgm_ops` 인덱스 2줄만 빼고 실행 — **검색은 되고 속도만 조금 다릅니다**

### 마이그레이션 파일 규칙

| 규칙 | 이유 |
|---|---|
| 번호 접두사 `001_`, `002_` | 실행 순서가 파일명순이라 |
| **멱등(idempotent)** 하게 | 몇 번 실행해도 결과가 같아야 안전. `IF NOT EXISTS` / `IF EXISTS` |
| 새 설치 스키마에도 같은 내용 반영 | 새 DB 는 마이그레이션을 안 거치므로 |

현재 파일 2개:

- **`001_ref_document_id.sql`** — `flow_nodes.ref_document_id` 컬럼 + 부분 인덱스 +
  `COMMENT ON COLUMN` 추가. 원칙 4(참조를 id 로)를 도입한 변경입니다.
- **`002_drop_sop_drafts.sql`** — 안 쓰는 `sop_drafts` 표 제거.
  "이 기기에 임시 저장" 은 브라우저 localStorage 에서만 하므로 서버 표가 필요 없었습니다.

새 마이그레이션을 쓸 때 템플릿:

```sql
-- 003_설명.sql
ALTER TABLE 표이름 ADD COLUMN IF NOT EXISTS 새칸 타입;
CREATE INDEX IF NOT EXISTS ix_이름 ON 표이름 (칸);
DROP INDEX IF EXISTS ix_안쓰는것;
COMMENT ON COLUMN 표이름.새칸 IS '설명';
```

그리고 **`sql/sop_schema.sql` 에도 같은 변경을 반영하는 것을 잊지 마세요.**
안 하면 새로 설치한 DB 와 기존 DB 의 모양이 달라집니다.

---

## 7. 자주 틀리는 것 (체크리스트)

| 상황 | 틀린 방법 | 맞는 방법 |
|---|---|---|
| "현재 노드" 조회 | `flow_nodes` 를 그냥 SELECT | `JOIN sop_documents d ON d.current_version_id = n.version_id` |
| 문서 참조 | `sop_no` 로 FK | `id`(uuid)로 FK. 번호는 표시용 |
| 목록 조회 | `SELECT *` (content 포함) | `content` 를 명시적으로 제외 |
| 새 버전 저장 | 기존 행 `UPDATE` | 항상 새 행 `INSERT` |
| 잠금 잡기 | 잠금 행만 `FOR UPDATE` | 문서 행을 먼저 `FOR UPDATE` |
| 문서 삭제 | `DELETE FROM sop_documents` | `status='retired'` |
| 노드 식별 | `node_key` 만 | `(instance_id, node_key)` 쌍 |
| 잠금 확인 | 행 존재 여부 | `expires_at > now()` 조건 필수 |
| 버전 번호 계산 | `MAX+1` 을 그냥 | 문서 행 `FOR UPDATE` 아래에서 |

---

## 8. 앞으로 (스키마에 주석으로 남아 있는 것)

`sql/sop_schema.sql` 끝에 "나중에 추가할 것" 으로 적혀 있는 항목들입니다.
**지금은 구현되어 있지 않습니다.**

- `sop_documents.review_required boolean` — 참조 대상이 개정되면 검토 알림
- `sop_chunks` 표 + `vector` / `pg_search` 확장 — 임베딩 기반 의미 검색

둘 다 확장 설치(`CREATE EXTENSION vector`)와 임베딩 파이프라인이 필요해서
지금 구조에 영향을 주지 않습니다. 필요해지면 마이그레이션 한 장이면 됩니다.

---

## 부록 — 손으로 확인해 보기

```bash
psql "$DATABASE_URL"
```

```sql
\dt                          -- 표 목록
\d+ flow_nodes               -- 컬럼 + 인덱스 + COMMENT 까지
\di                          -- 인덱스 목록

-- 문서 하나의 버전 이력
SELECT version_no, revision, saved_by, saved_at, change_note
  FROM sop_versions WHERE document_id = '…' ORDER BY version_no DESC;

-- 현재 버전의 노드만
SELECT d.sop_no, n.node_key, n.node_type, n.action
  FROM flow_nodes n JOIN sop_documents d ON d.current_version_id = n.version_id
 ORDER BY d.sop_no, n.node_key;

-- 살아 있는 잠금
SELECT * FROM sop_edit_locks WHERE expires_at > now();

-- content 없이 크기만 보기 (얼마나 무거운지 감 잡기)
SELECT version_no, pg_size_pretty(pg_column_size(content)::bigint)
  FROM sop_versions ORDER BY pg_column_size(content) DESC LIMIT 10;

-- 인덱스가 실제로 안 쓰이는지 확인 (운영 DB에서 한동안 돌린 뒤)
SELECT relname, indexrelname, idx_scan
  FROM pg_stat_user_indexes ORDER BY idx_scan;
```

마지막 쿼리의 `idx_scan = 0` 이 위 4장 표의 ❌ 를 실제 데이터로 증명해 줍니다.
인덱스를 지울지 말지는 그 숫자를 보고 정하면 됩니다.
