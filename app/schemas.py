"""
스키마(schemas) — API가 주고받는 JSON의 "모양"을 정의하는 곳.

pydantic 모델은 "이 요청에는 이런 필드가 이런 타입으로 와야 한다"는 설명서입니다.
FastAPI가 요청 JSON을 받으면 이 모델에 맞는지 자동으로 검사하고, 틀리면 422 오류를 냅니다.
그래서 API 함수 안에서는 "필드가 있나? 숫자인가?" 같은 검사를 따로 하지 않아도 됩니다.

주의: 편집기 문서 JSON(doc) 자체는 `dict` 로 받습니다.
      프론트가 계속 진화하므로 서버는 그 안의 필드를 하나하나 정의하지 않고 통째로 보관합니다.
      (필요한 몇 개 필드만 derive.py 에서 골라 씁니다)

시각(datetime) 은 전부 UTC 로, 끝에 Z 를 붙여 내보냅니다 (예: 2026-09-11T02:30:00.123456Z).
  DB 세션의 timezone 설정이 무엇이든(회사 DB 가 KST 여도) 응답은 항상 같은 모양이 되게 하려는 것입니다.
  아래 UtcDatetime 타입을 datetime 자리에 쓰면 자동으로 그렇게 됩니다.

이 파일 읽는 법 (pydantic 표기)
  이름: 타입 = 기본값            → 그 필드는 있어도 되고 없어도 됨 (없으면 기본값)
  이름: 타입                    → 반드시 있어야 함
  int | None                    → 정수이거나 비어 있음(null)
  Field(default_factory=list)   → 안 보내면 빈 목록 [] 로 시작 (= [] 라고 쓰면 모든 객체가 목록 하나를 공유해서 이렇게 씀)
  Field(default=120, ge=10, le=3600) → 기본 120, 허용 범위 10 이상 3600 이하 (밖이면 422)
  dict[str, Any]                → 아무 모양의 JSON 객체 (내용은 검사하지 않음)
"""

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer


def to_utc_z(value: datetime) -> str:
    """datetime 을 "UTC 기준 ISO 8601, 끝에 Z" 문자열로 바꿉니다. 시간대 정보가 없으면 UTC 로 간주합니다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# datetime 대신 이 타입을 쓰면 JSON 으로 내보낼 때 to_utc_z 가 자동 적용됩니다.
UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_z, return_type=str, when_used="json")]


# ---------------------------------------------------------------------
# 문서 목록 / 열기
# ---------------------------------------------------------------------
class DocumentSummary(BaseModel):
    """목록(라이브러리 트리) 한 줄. content 는 절대 넣지 않습니다 (문서당 수백 KB)."""

    id: UUID
    sop_no: str
    name: str
    area: str
    status: str
    version_no: int | None = None   # 최신 버전 번호
    revision: str = ""              # 편집기에 입력한 개정 번호 (예: 1.0)
    owner: str = ""
    updated_at: UtcDatetime


class LockInfo(BaseModel):
    """누가 언제까지 편집 잠금을 잡고 있는지."""

    locked_by: str
    expires_at: UtcDatetime


class RefDoc(BaseModel):
    """
    문서를 열 때 함께 주는 "이 문서가 참조하는 SOP" 한 건의 현재 정보.
    순서도의 sop 상자에 저장된 ref_document_id 가 가리키는 문서의 지금 번호·이름·상태입니다 (폐기된 것도 포함).
    편집기는 이걸로 상자에 최신 번호를 그리고, 폐기된 문서를 가리키면 표시해 줍니다.
    """

    id: UUID
    sop_no: str
    name: str
    area: str
    status: str


class DocumentOpen(BaseModel):
    """문서 열기 응답. content 가 편집기에 그대로 넘겨줄 JSON 원본입니다."""

    id: UUID
    sop_no: str
    version_no: int
    version_id: UUID
    lock: LockInfo | None = None    # 다른 사람이 잠갔으면 정보, 아니면 null
    content: dict[str, Any]
    ref_docs: list[RefDoc] = Field(default_factory=list)   # 이 버전의 sop 상자들이 가리키는 문서들의 현재 정보


# ---------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------
class SaveRequest(BaseModel):
    """POST /api/sops (새 문서) 와 PUT /api/sops/{doc_id} (새 버전) 의 요청 본문. 모양은 같습니다."""

    doc: dict[str, Any]                       # 편집기가 만든 문서 JSON 통째
    base_version_no: int | None = None        # 내가 열었던 버전 번호. 다른 사람이 먼저 저장했는지 검사용. null이면 검사 생략 (POST 는 무시)
    change_note: str = ""                     # 무엇을 바꿨는지 메모
    saved_by: str = ""                        # 비우면 사용자 헤더(X-User) 값을 씁니다


class RefsSummary(BaseModel):
    """저장할 때 순서도의 sop 상자(다른 SOP 참조)를 어떻게 처리했는지 개수로 요약한 것."""

    linked: int = 0    # 연결됨: 문서 id 로 실제 문서와 이어진 상자
    pending: int = 0   # 미작성: 번호만 적혀 있고 라이브러리에 그 번호의 문서가 아직 없는 상자
    empty: int = 0     # 비어 있음: 번호도 id 도 없는 상자


class SaveResponse(BaseModel):
    """저장 성공(201) 응답."""

    id: UUID
    sop_no: str
    version_id: UUID
    version_no: int
    saved_at: UtcDatetime
    warnings: list[str] = Field(default_factory=list)   # 저장은 됐지만 알려 둘 것 (건너뛴 노드, 끊긴 참조 등)
    warning: str | None = None                          # 다른 사람이 잠금 중이면 그 안내 문구
    refs: RefsSummary = Field(default_factory=RefsSummary)   # sop 상자 참조 처리 결과 (연결됨/미작성/비어 있음 개수)


# ---------------------------------------------------------------------
# 번호 변경 / 참조 관계
# ---------------------------------------------------------------------
class RenameRequest(BaseModel):
    """PATCH /api/sops/{doc_id}/number 요청 본문. SOP 번호만 바꿉니다 (버전은 손대지 않음)."""

    sop_no: str                # 새 번호
    user: str = ""             # 비우면 사용자 헤더(X-User) 값. 다른 사람이 잠금 중이면 423.


class RenameResponse(BaseModel):
    """번호 변경 성공(200) 응답."""

    id: UUID
    sop_no: str                # 바뀐(새) 번호
    old_sop_no: str            # 이전 번호
    referenced_by: int         # 이 문서를 참조하는 다른 문서 수 (id 로 연결된 것은 자동으로 새 번호를 보게 됨)


class ReferencedBy(BaseModel):
    """GET /api/sops/{doc_id}/referenced-by 응답 한 줄 — "이 문서를 참조하는 다른 문서"."""

    id: UUID
    sop_no: str
    name: str
    area: str
    status: str
    version_no: int            # 참조가 들어 있는 그 문서의 현재 버전 번호


# ---------------------------------------------------------------------
# 버전
# ---------------------------------------------------------------------
class VersionSummary(BaseModel):
    """버전 목록 한 줄. content 제외."""

    version_no: int
    revision: str
    saved_by: str
    saved_at: UtcDatetime
    change_note: str


# ---------------------------------------------------------------------
# 편집 잠금
# ---------------------------------------------------------------------
class LockRequest(BaseModel):
    """POST /api/sops/{doc_id}/lock 요청 본문."""

    user: str = ""                                    # 비우면 X-User 헤더 값
    ttl_sec: int = Field(default=120, ge=10, le=3600)  # 잠금 유지 시간(초). 하트비트로 계속 연장


class UnlockRequest(BaseModel):
    """DELETE /api/sops/{doc_id}/lock 요청 본문."""

    user: str = ""


# ---------------------------------------------------------------------
# 자동 임시 저장 (draft)
# ---------------------------------------------------------------------
class DraftRequest(BaseModel):
    """PUT /api/sops/{doc_id}/draft 요청 본문."""

    user: str = ""
    content: dict[str, Any]


class DraftResponse(BaseModel):
    document_id: UUID
    user_id: str
    content: dict[str, Any]
    updated_at: UtcDatetime


# ---------------------------------------------------------------------
# 기타
# ---------------------------------------------------------------------
class StatusResponse(BaseModel):
    """폐기/복구/잠금 해제처럼 "됐다" 만 알려 주면 되는 응답."""

    id: UUID
    status: str
    referenced_by: int | None = None   # 폐기(DELETE) 때만: 이 문서를 참조하는 다른 문서 수. 그 외 응답에서는 null


class HealthResponse(BaseModel):
    ok: bool
    db: str   # "up" 또는 "down"
