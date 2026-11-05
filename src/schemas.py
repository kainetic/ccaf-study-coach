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
    claim: str = Field(description="답변 속 사실 주장 하나 (한국어)")
    doc_id: str = Field(description="이 주장의 근거가 된 검색 결과의 doc_id 그대로. 예: TS-4.3, SQ-01, SCOPE-out, OV-details")
    excerpt: str = Field(description="그 검색 결과 원문에서 그대로 옮긴 짧은 구간 (영문, 번역·요약 금지)")


class TutorAnswer(BaseModel):
    """concept_tutor 구조화 출력 (§5-1). G3: 유효한 claims가 1개 이상이어야 답변으로 쓴다 (src/guards.py)."""
    answer_ko: str = Field(description="사용자에게 보일 한국어 답변 본문. 핵심 용어 영어 병기. [근거: …] 표기는 쓰지 않는다(코드가 붙임)")
    claims: list[Claim] = Field(description="답변의 핵심 주장과 근거. 검색 결과에서 확인한 것만. 근거가 없으면 빈 목록")
    in_exam_scope: Literal["in", "out", "unclear"] = Field(description="질문 주제가 시험 범위 안(in)·밖(out)·불명확(unclear)")
    coverage_gap: Optional[str] = Field(default=None, description="질문 중 가이드에 근거가 없는 부분 (없으면 null). 예: 합격률 수치")


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

SlotStatus = Literal["pending", "approved", "rejected", "duplicate", "needs_fix"]   # needs_fix: 사람 검토 "오류" (§5-2)


class ManifestSlot(BaseModel):
    """생성 슬롯 하나. quiz_id가 배치의 custom_id 역할 (TS 4.5)."""
    quiz_id: str
    slot_type: Literal["base", "extra", "fill", "exception"]   # exception: 항목당 2슬롯 규칙의 명시적 예외 (§5-2)
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


# ---------------------------------------------------------------- 풀이 기록 (§3-2, §8)

class StudyAttempt(BaseModel):
    """submit_answer 한 번. 채점은 코드로 하고 이 기록과 한 도구 안에서 저장한다 (§5-4)."""
    quiz_id: str
    task_id: str
    domain: int
    focus_bullet_ids: list[str]
    choice: Label
    correct: bool
    answered_at: str


class QuizIssued(BaseModel):
    """get_quiz 출제 한 번 (같은 문제 반복 출제를 줄이는 데 씀)."""
    quiz_id: str
    issued_at: str


class UserStudyLog(BaseModel):
    attempts: list[StudyAttempt] = Field(default_factory=list)
    issued: list[QuizIssued] = Field(default_factory=list)


class StudyLog(BaseModel):
    """data/study_log.json. user_id는 Store namespace 규칙에 맞춰 영문·숫자·_·-만 (점 금지)."""
    version: int = 1
    users: dict[str, UserStudyLog] = Field(default_factory=dict)


# ---------------------------------------------------------------- 가드레일 (§6)

class InputCheck(BaseModel):
    """입력 가드 2차 LLM 판별 (G1 덤프 요청 + G2 프롬프트 인젝션을 한 번에)."""
    category: Literal["none", "exam_dump", "injection"] = Field(
        description="exam_dump: 실제 시험 문항·덤프·기출 요청 / injection: 지시 무시·역할 탈취·시스템 프롬프트 요구 / none: 그 외 전부")
    confidence: float = Field(ge=0, le=1, description="판단 확신도")
    reason: str = Field(description="판단 근거 한 문장")


class GuardDecision(BaseModel):
    """input_guard 결과. block = 고정 거절 문구, correct = 고정 정정 문구(G4), pass = 그래프 실행."""
    action: Literal["pass", "block", "correct"]
    rule: Optional[Literal["G1", "G2", "G4"]] = None
    layer: Optional[Literal["rule", "llm"]] = None
    reason: str = ""


# ---------------------------------------------------------------- 학습 계획 (§5-5)

class DomainAllocation(BaseModel):
    domain: int
    name: str
    weight: float                       # 시험 비중
    attempts: int
    accuracy: Optional[float]           # 시도 0회면 None
    factor: float                       # 약점 계수 (출제와 같은 식: 3회 이상 max(0.2, 1-정답률), 미만 1.0)
    hours: float


class StudyAllocation(BaseModel):
    """allocate_study_time 결과. 합계는 항상 total_hours와 같다 (코드 계산, LLM 계산 금지)."""
    days: int
    hours_per_day: float
    total_hours: float
    unit: float                         # 배분 단위 (0.5h)
    formula: str
    domains: list[DomainAllocation]


class PlanItem(BaseModel):
    task_id: str = Field(description="공부할 TS 번호. 예: TS-2.1")
    hours: float = Field(description="이 TS에 쓸 시간 (0.5 단위)")
    focus: str = Field(description="무엇을 공부할지 한 줄 (한국어)")


class DayPlanDraft(BaseModel):
    day: int = Field(description="1부터 시작하는 일차")
    items: list[PlanItem]


class StudyPlanDraft(BaseModel):
    """make_study_plan planner(LLM) 출력. 합계 검증·보정은 코드가 한다."""
    days: list[DayPlanDraft]


# ---------------------------------------------------------------- 평가 (§9-1)

class JudgeItem(BaseModel):
    index: int = Field(description="항목 번호 (1부터)")
    ok: bool = Field(description="traits: 충족했으면 true / forbidden: 위반하지 않았으면 true")
    reason: str = Field(description="판정 근거 한 문장 (한국어)")


class JudgeVerdict(BaseModel):
    traits: list[JudgeItem] = Field(description="expected_traits 항목별 판정, 번호 순서대로 전부")
    forbidden: list[JudgeItem] = Field(description="forbidden 항목별 판정, 번호 순서대로 전부")
