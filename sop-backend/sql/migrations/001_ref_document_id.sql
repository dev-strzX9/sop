-- =====================================================================
--  마이그레이션 001 — flow_nodes 에 ref_document_id(참조 문서 id) 칸 추가
--
--  "마이그레이션" 이란? 이미 데이터가 들어 있는 DB 의 표 모양을 조금 바꾸는 작업입니다.
--  새 설치라면 sql/sop_schema.sql 에 이미 이 칸이 들어 있으니 이 파일은 필요 없습니다.
--
--  이 파일은 "멱등" 입니다 = 몇 번을 실행해도 결과가 같고 오류가 나지 않습니다.
--  (IF NOT EXISTS 덕분. 이미 있으면 그냥 넘어갑니다)
--
--  실행:  python -m app.tools.apply_schema          (도구가 이름순으로 자동 실행)
--    또는 psql "$DATABASE_URL" -f sql/migrations/001_ref_document_id.sql
-- =====================================================================

-- 1) 칸 추가. sop 노드가 가리키는 SOP 의 문서 id. NULL 이면 "번호만 적혀 있고 아직 연결 안 됨".
--    대상 문서 행이 지워지면(ON DELETE SET NULL) 자동으로 NULL 이 되어 참조가 깨진 채 남지 않습니다.
ALTER TABLE flow_nodes
    ADD COLUMN IF NOT EXISTS ref_document_id uuid REFERENCES sop_documents(id) ON DELETE SET NULL;

-- 2) "이 문서를 참조하는 SOP" 를 빨리 찾기 위한 인덱스. 값이 있는 행만 담는 부분 인덱스라 작고 빠릅니다.
CREATE INDEX IF NOT EXISTS ix_flow_nodes_ref_doc
    ON flow_nodes (ref_document_id) WHERE ref_document_id IS NOT NULL;

-- 3) 칸 설명(주석)을 DB 안에도 남겨 둡니다. (psql 의 \d+ flow_nodes 로 볼 수 있음)
COMMENT ON COLUMN flow_nodes.ref_document_id IS 'sop 노드가 참조하는 SOP 의 문서 id (진짜 연결). NULL = 번호만 있고 미연결';
