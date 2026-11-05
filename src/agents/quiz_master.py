"""quiz_master: 사전 생성 문제 풀에서 출제·채점 (DESIGN §3-1, §5-3, §5-4).

도구는 MCP 서버(get_quiz, submit_answer)를 감싼 래퍼다.
- user_id는 실행 config(configurable.user_id)에서, quiz_id는 그래프 상태 pending_quiz_id에서 주입한다.
  LLM은 두 값을 고를 수 없다 (다른 사용자 기록·임의 문항 채점 방지).
- get_quiz 결과는 QuizPublic뿐이라 정답·해설은 LLM 컨텍스트와 상태에 들어가지 않는다 (G6).
  상태에는 pending_quiz_id만 Command로 기록한다.
- 런타임에 문제를 만들지 않는다. no_match면 코드가 alternatives의 첫 조합으로 대체 출제한다 (G7).
- 두 도구는 return_direct: 출제 문항(원문 그대로)과 채점 결과(풀의 보기별 해설)를 코드가 서식화해 그대로 사용자에게
  전달한다. LLM이 문항을 고쳐 쓰거나, 채점 뒤에 덧붙인 말로 채점 결과를 덮어쓰는 일을 막는다.
"""
from typing import Optional

from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src import guards, retriever
from src.agents import SubAgentState
from src.llm import get_runtime_llm
from src.quiz_client import QuizBankClient

DEFAULT_USER = "demo_user"
DIRECT_TOOLS = ("get_quiz", "submit_answer")      # 결과 텍스트가 곧 사용자 답변 (src/agent.turn_answer)


def _user(runtime: ToolRuntime) -> str:
    return (runtime.config or {}).get("configurable", {}).get("user_id") or DEFAULT_USER


def _reply(runtime: ToolRuntime, name: str, text: str, **update) -> Command:
    msg = ToolMessage(text, tool_call_id=runtime.tool_call_id, name=name)
    return Command(update={**update, "messages": [msg]})


def scenario_title(n: int) -> str:
    c = retriever.load_chunks().get(f"SC-{n}")
    return c.title.split(": ", 1)[-1] if c else f"SC-{n}"


def format_quiz(q: dict) -> str:
    """QuizPublic → 사용자 표시용. 지문·보기는 원문(영어) 그대로."""
    opts = "\n".join(f"{o['label']}. {o['text']}" for o in q["options"])
    return (f"**연습 문제** ({q['quiz_id']} · {q['task_id']} · 시나리오 {q['scenario_id']}: "
            f"{scenario_title(q['scenario_id'])})\n\n{q['stem']}\n\n{opts}\n\n"
            "답을 A~D 중 하나로 알려 주세요. (문제는 실제 시험과 같은 영어, 해설은 한국어)")


def format_grade(g: dict) -> str:
    head = (f"정답입니다! (정답: {g['answer']})" if g["correct"]
            else f"아쉽지만 오답입니다. (선택: {g['choice']} · 정답: {g['answer']})")
    lines = []
    for label, why in sorted(g["option_rationales"].items()):
        tag = " (정답)" if label == g["answer"] else (" (선택)" if label == g["choice"] else "")
        lines.append(f"- **{label}{tag}**: {why}")
    return f"{head}\n\n**보기별 해설**\n" + "\n".join(lines) + f"\n\n[근거: {g['task_id']}]\n다음 문제를 원하면 말씀해 주세요."


def _scope(task_id: Optional[str], scenario_id: Optional[int], domain: Optional[int]) -> str:
    parts = [f"D{domain}" if domain else "", task_id or "", f"시나리오 {scenario_id}({scenario_title(scenario_id)})" if scenario_id else ""]
    return " × ".join(p for p in parts if p) or "요청한 범위"


