# SQL 기초 — 이 프로젝트 코드를 읽기 위한 최소한

> 🧭 **"이 함수는 언제 불리고 어떻게 움직이나" 가 궁금하면 `docs/CODE_WALKTHROUGH.md`** — 저장 요청 한 통을 처음부터 끝까지 따라갑니다.

> 대상: SQL 을 거의 안 써 본 백엔드 담당자.
> 목표: `docs/DB_SCHEMA.md` 와 `app/` 안의 SQL 문장을 **혼자 읽을 수 있게** 되는 것.
> 문법 전체를 다루지 않습니다. **이 저장소에 실제로 나오는 것만** 다룹니다.

---

## 0. 큰 그림 — DB 는 "엑셀 파일"이다

PostgreSQL 을 **엑셀 파일 하나**라고 생각하세요.

| SQL 용어 | 엑셀로 치면 | 이 프로젝트 예 |
|---|---|---|
| 데이터베이스 (database) | 엑셀 파일 하나 | `sop` |
| 표 (table) | 시트 한 장 | `sop_documents` |
| 컬럼 (column) | 세로줄 (A열, B열…) | `sop_no`, `name` |
| 행 (row) | 가로줄 (1행, 2행…) | SOP 한 건 |
| 값 (value) | 칸 하나 | `"SOP-ETCH-001"` |

`sop_documents` 표를 눈으로 보면 이렇게 생겼습니다.

```
 id(uuid)  | sop_no        | name        | area | status   | current_version_id
-----------+---------------+-------------+------+----------+--------------------
 a1b2…     | SOP-ETCH-001  | 식각 기본    | E    | approved | 77ff…
 c3d4…     | SOP-ETCH-002  | 식각 응용    | E    | draft    | 88aa…
 e5f6…     | SOP-PHOTO-001 | 노광 준비    | P    | draft    | (비어 있음)
```

**SQL 은 이 표에 말을 거는 언어**입니다. 딱 4가지 말만 할 줄 알면 됩니다.

| 하고 싶은 말 | SQL 낱말 | 엑셀로 치면 |
|---|---|---|
| 찾아줘 | `SELECT` | 필터 걸고 보기 |
| 새로 넣어줘 | `INSERT` | 맨 아래에 줄 추가 |
| 고쳐줘 | `UPDATE` | 칸 값 수정 |
| 지워줘 | `DELETE` | 줄 삭제 |

이 저장소에서 쓰는 SQL 문장은 **전부 합쳐 40개 남짓**이고, 거의 다 `SELECT` 입니다.
(`grep -rn "SELECT\|INSERT\|UPDATE\|DELETE" app/` 로 직접 세어 볼 수 있습니다.)

---

## 1. SELECT — 찾기

### 1-1. 가장 단순한 형태

```sql
SELECT sop_no, name FROM sop_documents;
```

읽는 법은 **뒤에서 앞으로**입니다.

```
SELECT sop_no, name      ③ 그중 이 두 칸만 보여줘
  FROM sop_documents     ① 이 표에서
```

결과:

```
 sop_no        | name
---------------+-----------
 SOP-ETCH-001  | 식각 기본
 SOP-ETCH-002  | 식각 응용
 SOP-PHOTO-001 | 노광 준비
```

`SELECT *` 라고 쓰면 "모든 칸" 입니다. **이 프로젝트에서는 거의 안 씁니다.**
`content` 칸 하나가 수백 KB 라서, 실수로 딸려오면 목록 API 가 느려지기 때문입니다.
그래서 필요한 칸 이름을 항상 손으로 적습니다.

### 1-2. WHERE — 조건 걸기

```sql
SELECT sop_no, name FROM sop_documents WHERE area = 'E';
```

```
SELECT sop_no, name      ③ 이 칸들을
  FROM sop_documents     ① 이 표에서
 WHERE area = 'E'        ② AREA 가 E 인 줄만 골라
```

`WHERE` 에서 쓰는 비교들:

| 쓰는 법 | 뜻 | 이 저장소에서 |
|---|---|---|
| `=` | 같다 | `WHERE id = %s` |
| `<>` | 다르다 (`!=` 도 됨) | `WHERE d.id <> %s` (자기 자신 제외) |
| `>` `<` `>=` `<=` | 크다/작다 | `WHERE expires_at > now()` |
| `IS NULL` | 값이 비어 있다 | `WHERE ref_document_id IS NULL` |
| `IS NOT NULL` | 값이 있다 | 부분 인덱스 조건 |
| `AND` | 둘 다 | `WHERE a = 1 AND b = 2` |
| `OR` | 둘 중 하나 | 참조 조회에서 씀 |
| `IN (…)` | 목록 중 하나 | `WHERE status IN ('draft','review')` |
| `ILIKE` | 대소문자 무시 부분일치 | 이름 검색 |
| `= ANY(%s)` | 배열 중 하나 | 참조 문서 일괄 조회 |

