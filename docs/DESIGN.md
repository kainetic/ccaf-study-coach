# DESIGN · CCA-F 학습 코치 (설계 v0.9, 최종)

> 기준 문서: Claude Certified Architect – Foundations Exam Guide (Version 0.2, 2026-06-30)
> 과제 규약: 교육 과정 mini PJT 안내 문서(과정 내부 자료) §4 산출물 규약, §5 12개 패턴. 본문의 "Day N `xxx.py` 재사용"은 같은 과정의 N일차 실습 코드를 가져왔다는 뜻
> 최종 구현 기준의 설계 문서입니다. SERVICE.md가 "왜/누구를", 이 문서는 "어떻게"를 다룹니다. 단계 라벨(S0~S6, v2-n)은 §1-1과 변경 이력 참조

---

## 0. 시험 가이드에서 설계로 가져온 것 (요약)

| 가이드 내용 | 설계 반영 |
|---|---|
| 5개 도메인, 30개 Task Statement(TS 1.1~5.6) | **검색 청킹 단위 = TS 1개**. `domain`, `task_id`가 검색 필터, 출처 표기, 약점 추적의 공통 키 |
| 각 TS의 Knowledge of / Skills in 세부 항목 (총 240개) | **문제 생성 단위 = 세부 항목 1개**. 문제마다 어느 항목을 묻는지 지정해 중복 방지, 커버리지 측정 |
| 도메인 비중 27/18/20/20/15 (D1 Agentic Architecture · D2 Tool Design & MCP · D3 Claude Code · D4 Prompt Eng. & Structured Output · D5 Context & Reliability) | 추가 30문제 배분, **런타임 출제 빈도**, 학습 시간 배분, 약점 점수 가중치 |
| 6개 시나리오와 각 시나리오의 primary domains | 문제마다 시나리오를 배정하되, 해당 도메인이 primary domain인 시나리오만 사용 |
| 문항 형식: 4지선다, 정답 1 + 그럴듯한 오답 3 | 퀴즈 Pydantic 스키마(보기 A~D, 정답 1개, 보기별 해설) |
| 샘플 문항 12개(정답·해설 포함) | 생성 few-shot 예시(형식·난이도 기준), 해설 질문의 근거 |
| Preparation Exercises 4개 | 학습 계획의 실습 과제 항목으로 연결 |
| In-scope / Out-of-scope 목록 | negative·edge 응답 근거 ("이건 시험 범위 밖") |
| TS 내용 자체 (hook 강제, 구조화 에러, 독립 검토, 배치 처리, 층화 표본, claim-source 매핑 등) | **이 앱의 구현 원칙으로도 적용** (§10, README "활용한 패턴") |

---

## 1. 범위

### 1-1. 단계 (Stage)

모든 단계 완료. 제출(S6) 뒤 개선은 v2-1~v2-7로 부른다(변경 이력).

| 단계 | 범위 | 완료 결과 |
|---|---|---|
| **S0** 기획·코퍼스 | SERVICE.md · `evaluation/test_queries.csv`·`ragas_reference.csv` 확정 · `src/ingest.py`(청크 62개 + 세부 항목 240개 → Chroma) | ingest 검증 통과(62/240, 도메인별 K/S 개수) |
| **S1** 문제 풀 구축 | `quiz_gen.py`·프롬프트 · 파일럿 · 전체 생성 · 보충 · 사람 표본 검토 · `quiz_pool_report.md` · `seed_study_log.py` (§5-2) | 승인 266, 커버리지 240/240, 사람 표본 오류 0건, `study_log.json` 시드 |
| **S2** 핵심 한 바퀴 | 도구 `search_guide`·`get_quiz`(MCP)·`submit_answer`(MCP) · Supervisor + concept_tutor + quiz_master · `/query` (answer, contexts, trace) · `evaluation/smoke_p0.py` · RAGAS 3건 스모크 | "개념 질문 → 출제 → 채점"이 `/query` 3회로 동작, 채점 결과가 `study_log.json`에 기록 |
| **S3** 기능 완성 | 가드레일 G1~G7 · `get_chunk` · `get_progress`·`get_weak_domains` · study_coach(`allocate_study_time`, `make_study_plan`) · 평가 러너(`run_eval.py`, `llm_judge.py`) | 20건 평가 실행 가능 |
| **S4** round1 평가 | 20건 평가 + RAGAS → `evaluation/round1_report.md` | 15/20 |
| **S5** round2 개선 | §9-4 후보 중 #2·#4 → 같은 세트 재평가 → `evaluation/round2_report.md` | 19/20 |
| **S6** 제출 정리 | README("활용한 패턴" 포함, §10) · 시연 캡처 | 제출 |
| 선택 항목 | `reset_progress` HITL · 장기 세션 요약 · 웹 UI · Dockerfile · LangSmith/LangFuse | 요약(v2-3)·웹 UI(v2-5)만 구현 |

### 1-2. 하지 않는 것
목록은 SERVICE §3 "이번 과제에서 하지 않는 것"을 기준으로 한다. 설계에 주는 영향은 두 가지다. 런타임에 문제를 만들지 않으므로 풀이 부족한 범위는 오프라인 배치로 보충한다(§5-2). LangSmith/LangFuse 대신 로컬 trace를 쓴다(§7).

---

## 2. 코퍼스 설계

### 2-1. 청크 구성 (62개, 검색용)

| doc_type | 개수 | doc_id 예시 | 비고 |
|---|---|---|---|
| `overview` | 2 | `OV-details`, `OV-candidate` | 시험 형식, 대상 응시자 |
| `domain` | 5 | `D1` ~ `D5` | 도메인 이름·비중·소속 TS 목록 |
| `scenario` | 6 | `SC-1` ~ `SC-6` | 시나리오 설명 + primary domains |
| `task_statement` | 30 | `TS-1.1` ~ `TS-5.6` | Knowledge of + Skills in 전체 |
| `sample_question` | 12 | `SQ-01` ~ `SQ-12` | 문항 + 정답 + 해설 |
| `exercise` | 4 | `EX-1` ~ `EX-4` | 실습 단계 + 강화 도메인 |
| `scope` | 3 | `SCOPE-tech`, `SCOPE-in`, `SCOPE-out` | Appendix |

```python
class GuideChunk(BaseModel):
    doc_id: str                 # "TS-1.4"
    doc_type: Literal["overview","domain","scenario","task_statement",
                      "sample_question","exercise","scope"]
    domain: Optional[int]       # 1~5, 해당 없으면 None
    title: str
    page: Optional[int]
    guide_version: str = "0.2"
    text: str
```

### 2-2. 세부 항목 (240개, 문제 생성용)

각 TS의 "Knowledge of"와 "Skills in" 아래 불릿을 하나씩 분리. 검색 인덱스에는 넣지 않고 문제 생성 슬롯과 커버리지 측정에만 사용.

| 도메인 | TS 수 | Knowledge | Skills | 합계 |
|---|---|---|---|---|
| D1 | 7 | 24 | 24 | 48 |
| D2 | 5 | 20 | 23 | 43 |
| D3 | 6 | 23 | 26 | 49 |
| D4 | 6 | 22 | 25 | 47 |
| D5 | 6 | 24 | 29 | 53 |
| 합계 | 30 | 113 | 127 | **240** |

(TS당 6~10개. 가장 적은 TS 1.1은 6개, 가장 많은 TS 4.3은 10개. ingest 후 개수가 240과 다르면 파싱 오류로 보고 멈출 것)

```python
class GuideBullet(BaseModel):
    bullet_id: str              # "TS-1.4-K2", "TS-1.4-S3" (K=Knowledge, S=Skills, 순번)
    task_id: str
    domain: int
    kind: Literal["knowledge", "skill"]
    text: str
```

### 2-3. 청킹 방식
- PDF 텍스트를 헤더 패턴(`Task Statement x.y:`, `Scenario n:`, `Question n:`, `Exercise n:`)으로 **구조 분할**. TS 하나가 300~500 토큰이라 고정 길이 분할은 쓰지 않음.
- 페이지 머리말("Claude Certification Program  Exam guide") 제거. 페이지 경계에서 끊긴 불릿은 다음 줄과 이어 붙임.
- 산출물: `data/guide_chunks.jsonl`(62), `data/guide_bullets.jsonl`(240) → 청크만 Chroma 컬렉션 `ccaf_guide` 적재 (Titan v2 임베딩).

### 2-4. 저장 정책 (private repo)
- 가이드 원문과 그 파생물(`data/raw/ccaf_exam_guide.pdf`, `guide_chunks.jsonl`, `guide_bullets.jsonl`, `chroma_db/`, `evaluation/review_sample.xlsx`)은 **커밋하지 않음** (`.gitignore`). 비공개 저장소여도 collaborator가 클론하면 원문이 재배포되기 때문. 모두 ingest(xlsx는 `review_sample.py`)로 다시 만들 수 있고, PDF 배치 위치와 ingest 실행법은 `data/README.md`에 있다.
- **문제 풀 `data/quiz_pool.json`은 커밋함**: 직접 생성한 원본 문제이고 근거 인용은 짧은 발췌만 포함. 평가 재현성 확보 목적.

### 2-5. 다국어 검색 이슈 (중요)
코퍼스는 영어, 질문은 한국어라 한국어 질의 그대로는 BM25로 영어 문서를 거의 찾지 못한다.
- 질의를 영어 키워드로 재작성하는 `rewrite_query` 후 BM25(영어 토크나이저) + 벡터 하이브리드 (Day 2 `hybrid.py`, `extra_multiquery.py` 재사용, §5-1)
- `task_id`가 질문에 명시되면("TS 2.2", "도메인 3") 그 청크를 정확 조회해 앞에 둠

---

## 3. 아키텍처

```mermaid
flowchart TD
    subgraph OFFLINE[오프라인: 문제 풀 구축]
        B0[(guide_bullets 240개)] --> B1[build_quiz_pool.py<br/>매니페스트]
        B1 --> B2[생성 → 코드 검증 → 독립 검토 → 재시도]
        B2 --> B3[후처리: 보기 섞기 · 중복 제거]
        B3 --> B4[사람 표본 검토 · 커버리지 보충]
        B4 --> QP[(quiz_pool.json 승인 266문제)]
    end
    U[POST /query] --> G{입력 가드<br/>규칙 + LLM}
    G -- 차단 --> R[고정 거절 문구]
    G -- 통과 --> S[Supervisor / coordinator]
    S -->|개념·해설| T[concept_tutor]
    S -->|출제·채점| Q[quiz_master]
    S -->|진도·약점·계획| C[study_coach]
    S -->|인사·기능 안내·무관한 주제| GA[general_agent]
    T --> V[(Chroma: ccaf_guide)]
    Q --> M[[MCP: quiz-bank server]]
    M --> QP
    M --> SL[(study_log.json)]
    C --> M
    C --> P[plan_and_execute 서브그래프]
    S --> O{output_check<br/>출처·금지표현}
    G -. LLM 판별은 그래프와 병렬, 차단 시 취소 .-> S
    O --> A[answer + contexts + trace]
```

