"""데이터 구조 (DESIGN §2, §4)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

DocType = Literal[
    "overview", "domain", "scenario", "task_statement",
    "sample_question", "exercise", "scope",
]
Label = Literal["A", "B", "C", "D"]
LABELS: tuple[str, ...] = ("A", "B", "C", "D")

ReviewPattern = Literal[
    "answer_not_supported", "multiple_correct", "distractor_obviously_wrong",
    "off_focus_bullet", "out_of_scope", "scenario_mismatch", "other",
]


# ---------------------------------------------------------------- 코퍼스 (§2)

class GuideChunk(BaseModel):
    """검색용 청크 (62개). Chroma 컬렉션 ccaf_guide에 적재."""
    doc_id: str                 # "TS-1.4"
    doc_type: DocType
    domain: Optional[int]       # 1~5, 해당 없으면 None
    title: str
    page: Optional[int]
    guide_version: str = "0.2"
    text: str


class GuideBullet(BaseModel):
    """TS의 Knowledge of / Skills in 불릿 하나 (240개). 문제 생성 슬롯·커버리지용, 검색 인덱스에는 넣지 않음."""
    bullet_id: str              # "TS-1.4-K2", "TS-1.4-S3"
    task_id: str                # "TS-1.4"
    domain: int
    kind: Literal["knowledge", "skill"]
    text: str


# ---------------------------------------------------------------- 답변 (§4)

class Claim(BaseModel):
    """TS 5.6 claim-source 매핑."""
    claim: str
    doc_id: str                 # "TS-4.3"
    excerpt: str                # 근거 원문 일부 (영문 그대로)


class TutorAnswer(BaseModel):
    answer_ko: str
    claims: list[Claim]
    in_exam_scope: Literal["in", "out", "unclear"]
    coverage_gap: Optional[str] = None


# ---------------------------------------------------------------- 문제 (§4, §5-2)

class QuizOption(BaseModel):
    label: Label
    text: str = Field(description="Option text in English.")
    rationale: str = Field(
        description="Why this option is correct or incorrect, in Korean with key terms in English. "
                    "Self-contained: never refer to other options by letter.")


class QuizDraft(BaseModel):
    """생성 LLM의 출력 형식. 검증 규칙은 두지 않는다 — 형식 오류는 quiz_gen.validate_draft가
    재시도 피드백 문장으로 바꾼다 (DESIGN §5-2)."""
    stem: str = Field(description="Scenario-based question stem in English, ending with the question.")
    options: list[QuizOption] = Field(description="Exactly 4 options labeled A, B, C, D.")
    answer: Label = Field(description="Label of the single correct option.")
    source_excerpt: str = Field(
        description="Verbatim span copied from the focus item text that supports the correct answer.")


class QuizOptionsRepair(BaseModel):
    """길이 규칙만 실패한 문항의 보기 수정 결과 (DESIGN §5-2 repair). stem·answer·source_excerpt는 원본을 유지한다."""
    options: list[QuizOption] = Field(
        description="All 4 options with the same labels; the option with the correct label stays correct.")


class QuizItem(BaseModel):
    """문제 은행 저장 형식 (정답 포함). 런타임에는 get_quiz가 QuizPublic으로 바꿔 내보낸다."""
    quiz_id: str                        # "Q-1.4-K2" / "Q-1.4-X1" / "Q-1.4-K2-F"
    task_id: str
    domain: int
    focus_bullet_ids: list[str]
    scenario_id: int                    # 1~6
    stem: str
    options: list[QuizOption]
    answer: Label
    source_excerpt: str
    # 운영 메타데이터
    status: Literal["approved", "rejected", "duplicate", "needs_fix"]
    review_retries: int = Field(ge=0, le=2)
    detected_patterns: list[str]
    human_checked: bool = False
    generator_model: str
    guide_version: str = "0.2"
    created_at: str

    @model_validator(mode="after")
    def _four_options_one_answer(self):
        labels = [o.label for o in self.options]
        if len(labels) != 4 or sorted(labels) != list(LABELS):
            raise ValueError(f"options must be exactly A, B, C, D once each, got {labels}")
        if self.answer not in labels:
            raise ValueError(f"answer {self.answer} not in option labels")
        return self


class QuizPublic(BaseModel):
    """LLM과 사용자에게 내보내는 형식 (정답·해설 없음)."""
    quiz_id: str
    task_id: str
    scenario_id: int
    stem: str
    options: list[dict]                 # label, text만


class ReviewVerdict(BaseModel):
    """독립 검토 결과 (TS 4.6)."""
    valid: bool = Field(description="True only if the item has no problem at all.")
    issues: list[str] = Field(
        description="Concrete problems in Korean, citing bullet ids and option content. Empty if valid.")
    detected_pattern: Optional[ReviewPattern] = Field(
        default=None, description="The single most severe failure pattern, or null if valid.")

    @field_validator("detected_pattern", mode="before")
    @classmethod
    def _null_string(cls, v):
        # Bedrock tool 호출에서 null이 문자열 "null"로 오는 경우가 있음
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none"):
            return None
        return v


class ScenarioFit(BaseModel):
    """--fill-gaps에서 시나리오 불일치로 탈락한 세부 항목의 시나리오 적합도 순위 (DESIGN §5-2)."""
    ranking: list[int] = Field(description="Candidate scenario numbers ordered from best to worst fit.")
    reason: str = Field(description="One sentence in Korean explaining why the first scenario fits best.")


class GradeResult(BaseModel):
    quiz_id: str
    choice: str
    correct: bool
    answer: str
    option_rationales: dict[str, str]
    task_id: str


# ---------------------------------------------------------------- 매니페스트 (§5-2)

SlotStatus = Literal["pending", "approved", "rejected", "duplicate"]


class ManifestSlot(BaseModel):
    """생성 슬롯 하나. quiz_id가 배치의 custom_id 역할 (TS 4.5)."""
    quiz_id: str
    slot_type: Literal["base", "extra", "fill"]
    task_id: str
    domain: int
    focus_bullet_ids: list[str]
    scenario_id: int
    scenario_candidates: list[int]
    target_answer: Label                # 보기 섞기 후 정답이 놓일 위치 (균등 배정)
    order: int                          # 생성·중복 판정 순서: 기본 → 추가 → 보충
    pilot: bool = False
    status: SlotStatus = "pending"
    attempts: int = 0                   # 생성 호출 수 (0~3). review_retries = attempts - 1
    repairs: int = 0                    # 길이 repair 호출 수 (attempts에 포함하지 않음)
    repairs_ok: int = 0                 # repair 후 코드 검증을 통과한 수
    generator_model: Optional[str] = None   # 최종 문항을 만든 생성 호출의 실제 모델 (모델 전환 시 기본과 다를 수 있음)
    reviewer_model: Optional[str] = None    # 승인 또는 마지막 탈락 판정을 낸 검토 호출의 모델
    detected_patterns: list[str] = Field(default_factory=list)
    last_errors: list[str] = Field(default_factory=list)
    prompt_version: Optional[str] = None
    updated_at: Optional[str] = None


class QuizManifest(BaseModel):
    manifest_version: int = 1
    guide_version: str
    generator_model: str
    created_at: str
    updated_at: str
    summary: dict[str, int] = Field(default_factory=dict)
    slots: list[ManifestSlot]