> **NULL 주의.** `NULL` 은 "0" 도 "빈 글자" 도 아니고 **"값이 없음"** 입니다.
> 그래서 `WHERE x = NULL` 은 **절대 참이 안 됩니다.** 반드시 `IS NULL` 을 씁니다.
> 이 프로젝트에서 `ref_document_id IS NULL` = "번호만 적혀 있고 아직 연결 안 됨" 이라는
> 의미를 가지므로 자주 나옵니다.

### 1-3. `%s` 는 뭔가요? — 값 자리 표시

코드에서는 이렇게 생겼습니다.

```python
await conn.execute("SELECT id, sop_no FROM sop_documents WHERE id = %s", (doc_id,))
```

`%s` 는 **"여기에 값이 들어갈 자리"** 라는 표시입니다. 실제 값(`doc_id`)은
뒤의 괄호 `(doc_id,)` 로 따로 넘깁니다.

**왜 이렇게 나눠서 넘기나?** 글자를 직접 붙이면 큰일 나기 때문입니다.

```python
# ❌ 절대 이렇게 쓰지 마세요
await conn.execute(f"SELECT * FROM sop_documents WHERE sop_no = '{sop_no}'")
```

누가 `sop_no` 에 `' OR 1=1 --` 같은 걸 넣으면, 문장이 통째로 바뀌어
남의 데이터를 다 가져가거나 표를 지울 수도 있습니다. 이걸 **SQL 인젝션**이라고
합니다. `%s` 로 넘기면 드라이버가 "이건 값이다" 라고 구분해 주므로 안전합니다.

> `(doc_id,)` 의 **쉼표를 빠뜨리지 마세요.** 파이썬에서 `(x)` 는 그냥 괄호고
> `(x,)` 가 1개짜리 튜플입니다. 빠뜨리면 오류가 납니다.

### 1-4. ORDER BY — 정렬

```sql
SELECT sop_no, name FROM sop_documents ORDER BY area, sop_no;
```

`area` 로 먼저 정렬하고, 같으면 `sop_no` 로 정렬합니다.
**`ORDER BY` 를 안 쓰면 순서는 보장되지 않습니다** — 오늘 맞게 나와도
내일 바뀔 수 있습니다. 순서가 중요하면 반드시 적습니다.

`DESC` 를 붙이면 역순입니다.

```sql
ORDER BY version_no DESC     -- 최신 버전이 맨 위
```

### 1-5. 한 줄만 / 여러 줄 받기

파이썬 쪽 사용법입니다.

```python
cur = await conn.execute("SELECT id, sop_no FROM sop_documents WHERE id = %s", (doc_id,))

row  = await cur.fetchone()    # 한 줄만. 없으면 None
rows = await cur.fetchall()    # 전부. 없으면 빈 리스트 []
```

이 프로젝트는 `dict_row` 설정을 써서 결과가 **사전(dict)** 으로 옵니다.

```python
row["sop_no"]          # "SOP-ETCH-001"
row["current_version_id"]
```

`fetchone()` 이 `None` 이면 "그런 줄이 없다" 는 뜻이고, 보통 404 를 냅니다.

```python
if row is None:
    raise ApiError(404, "not_found", "문서를 찾을 수 없습니다.")
```

---

## 2. 계산해서 가져오기

### 2-1. COALESCE — "비어 있으면 대신 이 값"

```sql
COALESCE(v.revision, '')
```

`v.revision` 이 `NULL` 이면 `''`(빈 글자) 를 쓰라는 뜻입니다.

이 프로젝트에서 쓰는 이유: 아직 버전이 하나도 없는 문서는 버전 칸이 전부
`NULL` 로 나옵니다. 그대로 보내면 응답 모양이 깨지니 `''` 로 메웁니다.

```sql
COALESCE(MAX(version_no), 0)
```

버전이 하나도 없으면 `MAX` 는 `NULL` 인데, 여기에 `+1` 을 하면 `NULL` 입니다.
`0` 으로 메워야 첫 버전이 `1` 이 됩니다.

### 2-2. MAX / count — 모아서 하나로

```sql
SELECT MAX(version_no) FROM sop_versions WHERE document_id = %s;
```

여러 줄을 훑어서 **한 줄짜리 답**을 냅니다. 이런 함수를 **집계함수**라고 합니다.

```sql
SELECT count(DISTINCT d.id) AS total FROM …;
```

- `count(…)` = 개수 세기
- `DISTINCT` = 중복 빼고 (같은 문서가 여러 번 걸려도 1로 셈)
- `AS total` = 결과 칸 이름을 `total` 로 (파이썬에서 `row["total"]` 로 꺼냄)

### 2-3. GROUP BY — "묶어서 한 줄씩"