def make_quiz_tools(bank: QuizBankClient) -> list:
    async def issue(runtime: ToolRuntime, args: dict, prefix: str = "") -> Command:
        res = await bank.call("get_quiz", {"user_id": _user(runtime), **args})
        event = {"args": {k: v for k, v in args.items() if v is not None}}
        if res.get("status") == "ok":
            q = res["quiz"]
            chunk = retriever.load_chunks().get(q["task_id"])
            await adispatch_custom_event("get_quiz", {**event, "status": "ok", "quiz_id": q["quiz_id"],
                                                      "task_id": q["task_id"], "scenario_id": q["scenario_id"],
                                                      "context": {"doc_id": chunk.doc_id, "text": chunk.text} if chunk else None})
            return _reply(runtime, "get_quiz", prefix + format_quiz(q), pending_quiz_id=q["quiz_id"])

        if res.get("isError"):
            await adispatch_custom_event("get_quiz", {**event, "status": "error", "reason": res.get("message")})
            return _reply(runtime, "get_quiz", f"문제를 꺼낼 수 없습니다: {res.get('message')}\n"
                                               "도메인(1~5), TS 번호(예: TS-1.4), 시나리오(1~6)로 다시 요청해 주세요.")

        # no_match: 문제를 만들지 않고 풀에 있는 첫 대체 조합으로 낸다
        alts = res.get("alternatives") or []
        await adispatch_custom_event("get_quiz", {**event, "status": "no_match", "reason": res.get("reason"),
                                                  "alternatives": len(alts)})
        why = ("의 검토된 문제는 아직 풀에 없습니다" if res.get("reason") == "not_in_pool"
               else "의 문제는 이미 모두 풀었습니다")
        notice = f"요청한 조합({_scope(args.get('task_id'), args.get('scenario_id'), args.get('domain'))}){why}. 문제를 새로 만들지는 않습니다."
        if prefix or not alts:
            return _reply(runtime, "get_quiz", notice + " 다른 도메인이나 TS를 골라 주세요.")
        a = alts[0]
        others = ", ".join(f"{x['task_id']}·시나리오 {x['scenario_id']}" for x in alts[1:4])
        lead = (f"{notice}\n대신 같은 범위의 {a['task_id']} · 시나리오 {a['scenario_id']}({scenario_title(a['scenario_id'])}) "
                f"문제를 드립니다." + (f" (다른 대안: {others})" if others else "") + "\n\n")
        return await issue(runtime, {"task_id": a["task_id"], "scenario_id": a["scenario_id"]}, prefix=lead)

    @tool(return_direct=True)
    async def get_quiz(runtime: ToolRuntime, domain: Optional[int] = None, task_id: Optional[str] = None,
                       scenario_id: Optional[int] = None) -> Command:
        """검토를 통과한 사전 생성 문제 풀에서 연습 문제 1개를 정답 없이 꺼내 사용자에게 그대로 보여 준다. 문제를 새로 만들지 않는다.

        언제 쓰나: 사용자가 연습 문제·퀴즈를 요청할 때. 언제 쓰지 않나: 개념 설명, 답 제출(submit_answer).
        입력(모두 선택): domain 1~5 ("도메인 1" → 1), task_id "TS-1.4", scenario_id 1~6
          (1 고객 지원 에이전트, 2 Claude Code 코드 생성, 3 멀티 에이전트 리서치, 4 개발자 생산성,
           5 CI(지속적 통합)용 Claude Code, 6 구조화 데이터 추출)
        풀에 없는 조합이면 도구가 그 사실을 밝히고 같은 범위의 대체 문제를 낸다.
        예: get_quiz(domain=1) / get_quiz(task_id="TS-1.7", scenario_id=5)
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        return await issue(runtime, {"domain": domain, "task_id": task_id, "scenario_id": scenario_id})

    @tool(return_direct=True)
    async def submit_answer(runtime: ToolRuntime, choice: str) -> Command:
        """직전에 출제한 문제에 대한 사용자의 답(A~D)을 코드로 채점하고 풀이 기록에 저장한 뒤, 정오와 보기별 해설을 그대로 보여 준다.

        언제 쓰나: 사용자가 출제된 문제의 답을 말했을 때만 ("답은 B", "C요", "b"). 채점할 문제는 대화 상태에서 자동으로 정해진다.
        언제 쓰지 않나: 문제를 아직 내지 않았을 때, 정답을 먼저 알려 달라는 요청(채점 전에는 공개하지 않음).
        입력: choice "A" | "B" | "C" | "D"
        """
        await guards.wait_input_ok()   # 입력 가드 LLM 통과 전에는 실행하지 않음
        quiz_id = (runtime.state or {}).get("pending_quiz_id")
        if not quiz_id:
            await adispatch_custom_event("submit", {"status": "no_pending"})
            return _reply(runtime, "submit_answer", "채점할 문제가 없습니다. 먼저 \"문제 하나 내줘\"처럼 문제를 요청해 주세요.")
        res = await bank.call("submit_answer", {"user_id": _user(runtime), "quiz_id": quiz_id, "choice": choice})
        if res.get("status") == "graded":
            await adispatch_custom_event("submit", {"status": "graded", "quiz_id": quiz_id, "choice": res["choice"],
                                                    "correct": res["correct"]})
            return _reply(runtime, "submit_answer", format_grade(res), pending_quiz_id=None)
        await adispatch_custom_event("submit", {"status": "error", "quiz_id": quiz_id, "message": res.get("message")})
        return _reply(runtime, "submit_answer", f"채점할 수 없습니다: {res.get('message')}\nA·B·C·D 중 하나로 답해 주세요.")

    return [get_quiz, submit_answer]


PROMPT = """너는 CCA-F 연습 문제 출제·채점 담당이다. 문제는 사전에 검토된 문제 풀에서만 꺼내고, 절대 새로 만들지 않는다.

- 문제 요청이면 get_quiz를 호출한다. 도메인·TS 번호·시나리오가 언급되면 인자로 넘긴다
  (예: "CI 파이프라인 시나리오" → scenario_id=5, "fork_session" 같은 개념이 TS-1.7이면 task_id="TS-1.7").
- 사용자가 출제된 문제의 답(A~D)을 말하면 submit_answer(choice)를 호출한다. 문제 번호는 묻지 않는다.
- 두 도구의 결과는 그대로 사용자에게 전달되므로 도구를 부른 뒤 따로 설명을 덧붙이지 않는다.
- 너는 정답을 모른다. 채점 전에 정답·힌트를 달라고 하면 먼저 A~D 중 하나를 고르라고 안내한다(도구 호출 없이).
- 실제 시험 문항이라는 표현을 쓰지 않는다."""


def build_quiz_master(bank: QuizBankClient):
    return create_agent(get_runtime_llm(), make_quiz_tools(bank), system_prompt=PROMPT,
                        state_schema=SubAgentState, name="quiz_master")