- LangGraph `create_supervisor` (Day 6) 기반. Supervisor는 질문 유형에 따라 **필요한 에이전트만** 호출 (TS 1.2).
- 하위 에이전트가 끝나면 Supervisor로 돌아가지 않고 턴을 끝냄(`agent.after_agent`, §7). 복합 질문(개념 + 문제)만 Supervisor로 돌아가 두 에이전트를 순서대로 호출하고 각 답변을 이어 붙임 (§7 `turn_answer`, 별도 종합 단계 없음).
- handoff 시 필요한 정보(task_id, 도메인, 시나리오)를 명시적으로 전달 (TS 1.3). 채점할 quiz_id는 핸드오프가 아니라 그래프 State의 `pending_quiz_id`로 넘김.
- 문제 생성 함수(`quiz_gen.py`)는 오프라인 스크립트에서만 호출하고 에이전트 도구로 노출하지 않음. 런타임에는 문제를 만들지 않음.

### 3-1. 에이전트와 도구 (에이전트당 도구 4개 이하, TS 2.3)

| 에이전트 | 역할 | 도구 (단계) | 출력 |
|---|---|---|---|
| `concept_tutor` | 개념 설명, 샘플 문항 해설, 범위 안내 | `search_guide(query, domain?, doc_type?)`, `get_chunk(doc_id)` | 답변 + claim-source 목록 |
| `quiz_master` | 출제, 채점 | `get_quiz` (MCP), `submit_answer` (MCP) | 문제(정답 제외) / 채점 결과 |
| `study_coach` | 진도, 약점, 학습 계획 | `get_progress`, `get_weak_domains` (MCP), `allocate_study_time`, `make_study_plan` | 진도 요약 / 학습 계획 |
| `general_agent` | 인사, 기능 안내, 무관한 주제 안내(G5). Day 6 `general_agent` 재사용 | 없음 | 짧은 안내 |

`search_guide`는 질문에 TS 번호 등이 있으면 그 청크를 정확 조회해 결과 앞에 둔다. `get_chunk(doc_id)`는 모델이 doc_id를 이미 알 때나 검색 결과의 전문을 확인할 때 쓰는 별도 도구다.

도구 설명은 "언제 쓰고 언제 쓰지 않는지, 입력 형식, 예시 질의"를 포함해 작성 (TS 2.1).
- `get_quiz`: "문제 요청 시 사용. 도메인·TS·시나리오로 필터. 풀에 없는 조합이면 도구가 그 사실을 밝히고 대체 문제를 낸다." (대체 문제는 조건을 하나씩 뺀 조합에서 고르므로 도메인이 달라질 수 있음) (no_match 처리는 래퍼 코드, §5-3)
- `submit_answer`: "사용자가 직전 문제의 답을 말했을 때만 사용. quiz_id는 대화 상태의 pending_quiz_id를 사용."

**quiz_master 도구 = MCP 래퍼 (`src/agents/quiz_master.py`)**
- LLM에 보이는 인자: `get_quiz(domain?, task_id?, scenario_id?)`, `submit_answer(choice)`. **user_id는 실행 config(`configurable.user_id`), quiz_id는 그래프 상태 `pending_quiz_id`에서 래퍼가 주입**하므로 LLM이 고를 수 없음. MCP 서버 시그니처(§3-2)는 그대로
- 래퍼는 `ToolRuntime`으로 state·config를 읽고 `Command(update={"pending_quiz_id": …})`로 상태를 갱신 (출제 시 설정, 채점 후 None)
- **두 도구 모두 `return_direct`**: 출제 문항(지문·보기 원문 그대로 + 시나리오 이름)과 채점 결과(정오 + 풀의 보기별 해설 + `[근거: TS-x.y]`)를 **코드가 서식화**해 그대로 답변으로 씀. LLM이 문항을 고쳐 쓰거나 채점 뒤에 덧붙인 말로 결과를 덮어쓰는 문제를 막음
- MCP 클라이언트는 앱 수명 동안 stdio 세션 1개를 열어 두고 `session.call_tool()`로 호출 (`src/quiz_client.py`. `get_tools()`는 호출마다 서버 프로세스를 새로 띄움)

**study_coach 도구 (`src/agents/study_coach.py`)**
- `get_progress()`, `get_weak_domains(top_k)`: MCP 래퍼, user_id는 config에서 주입
- `allocate_study_time(days, hours_per_day)`: LLM에게는 두 인자만 보임. 비중(config)·정답률(study_log, MCP get_progress)은 래퍼가 코드로 가져와 계산 → 정답률을 지어낼 수 없음 (§5-5)
- `make_study_plan(days, hours_per_day)`: **return_direct**. Plan-Execute 서브그래프 결과를 코드가 서식화해 그대로 답변으로 씀
  - `create_agent`는 한 차례에 실행한 도구가 **전부** return_direct일 때만 끝나므로, 모델이 세 도구를 한 번에 부르면 return_direct가 걸리지 않는다. before_model 미들웨어 `PlanDirectReturn`이 이번 턴 `make_study_plan` 결과를 LLM 없이 마지막 답변으로 두고 끝냄

### 3-2. MCP 서버: `quiz_bank_server.py` (fastmcp, Day 4 구조 재사용)

| 도구 | 입력 | 출력 |
|---|---|---|
| `get_quiz` | user_id, domain?, task_id?, scenario_id? | 정답·해설을 **제외한** 문제(`QuizPublic`) 또는 `no_match` |
| `submit_answer` | user_id, quiz_id, choice | 채점 결과 + 보기별 해설. **채점과 기록 저장을 한 번에 수행** |
| `get_progress` | user_id | 도메인별 시도 수·정답률, 세부 항목 커버리지(푼 항목 수 / 240), TS별 시도·정답·남은 승인 문제 수. 기록 없는 사용자도 정상 결과(0건) |
| `get_weak_domains` | user_id, top_k=2 | 약점 점수 순 도메인 (점수 = 비중 × (1 − 정답률), 시도 3회 미만 도메인은 "데이터 부족") |

- 저장소: `data/quiz_pool.json`(문제 은행, 읽기 전용), `data/study_log.json`(풀이 기록).
- MCP resource `quiz://catalog`: 도메인·TS·시나리오별 승인 문제 수 (탐색용 도구 호출 감소, TS 2.4)
- 에러는 구조화해서 반환 (TS 2.2):
  ```json
  {"isError": true, "errorCategory": "validation", "isRetryable": false,
   "message": "알 수 없는 quiz_id: Q-9.9-K1"}
  ```
- 조건에 맞는 문제가 없는 경우는 에러가 아니라 정상 결과 `{"status": "no_match", "reason": "...", "alternatives": [...]}`로 구분 (TS 2.2, 5.3). `alternatives`에는 조건을 하나 완화했을 때 가능한 조합(예: 같은 TS의 다른 시나리오)을 넣음.

---

## 4. 주요 스키마