```sql
SELECT d.id, d.sop_no, …
  FROM flow_nodes n JOIN sop_documents d ON …
 GROUP BY d.id, d.sop_no, …
```

한 문서 안에 "SOP-ETCH-001 을 가리키는 상자" 가 3개 있으면 조인 결과가
3줄 나옵니다. 하지만 우리가 원하는 답은 **"이 문서가 참조한다"** 한 줄입니다.
`GROUP BY d.id` 로 문서별로 묶으면 1줄이 됩니다.

> `GROUP BY` 에는 **`SELECT` 에 쓴 칸을 전부 적어야 합니다** (집계함수 제외).
> 안 적으면 "이 칸은 어느 값을 보여줄지 모르겠다" 며 오류가 납니다.

### 2-4. now() 와 시간 계산

```sql
expires_at > now()                               -- 아직 안 지났나?
now() + make_interval(secs => %s)                -- 지금부터 N초 뒤
```

`now()` 는 현재 시각입니다. `make_interval(secs => 300)` 은 "5분" 이라는
기간이고, `=>` 는 **함수 인자를 이름으로 넘기는 표기**입니다
(파이썬의 `f(secs=300)` 과 같은 느낌).

---

## 3. JOIN — 표 두 개를 붙여 보기

여기가 처음 배울 때 제일 막히는 부분이니 천천히 갑니다.

### 3-1. 왜 필요한가

이 프로젝트는 문서 정보와 버전 정보가 **서로 다른 표**에 있습니다.

```
sop_documents                          sop_versions
 id     | sop_no       | current_version_id       id    | document_id | version_no | revision
--------+--------------+-------------------      ------+-------------+------------+---------
 a1b2…  | SOP-ETCH-001 | 77ff…              ─┐    77ff… | a1b2…       |     3      | 1.2
 c3d4…  | SOP-ETCH-002 | 88aa…               └──► 88aa… | c3d4…       |     1      | 1.0
```

"목록에 SOP 번호 **와** 현재 개정번호를 같이 보여줘" 는 두 표를 다 봐야 합니다.
**두 표를 붙여서 한 표처럼 보는 것**이 `JOIN` 입니다.

### 3-2. 기본형

```sql
SELECT d.sop_no, v.revision
  FROM sop_documents d
  JOIN sop_versions  v ON v.id = d.current_version_id;
```

- `sop_documents d` — 이 표를 앞으로 `d` 라고 부르겠다 (**별칭**, 타이핑 줄이기용)
- `JOIN sop_versions v` — `sop_versions` 표도 같이 보겠다, 별칭은 `v`
- `ON v.id = d.current_version_id` — **어떤 줄끼리 짝지을지**

`ON` 이 핵심입니다. "문서의 `current_version_id` 와 같은 `id` 를 가진 버전 줄을
옆에 붙여라" 라는 뜻입니다.

결과:

```
 sop_no        | revision
---------------+---------
 SOP-ETCH-001  | 1.2
 SOP-ETCH-002  | 1.0
```

### 3-3. LEFT JOIN — 짝이 없어도 버리지 않기

그냥 `JOIN` 은 **짝이 있는 줄만** 남깁니다. `SOP-PHOTO-001` 은 아직 버전이
없어서(`current_version_id` 가 비어 있음) **결과에서 사라집니다.**

목록 API 에서는 곤란합니다. 방금 만든 빈 문서도 트리에 보여야 하니까요.

```sql
  FROM sop_documents d
  LEFT JOIN sop_versions v ON v.id = d.current_version_id
```

`LEFT JOIN` 은 **왼쪽 표(`d`)는 무조건 다 남기고**, 짝이 없으면 오른쪽 칸을
`NULL` 로 채웁니다.

```
 sop_no        | revision
---------------+---------
 SOP-ETCH-001  | 1.2
 SOP-ETCH-002  | 1.0
 SOP-PHOTO-001 | (NULL)     ← 사라지지 않음
```

그래서 `COALESCE(v.revision, '')` 이 바로 옆에 따라붙는 것입니다.
**`LEFT JOIN` 을 봤으면 `NULL` 처리를 찾아보세요.** 항상 짝으로 다닙니다.

### 3-4. 이 프로젝트의 가장 중요한 JOIN

```sql
JOIN sop_documents d ON d.current_version_id = n.version_id
```

이 한 줄이 **"현재 버전의 노드만"** 을 만들어 냅니다.

`flow_nodes` 에는 v1, v2, v3 … 모든 버전의 노드가 다 들어 있습니다.
그냥 `SELECT * FROM flow_nodes` 하면 옛날 노드까지 다 나옵니다.
위 조인은 "문서가 **지금 최신이라고 가리키는 버전**의 노드" 만 통과시킵니다.

> 이 저장소에서 SQL 실수가 나올 확률이 제일 높은 지점입니다.
> `flow_nodes` 를 건드리는 새 쿼리를 쓸 때는 **항상** 이 조인이 필요한지 먼저 생각하세요.

