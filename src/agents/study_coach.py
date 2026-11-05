"""study_coach: 진도·약점·학습 계획 (DESIGN §3-1, §5-5).

도구 4개:
- get_progress, get_weak_domains: MCP 래퍼. user_id는 실행 config(configurable.user_id)에서 주입 (LLM이 고를 수 없음)
- allocate_study_time(days, hours_per_day): 비중(config)·정답률(study_log)은 래퍼가 코드로 가져와 결정적으로 계산.
  LLM에게 정답률 인자를 받지 않는다 (기록을 지어낼 수 없음)
- make_study_plan(days, hours_per_day): Plan-Execute 서브그래프(src/planner.py). return_direct — 코드가 서식화한 계획
  (약점 근거·배분 합계·일자별 계획·Exercise)을 그대로 답변으로 쓴다 (agent.turn_answer)
"""
import json

from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.callbacks.manager import adispatch_custom_event

from src import guards, planner
from src.agents import SubAgentState
from src.llm import get_runtime_llm
from src.quiz_client import QuizBankClient

DEFAULT_USER = "demo_user"
DIRECT_TOOLS = ("make_study_plan",)


def _user(runtime: ToolRuntime) -> str:
    return (runtime.config or {}).get("configurable", {}).get("user_id") or DEFAULT_USER


def _err(res: dict) -> str:
    return f"기록을 조회할 수 없습니다: {res.get('message')}"


def make_coach_tools(bank: QuizBankClient) -> list:
    @tool
    async def get_progress(runtime: ToolRuntime) -> str:
        """현재 사용자의 풀이 진도를 코드로 집계해 돌려준다: 도메인별 시도·정답·정답률, 세부 항목 커버리지(푼 항목 / 240),
        TS별 시도·정답·남은 문제 수. 사용자는 대화 설정에서 자동으로 정해진다.

        언제 쓰나: "내 진도 어때?", "몇 문제 풀었어?", "어느 TS를 아직 안 풀었어?"
        언제 쓰지 않나: 약점 순위(get_weak_domains), 학습 계획(make_study_plan), 문제 출제·채점.
        입력: 없음. 예: get_progress()
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        res = await bank.call("get_progress", {"user_id": _user(runtime)})
        await adispatch_custom_event("progress", {"tool": "get_progress", "status": res.get("status", "error"),
                                                  "attempts": res.get("attempts")})
        return _err(res) if res.get("isError") else json.dumps(res, ensure_ascii=False)

    @tool
    async def get_weak_domains(runtime: ToolRuntime, top_k: int = 2) -> str:
        """현재 사용자의 약점 도메인을 점수 순으로 돌려준다. 점수 = 시험 비중 × (1 − 정답률) (코드 계산).
        시도 3회 미만 도메인은 insufficient(데이터 부족)로 따로 준다.

        언제 쓰나: "내 약점이 뭐야?", 약점 기준 학습 계획을 짜기 전 (계획 요청이면 가장 먼저 호출).
        언제 쓰지 않나: 전체 진도(get_progress), 문제 출제·채점.
        입력: top_k 1~5 (기본 2). 예: get_weak_domains(top_k=2)
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        res = await bank.call("get_weak_domains", {"user_id": _user(runtime), "top_k": top_k})
        await adispatch_custom_event("progress", {"tool": "get_weak_domains", "status": res.get("status", "error"),
                                                  "top": [f"D{r['domain']}" for r in res.get("top", [])]})
        return _err(res) if res.get("isError") else json.dumps(res, ensure_ascii=False)

    async def _allocation(runtime: ToolRuntime, days: int, hours_per_day: float):
        if err := planner.validate_request(days, hours_per_day):
            return None, None, err
        prog = await bank.call("get_progress", {"user_id": _user(runtime)})
        if prog.get("isError"):
            return None, None, _err(prog)
        return planner.allocate_study_time(days, hours_per_day, prog["domains"]), prog, None

    @tool
    async def allocate_study_time(runtime: ToolRuntime, days: int, hours_per_day: float) -> str:
        """남은 일수 × 하루 공부 시간을 도메인별로 배분한다 (코드 계산, 합계는 항상 총시간과 같음).
        몫 ∝ 시험 비중 × 약점 계수(풀이 기록 정답률에서 계산). 비중·정답률은 도구가 직접 조회한다.

        언제 쓰나: 학습 계획 요청에서 get_weak_domains 다음, make_study_plan 전에. "시간을 어떻게 나눠야 해?"
        언제 쓰지 않나: 일수나 하루 시간을 모를 때(먼저 사용자에게 묻는다).
        입력: days 1~30 (정수), hours_per_day 0.5~12 (0.5 단위). 예: allocate_study_time(days=10, hours_per_day=2)
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        alloc, _, err = await _allocation(runtime, days, hours_per_day)
        if err:
            return err
        await adispatch_custom_event("allocate", {"total_hours": alloc.total_hours,
                                                  "hours": {f"D{a.domain}": a.hours for a in alloc.domains}})
        return alloc.model_dump_json()

    @tool(return_direct=True)
    async def make_study_plan(runtime: ToolRuntime, days: int, hours_per_day: float) -> str:
        """약점·시간 배분을 반영한 일자별 학습 계획을 만들어 그대로 사용자에게 보여 준다
        (Plan-Execute: 계획 → 검증 → 수정 → 서식화. 약점 근거표, 도메인별 시간 합계, 일자별 TS, 연결 Exercise 포함).

        언제 쓰나: 학습 계획 요청에서 get_weak_domains, allocate_study_time 다음 마지막 단계로.
        언제 쓰지 않나: 일수나 하루 시간을 모를 때(먼저 묻는다), 진도만 물을 때.
        입력: days 1~30 (정수), hours_per_day 0.5~12 (0.5 단위). 예: make_study_plan(days=10, hours_per_day=2)
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        alloc, prog, err = await _allocation(runtime, days, hours_per_day)
        if err:
            return f"학습 계획을 만들 수 없습니다: {err}"
        weak = await bank.call("get_weak_domains", {"user_id": _user(runtime), "top_k": 2})
        if weak.get("isError"):
            return _err(weak)
        text, _ = await planner.make_study_plan(alloc, prog, weak)
        return text

    return [get_progress, get_weak_domains, allocate_study_time, make_study_plan]