```python
class Claim(BaseModel):                 # TS 5.6 claim-source 매핑
    claim: str
    doc_id: str                         # "TS-4.3"
    excerpt: str                        # 근거 원문 일부 (영문 그대로)

class TutorAnswer(BaseModel):
    answer_ko: str
    claims: list[Claim]
    in_exam_scope: Literal["in", "out", "unclear"]   # TS 4.3 "unclear" enum 패턴
    coverage_gap: Optional[str] = None  # 가이드에 근거가 없을 때 무엇이 없는지

class QuizOption(BaseModel):
    label: Literal["A","B","C","D"]
    text: str
    rationale: str                      # 왜 정답/오답인지

class QuizItem(BaseModel):              # 문제 은행 저장 형식 (정답 포함)
    quiz_id: str                        # "Q-1.4-K2" (기본 슬롯) / "Q-1.4-X1" (추가 슬롯). 배치의 custom_id 역할
    task_id: str
    domain: int
    focus_bullet_ids: list[str]         # 기본 슬롯 1개, 추가 슬롯 2개 (같은 TS 내 두 항목 결합)
    scenario_id: int                    # 1~6
    stem: str
    options: list[QuizOption]           # 정확히 4개 (validator로 강제)
    answer: Literal["A","B","C","D"]
    source_excerpt: str
    # 운영 메타데이터
    status: Literal["approved", "rejected", "duplicate", "needs_fix"]
    review_retries: int                 # 0~2
    detected_patterns: list[str]        # 검토 실패 유형 기록 (TS 4.4)
    human_checked: bool = False
    generator_model: str
    guide_version: str = "0.2"
    created_at: str

class QuizPublic(BaseModel):            # LLM과 사용자에게 내보내는 형식 (정답 없음)
    quiz_id: str
    task_id: str
    scenario_id: int
    stem: str
    options: list[dict]                 # label, text만

class ReviewVerdict(BaseModel):         # 독립 검토 결과
    valid: bool
    issues: list[str]                   # "정답 근거가 TS-1.4-K2에 없음", "B도 정답으로 해석 가능" 등
    detected_pattern: Optional[Literal[
        "answer_not_supported", "multiple_correct", "distractor_obviously_wrong",
        "off_focus_bullet", "out_of_scope", "scenario_mismatch", "other"]]

class GradeResult(BaseModel):
    quiz_id: str
    choice: str
    correct: bool
    answer: str
    option_rationales: dict[str, str]
    task_id: str

# ---- 풀이 기록 data/study_log.json (§3-2) ----
class StudyAttempt(BaseModel):          # submit_answer 한 번 (채점과 같은 도구 안에서 저장)
    quiz_id: str; task_id: str; domain: int; focus_bullet_ids: list[str]
    choice: Literal["A","B","C","D"]; correct: bool; answered_at: str
class QuizIssued(BaseModel):            # get_quiz 출제 한 번 (반복 출제 줄이기)
    quiz_id: str; issued_at: str
class UserStudyLog(BaseModel):
    attempts: list[StudyAttempt] = []; issued: list[QuizIssued] = []
class StudyLog(BaseModel):              # {"version": 1, "users": {user_id: UserStudyLog}}. user_id는 영문·숫자·_·-만 (점 금지)
    version: int = 1; users: dict[str, UserStudyLog] = {}

# ---- 그래프 상태 (src/agent.py) ----
class CoachState(AgentState):           # Supervisor·하위 에이전트 공용. 정답은 두지 않음
    pending_quiz_id: NotRequired[Optional[str]]   # get_quiz 래퍼가 설정, submit_answer 래퍼가 비움 (Command)
    case_facts: NotRequired[dict]                 # 요약에 묻히면 안 되는 사실 (memory.extract_facts)

# ---- 오프라인 풀 구축용 (§5-2) ----
class QuizDraft(BaseModel):             # 생성 LLM 출력. validator 없음 → 형식 오류는 코드 검증이 재시도 피드백으로 변환
    stem: str
    options: list[QuizOption]
    answer: Literal["A","B","C","D"]
    source_excerpt: str                 # 초점 세부 항목 원문의 연속 구간 (코드로 확인)

class QuizOptionsRepair(BaseModel):     # 길이 repair 출력. options만 받아 원본 draft에 끼움 (stem·answer·source_excerpt 유지)
    options: list[QuizOption]

class ManifestSlot(BaseModel):          # data/quiz_pool_manifest.json의 슬롯 하나
    quiz_id: str
    slot_type: Literal["base", "extra", "fill", "exception"]   # exception: 항목당 2슬롯 규칙의 명시적 예외
    task_id: str
    domain: int
    focus_bullet_ids: list[str]
    scenario_id: int                    # scenario_mismatch 재시도 시 다음 후보로 갱신
    scenario_candidates: list[int]      # 도메인 배정 시나리오 목록
    target_answer: Literal["A","B","C","D"]   # 보기 섞기 후 정답 위치 (균등 배정)
    order: int                          # 생성·중복 판정 순서: 기본 → 추가 → 보충
    pilot: bool = False
    status: Literal["pending", "approved", "rejected", "duplicate", "needs_fix"] = "pending"   # needs_fix: 사람 검토 "오류"
    attempts: int = 0                   # 생성 호출 수 0~3 (QuizItem.review_retries = attempts - 1)
    repairs: int = 0                    # 길이 repair 호출 수 (attempts에 포함하지 않음)
    repairs_ok: int = 0                 # repair 후 코드 검증을 통과한 수
    generator_model: Optional[str]      # 최종 문항을 만든 생성 호출의 실제 모델 (모델 전환, §5-2). 이 필드를 도입하기 전에 처리된 슬롯(220개)은 None (문항 쪽 QuizItem.generator_model에는 모두 기록됨)
    reviewer_model: Optional[str]       # 승인 또는 마지막 탈락 판정을 낸 검토 호출의 모델
    detected_patterns: list[str] = []   # 시도별 실패 유형 (parse, code, 검토 패턴, duplicate_of:Q-…)
    last_errors: list[str] = []         # 마지막 실패의 구체적 오류
    prompt_version: Optional[str]       # src/prompts 파일들의 sha1 앞 8자리
    updated_at: Optional[str]

class QuizManifest(BaseModel):
    manifest_version: int = 1
    guide_version: str
    generator_model: str
    created_at: str
    updated_at: str
    summary: dict[str, int]             # 상태별 슬롯 수 (저장할 때마다 재계산)
    slots: list[ManifestSlot]
```

`QuizItem` validator: options 정확히 4개, 라벨 {A,B,C,D} 각 1회, answer ∈ 라벨, review_retries 0~2.

`CoachState` 보충:
- `pending_quiz_id`는 checkpointer(thread_id)에 저장돼 다음 턴 채점에 쓰임. **정답은 State에 두지 않음**
- `case_facts`(TS 5.1): `memory.extract_facts`가 사용자 입력에서 정규식으로 남은 일수(`days`)·하루 공부 시간(`hours_per_day`)·관심 도메인(`domains`)·TS(`task_ids`)를 뽑아 둔다(새 값이 덮어씀)
- 장기 기억은 Store를 쓰지 않고 사용자별 풀이 기록(`study_log.json`, MCP 경유)으로 대신함. 시험 예정일·선호 언어 저장은 미구현

---

## 5. 핵심 흐름

### 5-1. 개념 Q&A
1. `rewrite_query`: 한국어 질문 → 영어 키워드 질의 (+ TS 번호 감지). 턴 시작 때 선행 실행
2. `search_guide` 하이브리드 검색 k=4. 첫 검색은 질문 원문으로 코드가 실행하고, 모델은 필요할 때만 추가 검색(도메인·문서 유형 필터, get_chunk)
3. `TutorAnswer` 구조화 출력 → G3 검증. 유효한 claim이 없으면 답을 지어내지 않고 `coverage_gap`으로 "가이드에 없음" 안내
4. 답변 끝에 출처 표기: `[근거: TS-1.4, SQ-01]`

구현 (`src/retriever.py`, `src/agents/concept_tutor.py`, `src/guards.check_tutor_answer`, `src/memory.py`):
- **검색**: 정확 조회는 정규식(`TS 2.2`·`Task Statement 2.2`·`도메인 3`·`Domain 3`·`D3`·`샘플 1번`·`Sample Question 1`·`시나리오 5`·`Exercise 2`)으로 감지한 청크를 앞에 두고 남은 자리를 하이브리드로 채움. BM25는 영어 토크나이저(가이드가 영어), 가중치 BM25 0.4 · 벡터 0.6, 재작성은 `rewrite_query` LLM 1회(실패 시 원문). rewrite·retrieve 결과는 custom event로 trace에 기록
- **선행 검색** (`retriever.start_prefetch`·`PREFETCH`): `CoachApp.run`이 1차 규칙 통과 직후 질문 원문으로 재작성 + 필터 없는 하이브리드 검색을 시작(입력 가드 LLM·Supervisor 배분과 동시). `search_guide`는 `wait_input_ok()` 다음에 query가 이번 턴 질문과 같으면 이 결과를 씀(필터가 있으면 재작성 질의만 재사용). trace `rewrite.prefetch` = hit / query_only / miss. 차단되면 취소하고, 결과는 gate 통과 뒤에만 쓰이므로 차단 입력의 검색 결과는 나가지 않음. 채점 입력(§6)에는 하지 않음
- **첫 검색 주입** (`concept_tutor.ForceFirstSearch`, before_model): 이번 턴에 아직 검색하지 않았으면 모델을 부르지 않고 `search_guide(질문 원문)` 호출을 넣어 도구 단계로 감(프롬프트가 "먼저 검색"으로 고정이라 첫 모델 호출은 검색 결정뿐이었음). 결과를 본 모델은 바로 답하거나 한 번 더 검색
- **검색 결과 트리밍** (`retriever.trim_hits`) — 청크 단위 필터: 1순위 hit, 정확 조회 hit, task_statement가 아닌 청크(샘플 문항·범위·개요 등)는 그대로. 2순위 이하 `task_statement` 청크는 영어 검색어와 키워드(일반어 제외)가 겹치는 bullet이 하나라도 있으면 전문, 없으면 뺀다. 청크 안의 bullet은 자르지 않음(bullet 단위로 자르면 답에 필요한 bullet까지 빠짐)
- **TutorAnswer + G3**: `create_agent(response_format=ToolStrategy(TutorAnswer))` + after_agent 미들웨어 `TutorAnswerGuard`. claim은 **이번 턴에 search_guide/get_chunk가 실제로 돌려준 doc_id를 가리킬 때만 유효**(대소문자 무시). 유효 claim 0개(또는 구조화 응답 없음)면 고정 문구 `NO_EVIDENCE`(+coverage_gap). 아니면 answer_ko + "가이드에 없는 부분: …"(coverage_gap) + `GAP_NOTICE`(coverage_gap이 있을 때) + 코드가 만든 `[근거: …]`(answer_ko의 LLM 표기는 지움). excerpt 원문 일치 수는 trace `output_check`에만 기록(강제 안 함). 결과는 `name="concept_tutor"` AIMessage로 마지막에 붙여 Supervisor last_message·turn_answer가 사용
- **장기 대화 요약** (`memory.compact`, `CoachApp.run`): 턴 시작 전 thread 메시지가 `config.SUMMARY_TRIGGER_MESSAGES`(40)를 넘으면 최근 `SUMMARY_KEEP_TURNS`(3)개 턴은 그대로 두고 그 앞을 LLM 1회로 한국어 10줄 이내 요약 → `SystemMessage`([이전 대화 요약] + [핵심 사실 — case_facts, 코드 보존]) 하나로 바꿔 바깥 그래프 상태에 `aupdate_state`로 적용(`RemoveMessage(REMOVE_ALL)`). 자르는 지점은 사용자 입력 경계라 도구 호출 쌍이 끊기지 않음. 요약 LLM은 입력 가드 판별과 동시에 돌고, 실패하면 원래 메시지 유지. Supervisor `pre_model_hook`으로 지우면 내부 그래프에만 적용되고 바깥 thread에는 옛 메시지가 남아서 그래프 밖에서 처리. 하위 에이전트도 같은 messages를 받으므로 study_coach는 요약 블록의 일수·시간을 쓰고 다시 묻지 않음. 가드 차단 턴은 `_rollback`이 messages·case_facts를 통째로 복원. 검증: `evaluation/smoke_memory.py`

### 5-2. 문제 풀 사전 생성 (오프라인, `scripts/build_quiz_pool.py`)

**규모와 배분: 목표 승인 문제 270개** (실제 266, 아래 "실제 결과")
- **기본 슬롯 240개**: 세부 항목마다 1문제 (그 항목 하나를 판단 근거로 삼는 문제)
- **추가 슬롯 30개**: 도메인 비중으로 배분. 같은 TS 안 세부 항목 2개를 결합한 판단형 문제. 세부 항목 수가 적은 TS부터 배정

| 도메인 | 비중 | 세부 항목(기본) | 추가 | 합계 | 배정 시나리오 |
|---|---|---|---|---|---|
| D1 Agentic Architecture | 27% | 48 | 8 | 56 | SC-1, SC-3, SC-4 |
| D2 Tool Design & MCP | 18% | 43 | 5 | 48 | SC-1, SC-3, SC-4 |
| D3 Claude Code | 20% | 49 | 6 | 55 | SC-2, SC-4, SC-5 |
| D4 Prompt Eng. & Structured Output | 20% | 47 | 6 | 53 | SC-5, SC-6 |
| D5 Context & Reliability | 15% | 53 | 5 | 58 | SC-1, SC-2, SC-3, SC-6 |
| 합계 | | 240 | 30 | **270** | |