---

## 4. INSERT / UPDATE / DELETE — 바꾸기

### 4-1. INSERT — 새 줄 넣기

```sql
INSERT INTO sop_documents (sop_no, name, area, created_by)
VALUES (%s, %s, %s, %s)
RETURNING id;
```

- 앞 괄호 = 채울 칸 이름
- `VALUES` 괄호 = 넣을 값 (순서가 **정확히** 맞아야 합니다)
- 안 적은 칸은 `DEFAULT` 값이 들어갑니다 (`id` 는 자동 생성, `created_at` 은 `now()`)

**`RETURNING` 이 편리합니다.** 방금 넣은 줄의 값을 바로 돌려받습니다.
없으면 "넣고 → 다시 SELECT" 두 번 왕복해야 하고, 그 사이 다른 요청이
끼어들 수도 있습니다.

```python
cur = await conn.execute("INSERT … RETURNING id", (…))
doc_id = (await cur.fetchone())["id"]      # 방금 만든 문서의 id
```

### 4-2. executemany — 같은 INSERT 를 여러 번

순서도 노드가 50개면 INSERT 를 50번 해야 합니다. 한 번씩 보내면
네트워크 왕복이 50번이라 느립니다.

```python
async with conn.cursor() as cur:
    await cur.executemany(
        "INSERT INTO flow_nodes (version_id, instance_id, …) VALUES (%s, %s, …)",
        node_params,          # [(값들…), (값들…), (값들…), …]
    )
```

문장은 하나, 값 묶음만 여러 개를 한 번에 보냅니다.
(`executemany` 는 커서(cursor)를 통해서만 쓸 수 있어서 `conn.cursor()` 를
잠깐 만들어 쓰는 것입니다.)

### 4-3. UPDATE — 고치기

```sql
UPDATE sop_documents
   SET current_version_id = %s, name = %s, updated_at = now()
 WHERE id = %s;
```

> ⚠️ **`WHERE` 를 빠뜨리면 표의 모든 줄이 바뀝니다.** 되돌릴 수 없습니다.
> `UPDATE` 나 `DELETE` 를 쓸 때는 `WHERE` 를 먼저 쓰는 습관을 들이세요.

### 4-4. DELETE — 지우기

```sql
DELETE FROM sop_edit_locks WHERE document_id = %s AND locked_by = %s;
```

이 프로젝트에서 `DELETE` 는 **잠금 표에서만** 씁니다.
문서·버전은 절대 지우지 않고 `status = 'retired'` 로 표시만 합니다
(SOP 는 이력 보존이 원칙).

위 문장에 `AND locked_by = %s` 가 붙은 덕분에 **남의 잠금은 지워지지 않습니다.**
남의 잠금이면 "지워진 줄 0개" 로 조용히 끝나고 오류도 안 납니다.

---

## 5. 트랜잭션 — "전부 되거나, 전부 안 되거나"

문서를 저장하려면 DB 작업이 여러 번 필요합니다.

```
1) sop_versions 에 새 버전 줄 넣기
2) flow_nodes 에 노드 50줄 넣기
3) flow_edges 에 연결선 40줄 넣기
4) sop_documents 의 현재 버전 포인터 옮기기
```

3번에서 오류가 나면? 1·2번은 이미 들어갔는데 4번은 안 됐습니다.
**반쯤 저장된 쓰레기 데이터**가 남습니다.

트랜잭션은 이걸 막습니다. **"묶음 안의 작업은 전부 성공하거나, 전부 취소된다."**

```python
async with conn.transaction():
    # 이 안에서 오류(raise)가 나면 여기까지 한 일이 전부 자동 취소(롤백)됩니다
    ...
```

SQL 로 직접 쓰면 이렇게 생겼습니다.

```sql
BEGIN;      -- 묶음 시작
  …
COMMIT;     -- 확정  (오류 시 ROLLBACK = 전부 취소)
```

이 프로젝트는 `async with conn.transaction():` 블록이 알아서 해 줍니다.
블록을 정상적으로 빠져나가면 `COMMIT`, 예외가 나면 `ROLLBACK` 입니다.

> 그래서 `_append_version()` 주석에 **"반드시 트랜잭션 안에서 불러야 합니다"** 라고
> 적혀 있습니다. 중간에 409 오류를 던지면 그때까지 넣은 줄이 전부 사라져야 하니까요.

---

## 6. FOR UPDATE — 동시에 저장할 때 줄 세우기

### 6-1. 무슨 문제가 생기나

두 사람이 **같은 문서를 동시에** 저장한다고 해 봅시다.