PROMPT = """너는 CCA-F 학습 코치(비공식 학습 도구)의 진도·약점·학습 계획 담당이다. 모든 수치는 도구 결과만 쓴다.

- 학습 계획 요청: get_weak_domains → allocate_study_time(days, hours_per_day) → make_study_plan(days, hours_per_day)
  순서로 호출한다. make_study_plan 결과는 그대로 사용자에게 전달되므로 그 뒤에 덧붙이지 않는다.
  "10일 남았고 하루 2시간" → days=10, hours_per_day=2. "2주" → days=14. "하루 90분" → hours_per_day=1.5.
- 이번 요청에 없더라도 대화 앞부분(이전 대화 요약의 [핵심 사실 — case_facts] 블록 포함)에 사용자가 말한
  남은 일수·하루 공부 시간이 있으면 그 값을 쓰고 다시 묻지 않는다.
- 남은 일수나 하루 공부 시간을 어디에서도 알 수 없으면 도구를 부르지 말고 둘 다 물어본다 (시험일을 지어내지 않는다).
- 진도 질문: get_progress. 약점 질문: get_weak_domains. 결과를 한국어로 짧게 정리하고, 정답률·시도 수는 도구 값 그대로 쓴다.
  시도 3회 미만 도메인은 "기록 부족"이라고 말한다. 기록이 없으면 먼저 연습 문제를 풀어 보라고 안내한다.
- 정답률이나 시간 배분을 직접 계산하거나 추정하지 않는다. 합격을 보장하는 표현을 쓰지 않는다."""


def build_study_coach(bank: QuizBankClient):
    return create_agent(get_runtime_llm(), make_coach_tools(bank), system_prompt=PROMPT,
                        state_schema=SubAgentState, name="study_coach")