- 풀 구성은 시험 비중과 일치하지 않음(D1 21%, D5 21%). 풀은 **빠짐없이 다루는 것**을 맡고, 실제 **출제 빈도는 `get_quiz`가 시험 비중 × 약점으로 조절** (§5-3).
- 시나리오는 도메인의 primary 시나리오 목록에서 TS 안 순서대로 순환 배정. 검토에서 `scenario_mismatch`가 나오면 재시도 때 목록의 다음 시나리오로 바꿈.
- 예외: 세부 항목 원문이 특정 시나리오를 전제하면 시작 시나리오를 고정 (`build_quiz_pool.SCENARIO_OVERRIDES`. 순환 위치는 그대로 진행해 다른 슬롯 배정은 불변). `TS-1.4-S2`("multi-concern customer requests") → SC-1

**생성 절차**
1. **매니페스트 작성**: 기본·추가 슬롯 270개(보충·예외 슬롯은 나중에 추가, `quiz_id`, task_id, focus_bullet_ids, scenario_id)를 `data/quiz_pool_manifest.json`에 기록. 슬롯 상태(pending/approved/rejected/duplicate/needs_fix)를 갱신하므로 중단돼도 `--resume`으로 미완료 슬롯만 이어서 생성 (TS 4.5 custom_id 재제출, TS 5.4 manifest 기반 복구)
2. **파일럿 10문제**(도메인당 2개)로 생성·검토 프롬프트를 먼저 다듬음 (TS 4.5: 대량 처리 전 표본으로 프롬프트 개선)
3. **생성 순서**: 기본 슬롯 240개를 먼저 전 도메인에 걸쳐 생성하고, 그다음 추가 슬롯 30개 (중간에 멈춰도 커버리지 우선 확보)
4. **슬롯별 생성**: 해당 TS 청크 + 초점 세부 항목 + 시나리오 청크 + 샘플 문항 2개 few-shot → `quiz_gen.generate()` (TS 4.2)
5. **코드 검증**(Pydantic): 보기 4개, 라벨 중복 없음, 정답 라벨 존재, task_id·bullet_id·scenario_id 실재
6. **독립 검토**: `quiz_reviewer`는 생성 추론 없이 문항 + 근거 청크 + 초점 세부 항목만 받아 판정 (TS 4.6)
7. **재시도**: 실패 시 근거 + 실패한 문항 + 구체적 오류를 붙여 재생성, 최대 2회 (TS 4.4). 그래도 실패하면 `rejected`로 남기고 `detected_patterns` 기록
8. 동시 실행 **기본 2개**(`--concurrency N`), Bedrock throttling 시 지수 백오프 + 모델 전환(아래 "모델 전환")

**구현 세부 규칙**
- **문항 언어**: stem·options는 영어(실제 시험·few-shot과 동일), rationale은 한국어 + 영어 용어 병기, source_excerpt는 가이드 영문 원문 그대로
- **추가 슬롯 쌍**: 같은 TS의 K×S 쌍 중 **키워드 겹침이 가장 큰 쌍**을 고르고, 같은 TS에 2개를 배정하면 이미 쓴 쌍은 피함 (`quiz_gen.pair_extra_bullets`). 키워드 = 소문자 토큰 3자 이상 − 불용어, 간이 어간(-ing/-es/-s/-ed). 점수: 겹친 수 → Jaccard → 아직 안 쓴 불릿 수 → (K, S 번호) 오름차순. quiz_id `Q-x.y-Xj`, 시나리오는 그 TS의 순환을 이어서 배정. `--dry-run`이 쌍별 겹친 키워드를 출력 (30쌍 모두 4~11개 겹침)
- **few-shot**: 슬롯 시나리오의 SQ 2개(quiz_id 시드). SQ가 없는 SC-4·SC-6은 도메인 시나리오 목록 순서로 채움
- **코드 검증 세부 패턴** (`detected_patterns`에 `code` 대신 기록. 위가 더 심각하며, 시도 하나의 대표 패턴은 이 순서로 고름):

  | 패턴 | 조건 |
  |---|---|
  | `format` | 보기 4개·라벨 A~D 각 1회·정답 라벨 존재·보기 텍스트 중복 없음·빈 값 없음·stem 길이 |
  | `unknown_ref` | task_id·bullet_id·scenario_id 실재 |
  | `excerpt_not_verbatim` | source_excerpt가 TS 원문 한 줄의 부분 문자열인지 (초점 적합성은 검토자 `off_focus_bullet`이 판정) |
  | `language` | stem·options에 한글 없음, rationale은 한국어 |
  | `label_reference` | 보기를 라벨로 가리키는 표현 금지 (보기를 섞으면 라벨이 바뀌므로 해설은 자기 내용만 설명) |
  | `meta_reference` | 해설에 내부 용어("focus item", "task statement", source_excerpt) 금지 |
  | `forbidden_phrase` | "all/none of the above", stem·보기에 가이드·TS 번호 언급 금지 |
  | `answer_too_long` | **정답 길이 단서 방지**: 정답 보기 길이 ≤ 오답 평균 × 1.25 그리고 ≤ 가장 긴 오답 × 1.15 |
- **길이 repair**: 한 생성 시도의 코드 검증 오류가 **전부 `answer_too_long`**이면 재생성 대신 보기만 고치는 repair를 **시도마다 1회** 호출 (`src/prompts/quiz_repair.md`, 출력 `QuizOptionsRepair`)
  - 입력: TS 원문 + 초점 항목 + 문항 + 길이 수치(정답·오답 평균·가장 긴 오답). 지시: 지문과 정답 보기는 유지, 정답은 의미를 유지한 채 "왜 좋은지" 설명구를 rationale로 옮기고, 오답은 같은 구조·구체성으로 맞춤
  - 코드로 options만 원본 draft에 끼움(stem·answer·source_excerpt 불변, 라벨 집합이 바뀌면 거부) → 코드 검증 다시 → 통과하면 **반드시 독립 검토**로 진행 (정답 의미가 바뀌었으면 검토에서 걸림)
  - repair는 attempts(생성 호출 수)에 넣지 않음. 매니페스트 `repairs`·`repairs_ok`, 성공 시 detected_patterns에 `answer_too_long:repaired` 기록. repair 후에도 실패하면 그 시도는 실패로 처리하고 피드백에 "repair 후에도 실패"를 명시
- **생성 프롬프트 길이 균형 지침**: 정답을 먼저 쓰고 오답 3개를 같은 문장 구조(행동 + 수단 + 범위)·같은 수준의 세부로 작성, 보기당 약 15~30단어, 정답에만 "왜 좋은지" 설명구를 붙이지 않음(해설로), 반환 전 길이 비교
- **재시도 피드백**: 원래 생성 프롬프트 전체 + 실패 문항 JSON + 문제점 목록(`[parse]`/`[code:세부 패턴]`/`[review:패턴]`) + 패턴별 고정 지시문(`src/prompts/FIX_HINTS`, 코드 세부 패턴별로 분리) + 이전 시도 요약 한 줄씩. `scenario_mismatch`면 다음 후보 시나리오로 바꾸고 그 사실을 피드백에 명시 (마지막 시도 뒤에는 바꾸지 않음)
- **Bedrock 일시 오류**(throttling 등)는 재시도 횟수(attempts)에 넣지 않음. 처리할 수 없으면 슬롯은 pending으로 남음
- **모델 전환** (문제 풀 생성용 — 런타임 전환은 §7-1): 생성·검토·repair·시나리오 순위 호출은 `quiz_gen.ModelPool`을 거침
  - 체인 `config.QUIZ_GEN_MODEL_CHAIN`: us Sonnet 4.5(기본) → global Sonnet 4.5 → us Sonnet 4.6 → global Sonnet 4.6. **Haiku는 쓰지 않음**(문항·검토 품질, 검토자가 생성자보다 약하면 안 됨)
  - 쓰로틀된 모델은 쿨다운(`min(60, 4·2^k)`초, 일일 토큰 할당량 소진 메시지면 60분)을 걸고 **기다리지 않고 다음 모델로 전환**. 기본 모델은 쿨다운이 끝나면 다시 우선 사용
  - 체인 전체가 쿨다운이면 가장 빨리 풀리는 시각까지 대기, 한 호출의 대기가 180초를 넘으면 `TransientError` → 슬롯 pending, 연속 3개 슬롯이면 실행 중단(재실행은 `--resume`)
  - 일시 오류가 아닌 오류(ValidationException 등)는 전환하지 않고 올림(모델 문제를 다른 모델로 감추지 않음). 권한 없음·모델 없음은 그 모델을 이번 실행에서 제외
  - botocore 내부 재시도는 1회로 줄여(기본 4회) 전환이 바로 일어나게 함
  - 생성 모델과 검토 모델이 달라질 수 있음(독립성에는 유리). 기록: `QuizItem.generator_model` = 최종 문항을 만든 실제 생성 모델(repair가 있었어도 원 생성 모델), `ManifestSlot.generator_model`·`reviewer_model`
  - 접근 확인: `python scripts/check_bedrock.py --quiz-chain`
- **저장**: 코드 검증을 통과한 문항만 `quiz_pool.json`에 저장(approved / 검토 탈락 rejected / duplicate). 코드 검증 단계에서 끝난 rejected는 매니페스트에만 기록

**풀을 다시 만들거나 프롬프트를 고칠 때만 쓰는 규칙**
- **파일럿**(`--pilot N`): 실행할 때마다 해당 슬롯을 새로 생성 (프롬프트 조정 반복용)
- **`--reset-stale`** (LLM 호출 없음): 현재 `prompt_version`과 다른 버전으로 처리된 슬롯을 되돌림. base/extra는 pending으로 초기화(attempts·repairs·detected_patterns·last_errors, 시나리오는 원래 배정값), fill 슬롯은 삭제, pool에서 해당 문항 제거. 자동으로 실행하지 않고 오래된 버전 슬롯 수만 경고로 출력
- **매니페스트 동기화**: 슬롯 배정 규칙이 바뀌면 pending 슬롯의 정의(focus_bullet_ids, scenario_candidates)는 자동 갱신. 처리된 슬롯의 정의가 바뀌었으면 오류(`--reset-stale` 후 재실행)