```
사람 A: 지금 최신 버전이 몇 번? → 3
사람 B: 지금 최신 버전이 몇 번? → 3      (A 가 아직 안 넣었으니 똑같이 3)
사람 A: 그럼 4번으로 넣자        → 성공
사람 B: 그럼 4번으로 넣자        → 💥 이미 4번이 있음 → 오류
```

### 6-2. 해결

```sql
SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE;
```

`FOR UPDATE` 를 붙이면 **그 줄을 잠급니다.**
다른 요청이 같은 줄을 `FOR UPDATE` 로 읽으려 하면, 앞사람의 트랜잭션이
끝날 때까지 **기다립니다.**

```
사람 A: 문서 줄 잠금 획득 → 3 확인 → 4번 넣음 → COMMIT (잠금 해제)
사람 B: (기다림…………………………………………) → 4 확인 → 5번 넣음 → COMMIT
```

한 명씩 순서대로 통과하니 충돌이 안 납니다.

### 6-3. 꼭 기억할 함정

**`FOR UPDATE` 는 "없는 줄" 을 잠글 수 없습니다.**

잠금을 처음 거는 문서는 `sop_edit_locks` 에 아직 줄이 없습니다.
그 표를 `FOR UPDATE` 로 읽어도 잠글 대상이 없어서 **두 사람 다 그냥 통과**하고,
둘 다 `INSERT` 로 가서 늦은 쪽이 중복 오류를 냅니다.

그래서 `app/routers/locks.py` 는 이렇게 합니다.

```sql
-- ① 항상 존재하는 "문서 줄" 을 먼저 잠근다 ← 이게 진짜 줄 세우기
SELECT id FROM sop_documents WHERE id = %s FOR UPDATE;
-- ② 그 다음에 잠금 줄을 본다 (여기까지 한 명씩만 옴)
SELECT locked_by, expires_at, (expires_at > now()) AS is_active
  FROM sop_edit_locks WHERE document_id = %s FOR UPDATE;
```

> **규칙: 없을 수도 있는 줄을 잠그려면, 항상 있는 부모 줄을 먼저 잠근다.**
> PostgreSQL 을 쓰면 계속 만나는 패턴이니 통째로 외워 두세요.

---

## 7. 표를 만들 때 쓰는 것들 (CREATE TABLE 읽기)

`sql/sop_schema.sql` 을 읽으려면 이것들을 알아야 합니다.

### 7-1. 자료형

| 타입 | 뜻 | 예 |
|---|---|---|
| `text` | 글자 (길이 제한 없음) | `'SOP-ETCH-001'` |
| `int` | 정수 | `3` |
| `timestamptz` | 시각 + 시간대 | `2026-09-21 10:00:00+09` |
| `uuid` | 36글자짜리 임의 식별자 | `a1b2c3d4-…` |
| `jsonb` | JSON 통째 (검색 가능한 형태로 저장) | `{"x":120,"y":90}` |
| `text[]` | 글자 목록(배열) | `{태그1,태그2}` |

> `varchar(50)` 같은 길이 제한을 안 쓴 이유: PostgreSQL 에서는 `text` 와
> 성능 차이가 없고, 길이를 늘리려면 나중에 `ALTER` 가 필요해지기 때문입니다.
>
> `timestamp` 가 아니라 `timestamptz` 인 이유: 시간대가 없으면 "10시" 가
> 한국 10시인지 UTC 10시인지 알 수 없습니다. 항상 `timestamptz` 를 쓰세요.
>
> `json` 이 아니라 `jsonb` 인 이유: `json` 은 글자 그대로 저장이라 볼 때마다
> 파싱해야 하고, `jsonb` 는 미리 분해해 저장해서 조회가 빠릅니다.

### 7-2. 제약 (constraint) — DB 가 직접 지키는 규칙

```sql
id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
sop_no  text NOT NULL UNIQUE,
area    text NOT NULL DEFAULT '' CHECK (area IN ('', 'P','E','D','T','C')),
```

| 제약 | 뜻 | 어기면 |
|---|---|---|
| `PRIMARY KEY` | 이 표의 대표 식별자. 유일 + 비어 있을 수 없음 | 오류 |
| `NOT NULL` | 비워 둘 수 없음 | 오류 |
| `UNIQUE` | 같은 값이 두 줄에 있을 수 없음 | 오류 (`UniqueViolation`) |
| `DEFAULT x` | 안 적고 넣으면 `x` 가 들어감 | — |
| `CHECK (…)` | 조건을 만족하는 값만 허용 | 오류 |

**왜 파이썬에서 검사하면 안 되고 DB 에도 거나?**
파이썬 검사는 **우리 API 를 통해 들어올 때만** 작동합니다.
psql 로 직접 고치거나, 마이그레이션 스크립트를 돌리거나, 나중에 다른 프로그램이
같은 DB 를 쓰면 그냥 통과합니다. DB 제약은 **어떤 경로로 들어와도** 막습니다.

### 7-3. 복합 UNIQUE

```sql
UNIQUE (document_id, version_no)
```

"칸 하나씩" 이 아니라 **"두 칸의 조합"** 이 유일하다는 뜻입니다.
- 문서 A 의 버전 1 ✅
- 문서 B 의 버전 1 ✅ (문서가 다르니 OK)
- 문서 A 의 버전 1 두 번 ❌

```sql
UNIQUE (version_id, instance_id, node_key)
```

3칸 조합입니다. 한 문서에 순서도가 여러 장일 수 있어서 `node_key`("seq_1") 만으로는
부족하고, 어느 순서도(`instance_id`)의 것인지까지 함께 봐야 유일해집니다.

### 7-4. 외래키 (FOREIGN KEY) — 표끼리 연결

```sql
document_id uuid NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE
```

"이 칸의 값은 반드시 `sop_documents` 에 존재하는 `id` 여야 한다" 는 규칙입니다.
없는 문서 id 를 넣으려 하면 DB 가 거부합니다. 덕분에 **가리키는 곳이 없는
유령 데이터가 생기지 않습니다.**

`ON DELETE` 는 **가리키던 상대가 지워졌을 때 어떻게 할지**입니다.

| 설정 | 동작 | 이 프로젝트에서 |
|---|---|---|
| `ON DELETE CASCADE` | 나도 같이 지워짐 | 문서 삭제 시 버전·노드·연결선·잠금도 정리 |
| `ON DELETE SET NULL` | 내 칸만 비워짐(나는 살아남음) | `ref_document_id`, `current_version_id` |

`ref_document_id` 가 `SET NULL` 인 이유가 중요합니다.
참조하던 SOP 가 사라져도 **내 순서도 상자는 지워지면 안 됩니다.**
연결만 끊기고, `ref_sop_no`(번호 글자)는 남아서 "미작성 참조" 상태가 됩니다.
나중에 그 번호로 문서를 다시 만들면 자동으로 재연결됩니다.

---

## 8. 인덱스 — "책 뒤의 찾아보기"

### 8-1. 없으면 어떻게 되나

```sql
SELECT * FROM sop_documents WHERE sop_no = 'SOP-ETCH-001';
```

인덱스가 없으면 DB 는 **줄을 처음부터 끝까지 하나씩 봅니다.** 1만 건이면 1만 번.
책에서 단어를 찾으려고 1쪽부터 넘기는 것과 같습니다.

**인덱스는 책 뒤의 "찾아보기"** 입니다. 미리 정렬해 둔 목록이 있으니
바로 몇 쪽인지 알 수 있습니다.

```sql
CREATE INDEX ix_documents_area_no ON sop_documents (area, sop_no);
```

### 8-2. 사물함 비유 — "가져오기" 와 "찾기" 는 다른 일

인덱스를 헷갈리지 않는 가장 쉬운 방법입니다.

DB 를 **번호가 붙은 사물함 수천 개**라고 생각하세요.
사물함 안에는 가방이 들어 있고, 가방 안에는 온갖 물건이 들었습니다.

- 사물함 번호 = `id`
- 가방 = `content` (편집기 JSON 통째)

**우리 코드가 하는 일:**

> "**77번** 사물함 열어서 가방 꺼내 줘"

번호를 알고 있으니 바로 갑니다. **가방을 열어보지도 않습니다.** 통째로 들고 옵니다.

**"content 로 검색한다" 는 이런 겁니다:**

> "**가방 안에 빨간 우산이 든** 사물함이 몇 번인지 찾아줘"

전혀 다른 일입니다. 사물함을 하나하나 다 열어서 가방 속을 뒤져야 합니다.

**인덱스는** 이런 부탁이 자주 올까 봐 **미리 모든 가방을 열어 내용물 목록을 만들어 둔 것**입니다.

```
빨간 우산  → 3번, 77번, 512번 사물함
검은 신발  → 12번, 88번 사물함
```

목록이 있으면 "빨간 우산" 부탁이 순식간에 끝납니다.
**하지만 목록을 계속 최신으로 유지해야 합니다** — 가방을 하나 넣을 때마다
다 풀어헤쳐서 물건을 전부 적어 넣어야 합니다.

### 8-3. 코드에서 알아보는 법 — `WHERE` 뒤만 보세요

```sql
SELECT id, version_no, content FROM sop_versions WHERE id = %s;
                       ↑                               ↑
                  꺼내올 것 (가져오기)          찾는 조건 (찾기) ← 여기만 보면 됨
```

| 자리 | 하는 일 | 인덱스가 도움 되나 |
|---|---|---|
| `SELECT` 뒤 | 이미 고른 줄에서 **그 칸을 꺼내기** | ❌ 무관 |
| `WHERE` 뒤 | 수많은 줄 중에 **찾아내기** | ✅ 바로 이걸 위한 것 |