**후처리 (코드)**
- **보기 섞기**: LLM은 정답을 특정 위치에 몰아 두는 경향이 있으므로, 정답 보기를 슬롯의 `target_answer` 위치에 놓고(A~D 균등 리스트 68/68/67/67을 고정 시드로 섞어 슬롯 순서대로 배정, 보충 슬롯은 quiz_id 해시) 오답 3개는 `quiz_id`를 시드로 섞은 뒤 라벨을 다시 매핑. 결과 정답 분포가 A~D 각 20~30%인지 확인
- **중복 제거**: 같은 TS 안에서 지문 임베딩 코사인 유사도 0.92 이상이면 뒤 문제(order 기준: 기본 → 추가 → 보충)를 `duplicate` 처리. 매 실행 끝에 수행

**커버리지 보충 (`--fill-gaps`)**
- **가드**: pending인 base/extra 슬롯이 하나라도 남아 있으면 거부하고 종료 코드 1 (우회 옵션 없음). 기본 커버리지가 끝나기 전에 보충 슬롯이 생겨 한 항목에 문제가 2개 생기는 것을 막음
- 승인 문제가 없는 세부 항목만 골라 새 슬롯(`Q-x.y-K2-F`)으로 1회 더 생성. 세부 항목당 최대 2슬롯(기본 + 보충). 보충 슬롯 프롬프트에는 같은 TS의 승인 문항 지문을 "반복 금지"로 넣음
- 보충 슬롯 시나리오: 직전 슬롯이 마지막에 시도한 시나리오를 재사용. 단 마지막 실패가 중복이면 다음 후보
- **예외: 시나리오 불일치로 탈락한 세부 항목**: 직전 슬롯의 최종 탈락 사유가 `scenario_mismatch`이면 보충 슬롯에서는 **도메인 primary 시나리오 제한을 풀고 6개 시나리오 전체**를 후보로 씀
  - 순서: LLM이 세부 항목 내용과 시나리오 설명(“Primary domains” 줄 제외)의 적합도로 6개 순위를 매김 (`src/prompts/scenario_fit.md`, 출력 `ScenarioFit`, 슬롯당 1회). 코드가 허용 시나리오만 남기고, 직전 슬롯에서 이미 불일치가 난 시나리오는 맨 뒤로 보냄. 결과를 보충 슬롯의 `scenario_candidates`로 저장하므로 재시도 순환도 이 순서를 따름
  - 예: `Q-5.4-S3`(탐색 단계 요약 후 sub-agent 생성), `Q-2.5-K1`(Grep 내용 검색) → SC-4 개발자 생산성
  - **금지 조합은 유지**: `quiz_gen.FORBIDDEN_SCENARIOS = {"TS-1.7": {5}}`. TS-1.7 × SC-5는 `evaluation/test_queries.csv` 13번이 "풀에 없는 조합 → no_match + alternatives"로 전제하므로 어떤 경로로도 만들지 않음 (슬롯 검증 `plan_errors`에서도 확인)
  - 시나리오 순위 프롬프트는 문항 생성 프롬프트가 아니므로 `prompt_version` 해시에 넣지 않음
  - `no_match`는 배정 규칙이 아니라 **풀에 실제로 있는 approved 문항** 기준이므로(§5-3), 이 예외로 생긴 조합(예: D5 primary 밖인 TS-5.4 × SC-4, `Q-5.4-K3-F`·`Q-5.4-S3-F`)도 정상 출제 대상. `quiz://catalog`도 실제 풀 기준으로 집계
- 커버리지 = 승인 문항의 focus_bullet_ids에 한 번이라도 들어간 세부 항목 (추가 슬롯 승인도 인정). 아직 pending 슬롯이 있는 항목은 보충 대상이 아님

**예외 슬롯 (`--exception-slot BULLET_ID:SC`)**
- 기본 + 보충 2슬롯이 모두 탈락해 미커버로 남은 세부 항목에만, 시나리오를 고정한 슬롯 1개를 추가 (`slot_type: "exception"`, quiz_id `Q-x.y-K2-E`, `scenario_candidates=[SC]`라 재시도에도 시나리오 고정). "항목당 최대 2슬롯" 규칙의 **명시적 예외**이며 매니페스트·리포트에 기록
- 가드: 이미 승인 문항이 있는 항목, 금지 조합(`FORBIDDEN_SCENARIOS`), 같은 quiz_id는 거부. 같은 TS의 승인 문항 지문을 "반복 금지"로 넣음
- 사례: `Q-5.2-K2-E` (TS-5.2-K2 "고객이 명시적으로 요구하면 즉시 에스컬레이션", SC-1 고정)

**정답 길이 재조정 (`--rebalance-length`, 승인 후 사후 작업)**
- 대상: 승인 문항 중 **정답 > 가장 긴 오답 × 1.05**. 상한을 올리는 대신 **오답 하나가 정답보다 길게**를 목표로 보기만 다시 씀 (`src/prompts/quiz_rebalance.md`, 출력 `QuizOptionsRepair`)
- **목표 구간: 0.80 ≤ 정답 ÷ 가장 긴 오답 ≤ 0.95, 그리고 정답이 가장 짧은 보기가 아님** (`quiz_gen.rebalance_length_issues`). 하한이 없으면 정답을 과도하게 줄여 "가장 짧은 보기 = 정답" 역방향 단서가 생김
- 지문·정답 보기(의미)·source_excerpt는 불변. 정답은 조금만 다듬고("왜 좋은지" 설명구는 해설로), 그럴듯한 오답 하나를 구체적 메커니즘·범위로 보강해 가장 길게, 다른 오답 하나는 정답보다 짧게 유지
- 코드 검증 + 구간 확인 → 실패하면 오류를 붙여 다시 (문항당 최대 3회) → 통과하면 **독립 검토 필수**. 검토 탈락·검증 실패면 **원래 문항을 유지**하고 `length_cue:rebalance_failed`, 성공하면 `length_cue:rebalanced` 기록 (재실행 시 건너뜀)
- 모델: us Sonnet 4.5 할당량 보존을 위해 제외, global Sonnet 4.5 → us 4.6 → global 4.6 (`MAINT_MODEL_CHAIN`, 예외 슬롯도 같음). 재조정 프롬프트는 생성 프롬프트가 아니므로 `prompt_version` 해시 밖
- `generator_model`·`created_at`은 원래 값 유지, 검토 모델은 `ManifestSlot.reviewer_model`에 갱신

**사람 표본 검토 (TS 5.5 층화 표본)**
- 도메인별로 **재시도 없이 통과한 문제의 10% + 재시도 거친 문제의 20%** 무작위 추출 (실제 38문제). 재시도 문제는 오류 가능성이 높아 표본 비율을 높임
- 층(도메인 × 재시도 유무, 10개)마다 **repair 문항(생성 단계 길이 repair 또는 정답 길이 재조정) 최소 1개**를 먼저 넣음. 보기를 사후에 고친 문항이라 오류 가능성 확인 필요
- `python scripts/review_sample.py` → **`evaluation/review_sample.xlsx`** (고정 시드) + `evaluation/human_review_results.csv`(빈 판정). 검토 입력이 있으면 `--force` 없이 덮어쓰지 않음
  - 시트1 **"검토"**(정답 없음, 먼저 풀기): quiz_id · 층(도메인/재시도/repair) · 시나리오 · 지문 · 보기 A~D · **내 답**(드롭다운 A~D) · **판정**(드롭다운 OK/경미/오류) · **사유**(드롭다운 정답복수/근거불일치/오답뻔함/초점이탈/시나리오부적합/기타) · 메모
  - 시트2 **"정답·근거"**: quiz_id · 정답 · 보기별 해설 · 초점 세부 항목 원문 · source_excerpt
  - xlsx는 세부 항목 원문(가이드 문장)이 있어 **커밋하지 않음**(.gitignore, §2-4). 결과는 가이드 원문 없이 `human_review_results.csv`에 남기고(커밋), 리포트는 이 CSV만 읽음
  - 사유 → 검토 패턴 slug: 정답복수 `multiple_correct` · 근거불일치 `answer_not_supported` · 오답뻔함 `distractor_obviously_wrong` · 초점이탈 `off_focus_bullet` · 시나리오부적합 `scenario_mismatch` · 기타 `other`
- **`python scripts/review_sample.py --apply`** (`--dry-run`이면 검증·집계만): 값 검증(경미·오류는 사유 필수, 기타는 메모 필수, 하나라도 틀리면 아무것도 바꾸지 않고 종료 1, 판정 빈 행은 미검토) → 검토한 문항 `human_checked=True` → **판정 "오류"는 문항·슬롯 status `needs_fix`**(출제 대상 제외, 커버리지에서도 빠짐. 판정을 고쳐 다시 반영하면 approved로 복귀) → 결과 CSV 갱신 → 리포트 자동 절 갱신. 콘솔·리포트에 판정 분포, 사유 분포, **내 답 ≠ 정답 목록**, 같은 사유 2건 이상인 도메인 경고
- **오류 정의**: 성공 기준(오류 0건 목표, 최대 1건)의 "오류"는 **판정=오류 전부**(대표 사유: 정답복수·근거불일치). **경미는 별도 집계**하고 성공 기준에 넣지 않음
- 결과 집계: `python scripts/pool_report.py`가 리포트 자동 절을 갱신 (수기 절은 보존)
- 같은 사유가 한 도메인에서 2건 이상(오류+경미 합산)이면 그 도메인 프롬프트 수정 후 재생성 검토
- 결과 문서: `evaluation/quiz_pool_report.md` (도메인·시나리오별 승인 수, 세부 항목 커버리지, 승인율, 실패 유형 분포, 정답 위치 분포, 중복 제거 수, 사람 검토 오류율)

**실제 결과 (S1, 2026-11-04)** — 세부 수치는 `evaluation/quiz_pool_report.md`
- **승인 266개** (목표 270, 최소 250 충족) · 슬롯 284개 처리(approved 266 · rejected 16 · duplicate 2) → 승인율 93.7%. 도메인 D1 55 · D2 47 · D3 55 · D4 53 · D5 56, **세부 항목 커버리지 240/240**
- 쓰로틀링으로 중단된 실행을 매니페스트 `--resume`으로 이어서 완료(TS 5.4) → `--fill-gaps` 13슬롯(12 승인) → 예외 슬롯 `Q-5.2-K2-E`로 커버리지 239 → 240
- 승인 문항 생성 모델 us Sonnet 4.5 209 · global Sonnet 4.5 57 (us 일일 할당량 소진 후 전환, Sonnet 4.6은 쓰이지 않음)
- 사후 길이 재조정: 대상 75개 중 성공 55 · 실패 20(원본 유지) → 정답이 가장 긴 보기 54% → 33.8%

### 5-3. 퀴즈 출제 (런타임)
1. `get_quiz(user_id, domain?, task_id?, scenario_id?)` 호출
   - **실제 풀**(`data/quiz_pool.json`의 `status == "approved"` 문항)을 조회해, 조건에 맞고 사용자가 아직 풀지 않은 문제 중 하나를 정답 없이 반환. 매니페스트·슬롯 배정 규칙으로 판단하지 않음
   - 조건이 없으면 도메인을 **시험 비중 × 약점 계수** 비례로 고르고, 그 도메인 안에서는 사용자가 아직 안 푼 세부 항목을 우선
     - 약점 계수(코드 계산, `quiz_bank_server.QuizBank`): 그 도메인 시도 3회 이상이면 `max(0.2, 1 − 정답률)`, 3회 미만이면 1 (기록 부족 → 시험 비중만). 하한 0.2는 잘 푸는 도메인도 가끔 나오게 함
     - 후보 우선순위: 안 낸 문제이면서 안 푼 세부 항목을 다루는 문제 → 안 낸 문제 → 안 푼 문제. 무작위는 `user_id:시도 수:출제 수` 시드
   - `no_match` 형식: `{"status": "no_match", "reason": "not_in_pool" | "all_solved", "message", "alternatives": [{domain, task_id, scenario_id, count, relaxed}]}`. alternatives는 시나리오 → TS(같은 도메인 안) → 도메인 순으로 조건을 하나씩 뺀 조합, 최대 6개
2. `no_match`이면 문제를 지어내지 않고 "해당 조합의 문제가 없다"고 밝힌 뒤 `alternatives`의 첫 조합으로 바로 출제 — **get_quiz 래퍼가 코드로 처리**: "요청한 조합의 검토된 문제는 아직 풀에 없습니다. 문제를 새로 만들지는 않습니다" + 첫 대체 조합으로 바로 출제 + 다른 대안 나열
3. 사용자에게 지문과 보기만 보여주고 `pending_quiz_id` 저장

`no_match`가 나는 경우: **풀에 실제로 없는 조합** 요청(approved 문항이 0개인 TS·시나리오 등. 예: TS-1.7 × SC-5 — `FORBIDDEN_SCENARIOS`로 어떤 경로로도 만들지 않음, `test_queries.csv` 13번), 해당 범위 문제를 사용자가 모두 푼 경우. 배정 규칙 밖이라도 보충·예외 슬롯으로 승인된 조합은 출제함 (§5-2).

### 5-4. 채점
"답은 B" 또는 UI 보기 클릭 → `pending_quiz_id` 확인 → `submit_answer`(MCP)가 문제 은행의 정답으로 **코드 채점 + 기록 저장** → 채점 결과(정오, 정답, 풀의 한국어 보기별 해설, `[근거: TS-x.y]`)를 **submit_answer 래퍼가 코드로 서식화해 그대로 답변으로 씀**(return_direct, §3-1). `pending_quiz_id`가 없으면 "채점할 문제가 없다"고 안내.

채점을 LLM 판단이 아니라 코드로 하고, 기록 저장을 같은 도구 안에 묶어 "채점은 했는데 기록이 빠지는" 경우를 없앰 (TS 1.4 프로그램적 강제).

### 5-5. 학습 계획 (Plan-Execute, Day 7 재사용)
1. `get_progress` / `get_weak_domains`
2. `allocate_study_time(days, hours_per_day)` → 결정적 계산 (LLM 계산 금지, 비중·정답률은 래퍼가 코드로 가져옴)
3. planner → execute(일자별 TS 배정 + 관련 Exercise 연결 + 해당 TS의 남은 문제 수 안내) → replan → finalize
4. 남은 일수·하루 시간은 질문이나 같은 대화의 `case_facts`(요약 블록)에서 쓰고, 없으면 study_coach가 묻는다. 시험일은 저장하지 않음

구현 (`src/planner.py`):
- 배분: 몫 ∝ 시험 비중 × 약점 계수(`quiz_bank_server.weakness_factor` — 출제 가중 §5-3과 같은 식: 시도 3회 이상 max(0.2, 1 − 정답률), 미만 1.0), 0.5시간 단위 최대 잔여법 → **합계 = 일수 × 하루 시간** 항상 일치. demo_heavy 10일×2시간 → D1 4.0 · D2 5.5 · D3 2.0 · D4 3.5 · D5 5.0 = 20h. LLM 인자는 days·hours_per_day만 (weights·accuracy는 내부 인자)
- 일자별 도메인 시간표(grid)도 코드: 도메인 시간을 1시간 블록(+0.5 나머지)으로 나눠 기간 전체에 고르게 펼침
- plan(LLM, `StudyPlanDraft`): grid의 각 (일차, 도메인) 칸에 TS·시간만 배정 (TS 제목·정답률·남은 문제 수 제공, 약한 TS 우선). 칸 설명은 LLM이 쓰지 않고 finalize가 TS 제목(가이드 원문)을 붙임 — 출력 토큰 감소, 지어낸 초점 문장 없음
- execute(코드): TS 유효성·칸 시간 합 검증 → replan: 위반 목록을 주고 LLM 수정 1회 → 그래도 어긋나면 결정적 기본 배정(정답률 낮은 TS부터)
- finalize(코드 서식화): 약점 근거표 → 배분표(합계) → 일자별 계획(남은 문제 수) → 연결 Exercise(EX 청크의 "Domains reinforced" 파싱)

---

## 6. 가드레일 (코드로 강제, 프롬프트 의존 최소화 · TS 1.4/1.5 원칙)

| # | 규칙 | 위치 | 구현 |
|---|---|---|---|
| G1 | 실제 시험 문항·덤프·기출 요청 거절, 대신 원본 연습 문제 제안 | 입력 | 정규식 + LLM 판별 (아래 구현) |
| G2 | 프롬프트 인젝션·시스템 프롬프트 요구 차단 | 입력 | Day 5 `guards_input.py` 재사용 |
| G3 | 출처 없는 단정 금지: 개념 답변은 claims ≥ 1, 아니면 "가이드 근거 없음" 응답으로 대체. `coverage_gap`이 있으면 공식 안내 확인 문구(`GAP_NOTICE`)를 코드가 붙임 | 출력 | `TutorAnswer` 검증 후 분기 |
| G4 | 공식·제휴 사칭, 합격 보장 표현 금지 (`공식 튜터`, `합격 보장`) | 입력 + 출력 | 입력: 공식·인증·제휴·합격 보장 질문은 고정 정정 문구로 바로 답함(그래프 미호출). 출력: 긍정형 금지 표현 문장 제거 + 고정 고지 |
| G5 | 범위 밖 주제(Claude 시험과 무관한 질문)는 한 줄 안내 후 종료 | 입력/라우팅 | Supervisor 배분 기준 + general 응대 |
| G6 | 채점 전 정답 노출 금지 | 도구 | `get_quiz`는 `QuizPublic`만 반환. 정답은 `submit_answer`에서만 공개 |
| G7 | 검토를 통과하지 못한 문제는 출제 금지, 런타임에 문제를 만들지 않음 | 도구 | `get_quiz`는 `approved`만 반환. 풀에 없으면 안내 + 대체 조합 출제 |

고정 거절 문구는 Day 5 `guards_refusal.py` 방식으로 상수화 (평가 시 문자열 매칭 가능하게).

구현 (`src/guards.py`, 실행 순서는 `agent.CoachApp.run`):
- 입력 가드(`rule_check` → `llm_guard`): 1차 정규식 G1(`덤프`·`기출`·`실제 (시험에 나온) 문제` — "실제 시험 문제처럼" 같은 스타일 요청은 제외 — `exam dump`·`real questions`) → G2(Day 5 INJECTION_PATTERNS) → G4. 1차 규칙에 걸리면 그래프를 부르지 않음 → 도구 호출 0건. 걸리지 않으면 2차 LLM `InputCheck` 1회(G1+G2 함께, confidence > 0.7만 차단, 실패 시 통과 + 기록)를 **그래프와 동시에** 시작 (순차 실행 대비 응답 시간 약 4~5초 단축)
  - 앱 도구 8개는 첫 줄 `guards.wait_input_ok()`에서 판별 통과를 기다림(`INPUT_GATE` ContextVar의 Future). 판별 전에는 Supervisor 배분·하위 에이전트 LLM까지만 진행
  - LLM이 차단하면 그래프 task를 취소(도구는 gate에서 멈춰 실행 0건), 이번 턴에 thread에 추가된 메시지를 `RemoveMessage`로 되돌리고(`CoachApp._rollback`), trace는 guard 단계만 남김(`tracer.discard_graph_run`) → 차단 문항은 여전히 도구 호출 0건
  - 새 앱 도구를 추가하면 반드시 첫 줄에 `wait_input_ok()`를 둔다
  - **채점 입력은 LLM 판별 생략**: 출제 대기 중(`pending_quiz_id` 있음)이고 입력이 답 한 글자 형식(`guards.ANSWER_ONLY`: "B", "답은 c", "D요")이면 2차 LLM 판별 대신 바로 통과(`layer=rule`, reason `answer_only`). 한 글자 답에는 덤프 요청·인젝션이 들어갈 수 없음
  - 선행 검색(§5-1)은 판별과 동시에 돌지만 앱 도구가 아니고 부작용이 없으며, 결과는 gate 통과 뒤 `search_guide`에서만 쓰임
- 상수: `REFUSALS["G1"]`·`REFUSALS["G2"]`, `G4_CORRECTION`, `G4_NOTICE`, `NO_EVIDENCE`·`GAP_NOTICE`(G3), `OFF_TOPIC`(G5, general_agent 프롬프트에 그대로 삽입)
- 출력 `output_check`(G4 백스톱): 문장 단위로 금지 표현이 있고 부정어(아니·않·없·비공식 등)가 없으면 그 문장을 지우고 `G4_NOTICE`를 붙임
- 차단 사유는 `logs/guard_audit.log`에만 (사용자에게 비노출)
- 한계: G4 정정 질문과 개념 질문이 한 메시지에 섞이면 정정 문구만 답함

---

## 7. API 규약 매핑