**인덱스는 오직 `WHERE` 를 돕는 물건입니다.**

- `WHERE` 에 `id` 가 있다 → `id` 인덱스가 일함 ✅
- `WHERE` 에 `content` 가 없다 → `content` 인덱스는 놀고 있음 ❌

이 저장소에서 `content` 가 나오는 SQL 은 3개뿐이고, **`WHERE` 뒤에는 한 번도 안 나옵니다.**

```bash
grep -rn "content" app/routers/*.py | grep -i "select\|where\|insert"
# sops.py:82      SELECT id, version_no, content FROM sop_versions WHERE id = %s
# sops.py:250     INSERT INTO sop_versions (…, content, …)
# versions.py:59  SELECT id, version_no, content FROM sop_versions WHERE document_id = %s AND version_no = %s
```

전부 "꺼내오기" 이거나 "넣기" 입니다. 찾는 조건은 `id` 나 `(document_id, version_no)` 입니다.

**인덱스가 쓰였을 SQL 은 이런 모양이어야 합니다** (우리 코드엔 없음):

```sql
WHERE content @> '{"studio":{"area":"E"}}'      -- 이 내용을 포함하나?
WHERE content ? 'blocks'                        -- 이 키가 있나?
WHERE content->'sop'->>'name' = '식각 기본'      -- 이 값이 같나?
```

`@>` `?` `->>` 같은 jsonb 연산자가 **`WHERE` 에** 나와야 "content 로 검색" 입니다.

### 8-4. 공짜가 아니다

**인덱스는 읽기를 빠르게 하는 대신 쓰기를 느리게 합니다.**
줄을 하나 넣을 때마다 인덱스도 전부 갱신해야 하니까요.

이 프로젝트는 저장 한 번에 노드를 수십~수백 줄 넣습니다. `flow_nodes` 에
인덱스가 7개 있으면 그 갱신이 7번씩 따라붙습니다.

그래서 `docs/DB_SCHEMA.md` 4장에서 **실제로 쓰이는 인덱스와 안 쓰이는 인덱스를
구분해 둔 것**입니다. 안 쓰이는 인덱스는 순수 비용입니다.

### 8-5. 종류

```sql
CREATE INDEX ix_documents_name_trgm ON sop_documents USING gin (name gin_trgm_ops);
```

- `USING gin` — 일반(B-tree) 대신 **GIN** 방식. 한 칸 안에 여러 조각이 든 것
  (JSON, 배열, 글자 조각)을 찾을 때 씁니다.
- `gin_trgm_ops` — 글자를 3글자씩 쪼개 색인. `ILIKE '%식각%'` 같은
  **부분 일치 검색**을 빠르게 합니다. (`pg_trgm` 확장 필요)

```sql
CREATE INDEX ix_flow_nodes_ref_doc ON flow_nodes (ref_document_id)
    WHERE ref_document_id IS NOT NULL;
```

- 뒤에 `WHERE` 가 붙은 것을 **부분 인덱스**라고 합니다.
- 조건에 맞는 줄만 색인에 담습니다. 노드 대부분은 SOP 참조가 아니라
  `ref_document_id` 가 비어 있으므로, 인덱스가 훨씬 작고 빨라집니다.
- **좋은 인덱스 설계의 예입니다.**

---

## 9. 실전 — 이 저장소의 SQL 3개를 한 줄씩 해부

### 9-1. 문서 목록 (`app/routers/sops.py:152`)

```sql
SELECT d.id, d.sop_no, d.name, d.area, d.status, d.updated_at,
       v.version_no,
       COALESCE(v.revision, '') AS revision,
       COALESCE(v.owner,   '') AS owner
  FROM sop_documents d
  LEFT JOIN sop_versions v ON v.id = d.current_version_id
 WHERE status <> 'retired'
 ORDER BY d.area, d.sop_no
```

| 줄 | 하는 일 | 왜 |
|---|---|---|
| `SELECT d.…` | 문서 칸들 | `content` 는 일부러 뺌 (수백 KB) |
| `v.version_no` | 현재 버전 번호 | 목록에 "v3" 표시용 |
| `COALESCE(…, '')` | NULL 을 빈 글자로 | 버전 없는 문서 대비 |
| `FROM … d` | 문서 표가 기준 | |
| `LEFT JOIN … v` | 버전 표를 옆에 붙임 | 버전 없는 문서도 남겨야 하므로 `LEFT` |
| `ON v.id = d.current_version_id` | 현재 버전 줄과 짝지음 | 옛 버전이 딸려오지 않게 |
| `WHERE status <> 'retired'` | 폐기 문서 제외 | 트리에 안 보이게 |
| `ORDER BY d.area, d.sop_no` | AREA → 번호 순 | `ix_documents_area_no` 인덱스가 도움 |

### 9-2. 새 버전 번호 구하기 (`sops.py:209, 227`)