```
POST /query
Body: {"question": "...", "user_id": "..."?, "thread_id": "..."?}   # 뒤 2개는 선택 (user_id 기본 "demo_user", thread_id 없으면 새로 만듦)
Response: {"answer": "...", "contexts": [{"doc_id": "TS-1.4", "text": "..."}], "trace": [...], "thread_id": "..."}
```
- `thread_id`: 요청에 없으면 새로 만들어 돌려줌. 같은 thread_id로 이어 부르면 출제 → 채점이 이어짐 (checkpointer `InMemorySaver`, 서버 재시작 시 초기화). `user_id`는 `^[A-Za-z0-9_-]+$`
- `answer` 구성: 이번 턴의 하위 에이전트 최종 답변 + quiz_master return_direct 도구 결과를 순서대로 이어 붙임 (`agent.turn_answer`). Supervisor는 답변을 쓰지 않음 → 문항 원문 보존, 재작성 없음
- **에이전트 후 종료** (`agent.after_agent`): `create_supervisor`가 돌려준 builder에서 하위 에이전트 → Supervisor 고정 엣지를 조건 분기로 바꿈. 질문에 출제 요청(`QUIZ_INTENT`)이 있는데 이번 턴에 quiz_master가 아직 안 돌았으면 Supervisor로, 아니면 바로 종료(Supervisor "완료" LLM 호출 생략). thread는 handoff-back 메시지로 끝나고 다음 턴 입력이 그 뒤에 붙음
- Supervisor 동시 핸드오프 금지: Bedrock Converse는 parallel_tool_calls 끄기를 지원하지 않으므로 `post_model_hook`(`agent.single_handoff`)이 첫 핸드오프만 남김 (복합 요청은 concept_tutor → quiz_master 순서로 이어서 처리)
- `contexts`: `search_guide`/`get_chunk` 결과와, 출제 시에는 해당 문제의 TS 청크(정답 단서인 source_excerpt는 넣지 않음)를 콜백으로 수집 (Day 7 `run_eval.py`의 ToolRecorder 방식)
- `trace`: step 단위 `guard → route(agent) → rewrite → retrieve → get_quiz → submit → progress → allocate → plan → output_check` + 끝에 `tools`(이번 턴 앱 도구 이름, 핸드오프·TutorAnswer 제외 — 평가 판정용)와 `llm`(호출 수·토큰·실제 응답 모델). `output_check`는 G3(concept_tutor)과 G4(최종 답변) 두 종류. Day 7 `local_tracer.py` 확장. 로컬 사본 `logs/trace.jsonl`(커밋 안 함)
- 서버: FastAPI (Day 6 `api_server.py` 재사용, 경로만 `/query`로 변경). 시작 때 `retriever.warm_up()`으로 청크·BM25·Chroma를 열고 임베딩·재작성 LLM 1회씩 호출해 첫 요청의 콜드 스타트를 없앰. `config.get_llm`은 (모델, 재시도, 리전)마다 인스턴스를 공유해 모든 에이전트가 같은 Bedrock 클라이언트·연결을 씀
- 웹 UI: `GET /`가 `src/web/index.html`(바닐라 JS, 외부 의존성·CDN 없음)을 서빙하고 `style.css`·`app.js`는 `/static` 마운트. 화면 세부(카드·배너·문구·애니메이션)는 `src/web/`이 기준이고, 설계 원칙은 다음과 같다
  - 백엔드는 `/query`·`/progress`만 쓰고 그래프·가드·도구는 바꾸지 않음. 화면은 표시만 바꾸고 계산(정답률·약점·커버리지)은 서버 값 그대로
  - **정답은 UI도 모름**: `trace`의 `get_quiz` status=ok면 `format_quiz` 텍스트를 파싱해 문제 카드·보기 카드(클릭·키보드 A~D)로 보여 주고 고른 글자만 다음 질문으로 보냄. 채점 후 정답 글자는 `format_grade` 답변 문구에서 읽어 결과 배너·보기 표시·보기별 해설을 그림. 파싱에 실패하면 원문 마크다운
  - 진도 패널: `submit` status=graded 직후 `/progress`로 갱신. 약점 도메인(`get_weak_domains` top 2) 강조, 도메인을 누르면 그 도메인 출제 요청. 시도 3회 미만 도메인은 정답률 대신 "N/3 풀이"(약점 판정 기준과 같게), 전체 10문제 미만이면 정답률 대신 풀이 수. 도메인 막대는 길이 동일·채운 부분 = 정답률(비중은 텍스트)
  - 근거 배지(`[근거: TS-x.y]`)는 이번 대화에서 받은 `contexts`의 가이드 원문을 펼침(채점 턴은 출제 턴의 TS 청크)
  - 대화 기록(최근 60개)·thread_id는 sessionStorage에 두어 새로고침 시 다시 그림(같은 탭·같은 user_id만, 서버 재시작 시 thread는 이어지지 않음을 안내)
  - user_id는 헤더 프로필 메뉴(`web_demo` 기본·`demo_heavy`·`demo_new`)로만 고름. 오타로 빈 사용자가 생기지 않게 자유 입력은 두지 않고, 기본을 시드 사용자가 아닌 `web_demo`로 둬 커밋된 시드 기록을 보호
  - trace는 개발 모드(`/?debug=1`)에서만 표시. 데스크톱·라이트 모드 전용(모바일 레이아웃·다크 모드 없음). 한글은 `body`의 `word-break: keep-all` + `overflow-wrap: anywhere`로 어절 단위 줄바꿈
- `GET /progress?user_id=...`: `get_progress` MCP 결과 + `weak`(`get_weak_domains` top_k=2 결과)를 반환 (코드 계산, LLM·입력 가드 없음, 읽기 전용). 웹 UI 진도 패널용

### 7-1. 런타임 모델 전환 (`src/llm.py`)
- 기본은 고정 모델 `global.anthropic.claude-sonnet-4-5-20250929-v1:0` @ **ap-northeast-2(서울)**. 서울에는 Sonnet 4.5 `apac.` 프로필이 없어(ValidationException) `global.` 프로필을 씀. **쓰로틀링·일일 토큰 할당량 소진 등 일시 오류일 때만** `config.RUNTIME_MODEL_CHAIN` 순서로 전환: Sonnet 4.5(us@us-east-1 → global@us-east-1 → us@us-west-2 → global@us-west-2) → Sonnet 4.6(global@ap-northeast-2 → 미국 리전 같은 순서). Haiku는 쓰지 않음
- 임베딩 Titan v2도 `config.REGION`(서울)을 따름 — us-east-1과 벡터 코사인 0.9999라 Chroma 재색인 안 함. 문제 풀 생성(오프라인)은 `us.` 프로필이 있어 `QUIZ_GEN_MODEL_ID`·`QUIZ_GEN_REGION`(us-east-1)으로 분리
- `FallbackChatModel`(BaseChatModel): 후보마다 ChatBedrockConverse(botocore 재시도 1회). 쓰로틀된 후보는 쿨다운(일일 할당량 60분, 그 외 4·2^k초, 최대 60초)을 걸고 다음 후보로. 쿨다운은 프로세스 안 모든 에이전트가 공유. 일시 오류가 아닌 오류는 전환하지 않고 올림, 권한 없음·모델 없음은 제외
- `bind_tools`는 첫 후보로 Bedrock 형식 도구 kwargs를 만들고 호출 때 실제 후보의 `_generate`에 같은 kwargs를 넘김 (Converse 도구 형식은 모델 간 동일). 응답 메시지 `response_metadata.served_by`에 실제 모델·리전을 남기고 trace `llm.models`에 집계
- 판정 함수(`is_transient`, `is_unavailable`)는 문제 풀 생성 `quiz_gen.ModelPool`과 공용

---

## 8. 데이터 파일

| 파일 | 내용 | 커밋 |
|---|---|---|
| `data/raw/ccaf_exam_guide.pdf` | 시험 가이드 원본 | X |
| `data/guide_chunks.jsonl` | 검색용 청크 62개 | X |
| `data/guide_bullets.jsonl` | 세부 항목 240개 | X |
| `data/quiz_pool.json` | 문제 은행 (`QuizItem`, 승인 266 / 목표 270) | O |
| `data/quiz_pool_manifest.json` | 생성 슬롯과 상태 (재개·보충용) | O |
| `data/study_log.json` | 풀이 기록. 더미 사용자 `demo_heavy`(기록 많음), `demo_new`(기록 없음) 시드 | O |

커밋 여부는 `.gitignore`가 기준이고 이유는 §2-4 (위 표 밖의 `chroma_db/`·`evaluation/review_sample.xlsx`·`.env`·`logs/`도 미커밋).

`study_log.json` 시드는 풀이 완성된 뒤 실제 `quiz_id`를 참조해 생성 (`scripts/seed_study_log.py`).
- `demo_new`: 기록 없음 (test 5번 — 시험 비중만으로 출제)
- `demo_heavy`: 2026-10-21 ~ 11-03, 61회(하루 4~5문제), 30개 TS 전부, 승인 문항만·중복 없음, 고정 시드(`ccaf-seed-v1`)로 재현 가능. 도메인별 시도/정답률 D1 16/75% · D2 11/45% · D3 12/83% · D4 12/67% · D5 10/40% → 약점 점수(비중 × (1 − 정답률)) **D2 0.098 > D5 0.090** > D1 0.068 > D4 0.067 > D3 0.033. 모든 도메인 3회 이상(데이터 부족 없음). D5는 비중이 가장 낮지만 2위 — 정답률만 보는지 비중×약점을 보는지 test 7번에서 구분
- demo 사용자만 덮어쓰고 다른 사용자 기록은 보존. 이미 있으면 `--force` 필요

---

## 9. 평가 설계

### 9-1. 인-아웃 세트 (`evaluation/test_queries.csv`, 20건)
- 비율: positive 8 / negative 4 / edge 5 / guardrail 3 (= 40/20/25/15%)
- 판정 (case PASS 조건):
  1. `expected_tools`가 모두 호출됨 (ToolRecorder). `;`는 "모두 호출", `|`는 "그중 하나 이상 호출"(예: `search_guide|get_chunk`). 빈 값이면 도구 호출 0건이어야 PASS (guardrail 차단 문항)
  2. `forbidden` 항목이 답변에 없음 (문자열 + LLM judge)
  3. `expected_traits`를 LLM judge가 항목별로 충족 판정 (전부 충족해야 PASS)
- 문항 5는 풀 출제, 문항 13은 풀에 없는 조합 요청 시 문제를 지어내지 않고 안내한 뒤 대체 조합에서 출제하는지 검증. 둘 다 출제 응답까지만 판정
- 문항 7(학습 계획)은 기록이 있는 더미 사용자 `demo_heavy`로 실행
- 한 바퀴 검증은 CSV와 별도로 `evaluation/smoke_p0.py`(개념 질문 → 출제 → 채점 3턴 같은 thread_id + 늦은 가드 차단, 24항목)로 확인
- Judge: Bedrock Claude Sonnet 4.5, temperature 0, 루브릭 고정 (Day 7 `llm_judge.py` 확장)