```sql
-- ① 문서 줄을 잠근다
SELECT id, sop_no, status FROM sop_documents WHERE id = %s FOR UPDATE;

-- ② 그 아래에서 최대 버전 번호를 구한다
SELECT COALESCE(MAX(version_no), 0) AS max_no FROM sop_versions WHERE document_id = %s;
```

**왜 두 문장으로 나뉘어 있나?**
PostgreSQL 은 `MAX()` 같은 집계함수와 `FOR UPDATE` 를 한 문장에 같이 쓸 수 없습니다.
그래서 "잠그기" 와 "세기" 를 나눴습니다. 순서가 중요합니다 —
①로 줄을 세운 뒤에 ②를 해야 두 사람이 같은 번호를 읽지 않습니다.

`COALESCE(…, 0)` 이 없으면 첫 저장 때 `NULL + 1 = NULL` 이 됩니다.

### 9-3. 이 문서를 참조하는 다른 문서 (`app/refs.py:153`)

```sql
SELECT d.id, d.sop_no, d.name, d.area, d.status, v.version_no
  FROM flow_nodes n
  JOIN sop_documents d ON d.current_version_id = n.version_id
  JOIN sop_versions  v ON v.id = d.current_version_id
 WHERE (n.ref_document_id = %s OR (n.ref_document_id IS NULL AND n.ref_sop_no = %s))
   AND d.id <> %s
 GROUP BY d.id, d.sop_no, d.name, d.area, d.status, v.version_no
 ORDER BY d.area, d.sop_no
```

한 줄씩:

| 줄 | 하는 일 |
|---|---|
| `FROM flow_nodes n` | **노드(순서도 상자)가 기준.** 참조 정보는 상자에 들어 있으니까 |
| `JOIN sop_documents d ON d.current_version_id = n.version_id` | ★ **현재 버전의 상자만** 통과. 옛 버전 상자 배제 |
| `JOIN sop_versions v ON …` | 그 문서의 버전 번호를 같이 얻으려고 |
| `n.ref_document_id = %s` | **연결된 참조** — id 로 나를 가리킴 |
| `OR (… IS NULL AND n.ref_sop_no = %s)` | **미작성 참조** — id 는 없고 번호 글자만 내 번호와 같음 |
| `AND d.id <> %s` | 자기 자신은 뺌 (자기를 가리키는 상자도 허용되므로) |
| `GROUP BY d.id, …` | 한 문서가 나를 3번 가리켜도 1줄로 |
| `ORDER BY d.area, d.sop_no` | 보기 좋은 순서 |

**`OR` 이 있는 이유를 이해하면 이 프로젝트의 참조 설계를 이해한 것입니다.**
참조에는 두 종류가 있습니다 — 문서가 실제로 있어서 id 로 이어진 것,
그리고 번호만 적어 뒀는데 그 문서가 아직 없는 것(미작성). 둘 다 "참조" 로 세야
"이걸 폐기하면 3건이 영향받습니다" 같은 경고를 제대로 낼 수 있습니다.

---

## 10. 직접 해 보기

DB 에 붙어서 위 내용을 눈으로 확인해 보세요. 읽기만 하는 명령이라 안전합니다.

```bash
psql "$DATABASE_URL"
```

```sql
\dt                       -- 표 목록
\d  sop_documents         -- 이 표의 칸과 제약
\d+ flow_nodes            -- 인덱스와 주석까지
\di                       -- 인덱스 목록
\q                        -- 나가기
```

```sql
-- 문서 3건만 보기
SELECT sop_no, name, area, status FROM sop_documents ORDER BY area, sop_no LIMIT 3;

-- 버전이 몇 개씩 있나
SELECT d.sop_no, count(v.id) AS 버전수
  FROM sop_documents d LEFT JOIN sop_versions v ON v.document_id = d.id
 GROUP BY d.sop_no ORDER BY d.sop_no;

-- JOIN 을 뺐을 때 vs 넣었을 때 (3-4 절 확인)
SELECT count(*) FROM flow_nodes;                                    -- 모든 버전의 노드
SELECT count(*) FROM flow_nodes n
  JOIN sop_documents d ON d.current_version_id = n.version_id;      -- 현재 버전만
```

마지막 두 숫자가 다르다는 것을 보면, `docs/DB_SCHEMA.md` 가 계속 강조하는
"현재 버전 조인" 이 왜 필요한지 몸으로 알게 됩니다.

---

## 다음 읽을 것

- `docs/DB_SCHEMA.md` — 이 스키마가 **왜** 이렇게 생겼는지 (표별 상세, 동작별 흐름)
- `docs/BACKEND_GUIDE.md` — API·라우터·FastAPI 쪽 설명
- `sql/sop_schema.sql` — 원본. 이제 한 줄씩 읽힐 것입니다