구현 (`evaluation/run_eval.py`, `evaluation/llm_judge.py`):
- in-process(TestClient `/query`), 문항마다 새 thread. 시작 전 `data/study_log.json`을 임시 파일로 복사해 `CCAF_STUDY_LOG`로 지정(커밋된 기록 보존). user_id는 note의 `user_id=…`, 없으면 `eval_user`
- 도구 판정은 trace `tools` step. forbidden은 코드 검사(출제 문항의 정답이 "정답: X"로 노출됐는지, guardrail 문항은 note의 G1·G2·G4에 해당하는 고정 문구가 답변에 그대로 있는지) + judge
- judge 입력: 질문·답변·trace 요약(경로·도구·출제·약점·배분 수치, 가이드 원문 없음) → `JudgeVerdict`(traits·forbidden 항목별 ok + 사유). 빠뜨린 항목은 실패로 둠. `JUDGE_VERSION`으로 루브릭 버전 구분, judge 실제 모델 기록
- 산출물: `evaluation/results/<label>_eval.json`(커밋: 판정·사유·도구·doc_id·지연·호출 수, 답변 본문·가이드 원문 없음) + `logs/eval/<label>_full.json`(비커밋: 답변·contexts, RAGAS 재사용)
- 오프라인 스모크 `evaluation/smoke_p1.py` (44항목, LLM 없음): 가드 규칙, G3 분기, get_chunk 정규화, 진도·약점, 배분 합계, 기본 일정·TS 제목 서식, 검색 결과 트리밍, case_facts 추출, 응답 속도 경로(채점 입력 판정·에이전트 후 종료 분기·첫 검색 주입·계획 return 보정·선행 검색 재사용)
- 긴 대화 스모크 `evaluation/smoke_memory.py` (5턴, 9항목, Bedrock): 요약 발생 + case_facts 보존, study_log 임시 사본 사용
- 웹 UI(§7)는 자동 스모크 없음. 화면 확인은 `CCAF_STUDY_LOG`를 임시 사본으로 지정해 서버를 띄우고(출제·채점 기록이 커밋 대상 study_log에 쌓이지 않게) 1280·1440·1920 너비, 세 프로필(`web_demo`·`demo_heavy`·`demo_new`)로 본다. 2026-11-05 전체 점검 통과 (위 웹 UI 설계 원칙 항목별 동작, 가로 넘침 없음, 콘솔 오류 없음)

### 9-2. RAGAS
- 대상: 근거 검색이 있는 문항만 (`evaluation/ragas_reference.csv`의 `ragas=Y`, 14건). 출제·학습 계획·가드레일 차단 문항은 judge로만 평가
- 지표: context_recall, context_precision, faithfulness, answer_relevancy
- reference는 가이드 원문 근거로 사람이 작성 (순환 평가 방지)
- RAGAS LLM/임베딩도 Bedrock 래퍼로 연결. 3건짜리 스모크(`ragas_smoke.py`)와, 평가 답변 재사용 14건: `ragas_smoke.py --from logs/eval/<label>_full.json --out evaluation/ragas_<label>.json`으로 run_eval이 만든 같은 답변·contexts를 14건 전부 채점
- 주의: 가이드는 영어, 답변은 한국어 → answer_relevancy가 낮게 나옴(영어 핵심 용어 병기를 적용했으나 0.45 안팎). 같은 답변을 다시 채점해도 14건 평균이 ±0.05 정도 흔들림
- 3건 스모크 기준선 (`evaluation/ragas_smoke.py` → `ragas_smoke.json`, 1~3번): faithfulness 0.896 · context_recall 1.0 · context_precision 0.917 · answer_relevancy 0.681. ragas 0.4.3은 langchain-community 0.4에서 사라진 `chat_models.vertexai`를 import하므로 스크립트 안에서만 자리표시 모듈을 끼움

### 9-3. 문제 풀 품질 지표
목표와 최종 판정은 SERVICE §5, 세부 수치는 `evaluation/quiz_pool_report.md`. 여기서는 지표 정의만 둔다.
- 승인 문제 수, 세부 항목 커버리지
- 승인율(승인 ÷ 생성 슬롯), 실패 유형 분포: 검토 패턴(`detected_pattern`) + 코드 검증 세부 패턴(`format`, `excerpt_not_verbatim`, `answer_too_long` 등, §5-2)을 함께 집계. `answer_too_long:repaired`(repair로 복구)는 따로 셈
- 길이 repair 성공률 (`repairs_ok ÷ repairs`)
- 생성 모델별 승인 수·승인율, 승인 문항의 검토 모델 분포 (모델 전환, §5-2)
- 사람 표본 검토 오류율. **판정=오류만 셈**, 경미는 별도 집계 (§5-2)
- 정답 위치 분포, 중복 제거 수
- **정답이 가장 긴 보기 비율** (승인 문항 기준, 동률은 가장 긴 것으로 침. **목표 35% 이하 유지**, 우연 수준 25%). 최종 33.8% (재조정 전 54%)
  - 참고 지표(목표 없음, 병기만): **"정답이 가장 긴 오답보다 5% 넘게 긴 비율"** — 근소한 차이의 동률성 1위를 걸러 실제 단서에 가까운 비율. `--rebalance-length` 대상 기준과 같음. 최종 7.9% (재조정 전 28%). 리포트 `evaluation/quiz_pool_report.md`
- 런타임 `no_match` 비율: 최종 평가에서 출제 요청 3건 중 1건(13번, 풀에 없는 조합을 일부러 요청한 문항)

### 9-4. 개선 후보와 채택 결과

| # | 후보 | 결과 |
|---|---|---|
| 1 | 질의 재작성(한→영) + TS 번호 직접 조회 | S2에서 이미 구현돼 있어 후보에서 제외 |
| 2 | 도구 설명 보강 / 오라우팅 사례 예시 (Supervisor 배분) | **round2 채택** |
| 3 | `search_guide` 결과 트리밍 (TS 5.1) | v2-3에서 청크 단위 필터로 구현 (§5-1) |
| 4 | 개념 답변 `TutorAnswer` 형식 보강 (출처 표기율·faithfulness) | **round2 채택** |
| 5 | reranker 추가 | 하지 않음 |

- #2 (`src/agent.py` SUPERVISOR_PROMPT, `src/agents/general.py`): 시험에 관한 모든 사실 질문(합격률·접수·비용 포함)과 Claude·에이전트 설계 비교 질문은 concept_tutor, general_agent는 인사·기능 안내·명백히 무관한 주제만, 애매하면 concept_tutor. 배분 예시 7개(평가 문항과 다른 표현). general_agent는 시험 사실·URL을 직접 말하지 않음(지어내기 방지)
- #4 (`src/agents/concept_tutor.py` PROMPT): [완결성] 규칙 — 보기 해설은 모든 보기, 제약·한계 질문은 관련 bullet을 항목 단위로 빠짐없이 — 과 answer_ko 형식 템플릿 2개(내용 없는 형식 예시)
- 평가 세트 누출 방지: 예시에 test_queries 문장과 정답 내용을 쓰지 않음
- 풀 재생성(`detected_pattern` 기반 프롬프트 수정)은 풀이 S1 기준을 이미 충족해 하지 않음 (`evaluation/quiz_pool_report.md` "향후 개선")

---

## 10. 매핑표 · 폴더 구조

과제 12개 패턴의 적용 위치(12개 중 11개 구현, 7 HITL 미구현), 시험 TS를 앱 구현에 적용한 지점, LangGraph ↔ Claude Agent SDK 용어 대응은 README "활용한 패턴"을 기준으로 한다.

폴더 구조(과제 §4-1)는 README "폴더 구조"를 기준으로 한다 (커밋하지 않는 파일 포함). 모듈별 설계 위치: 검색 `retriever.py` §5-1, 문제 풀 `quiz_gen.py`·`scripts/build_quiz_pool.py` §5-2, MCP `quiz_bank_server.py` §3-2, 가드 `guards.py` §6, API·웹 UI `api.py`·`web/` §7, 런타임 LLM `llm.py` §7-1.

---

## 변경 이력

- v0.9 (v2-7, 응답 속도): 학습 계획 return_direct 보정(`PlanDirectReturn`, §3-1), 에이전트 후 Supervisor 마무리 호출 생략(`after_agent`, §3·§7), 재작성·검색 선행 실행과 tutor 첫 검색 코드 주입(§5-1), 계획 LLM은 TS·시간만(§5-5), 채점 입력 LLM 가드 생략(§6), 서버 예열·LLM 인스턴스 공유(§7). 20/20, 개념 16.6초·출제 4.1초·계획 15.6초, faithfulness 0.876. claim·excerpt 길이 제한은 faithfulness 하락으로 되돌림
- v0.8 (프로젝트 종료 정리): 문서 간 중복·모순 정리(폴더 구조는 README, 성공 기준·결과는 SERVICE §5, 커밋 금지 기준은 `.gitignore`·§2-4, `no_match` 처리를 "대체 조합 출제"로 통일, 핸드오프 quiz_id·복합 질문·case_facts·학습 계획 입력), 풀 소진은 조건 없는 출제 기준으로 판정, §7 웹 UI를 설계 원칙 중심으로, §9-3을 지표 정의로 축소, §10·§11 통합, 미구현 `reset_progress` 행·개발 경위 문장 삭제, 미사용 코드(`guards.input_guard`, `llm.served_stats`) 삭제, 변경 이력 압축
- v0.6.x (제출 후 개선 v2-1~v2-6): G3 `GAP_NOTICE`(v2-1), 입력 가드 LLM 판별 병렬화(v2-2, §6), 검색 결과 청크 필터·장기 대화 요약·`case_facts`(v2-3, §5-1), 예시 수치 금지 한 줄(v2-4), 웹 UI·`GET /progress`(v2-5, §7), 런타임 LLM·임베딩 서울 리전(v2-6, §7-1). LangSmith/LangFuse·Dockerfile은 하지 않기로 결정. 버전별 결과는 README "평가 결과"
- v0.5.x (S2~S6): 런타임 모델 전환(§7-1), quiz_master MCP 래퍼 + return_direct, 가드레일·`TutorAnswer` + G3, study_coach, 평가 러너, round2 프롬프트 개선(§9-4 #2·#4), README·시연 캡처
- v0.2~v0.5: 문제 은행 MCP 서버·코드 채점(v0.2), 런타임 문제 생성 제거·세부 항목 단위 사전 생성 풀 270문제 목표(v0.4), 문제 풀 생성 세부 규칙(v0.4.x, §5-2), 단계 S0~S6 통합(v0.5)
