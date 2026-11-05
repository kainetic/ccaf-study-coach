"""Supervisor 그래프 조립 (DESIGN §3). Day 6 final_scenario.py build_app 구조.

MCP 도구를 비동기로 쓰므로 build_app은 async이고 실행은 ainvoke. 사용이 끝나면 close()로 MCP 세션을 닫는다.
에이전트: concept_tutor(search_guide, get_chunk) · quiz_master(get_quiz, submit_answer)
         · study_coach(get_progress, get_weak_domains, allocate_study_time, make_study_plan) · general_agent.
한 턴 실행은 CoachApp.run: rule_check(G1·G2·G4) → [llm_guard(G1·G2) ∥ 선행 검색 ∥ Supervisor 그래프, 도구는 gate 대기]
  → turn_answer → output_check(G4 백스톱).
G3는 concept_tutor 안(TutorAnswerGuard), G5는 Supervisor 배분 + general_agent.
긴 thread는 CoachApp.run이 그래프 시작 전에 요약(memory.compact)하고 case_facts를 보존한다.
하위 에이전트가 끝나면 Supervisor로 돌아가지 않고 끝낸다(after_agent). 복합 요청(개념 + 출제)만 Supervisor로 돌아간다.
"""
import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import NotRequired, Optional

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph_supervisor import create_supervisor

from src import config, guards, memory, retriever
from src.agents import last_question, this_turn
from src.agents.concept_tutor import build_concept_tutor
from src.agents.general import build_general_agent
from src.agents.quiz_master import DIRECT_TOOLS as QUIZ_DIRECT, build_quiz_master
from src.agents.study_coach import DIRECT_TOOLS as COACH_DIRECT, build_study_coach
from src.llm import get_runtime_llm
from src.quiz_client import QuizBankClient

AGENTS = ("concept_tutor", "quiz_master", "study_coach", "general_agent")
DIRECT_TOOLS = QUIZ_DIRECT + COACH_DIRECT       # 결과 텍스트가 곧 사용자 답변
RECURSION_LIMIT = 30


class CoachState(AgentState):
    """Supervisor 상태. 정답은 두지 않고 출제 중인 문제 id만 둔다 (CLAUDE.md 규칙)."""
    pending_quiz_id: NotRequired[Optional[str]]
    case_facts: NotRequired[dict]          # 요약에 묻히면 안 되는 사실 (memory.extract_facts, TS 5.1)


SUPERVISOR_PROMPT = """너는 CCA-F 학습 코치(비공식 학습 도구)의 Supervisor다. 직접 답하지 말고 담당 에이전트에게 맡긴다.

[배분 기준]
- concept_tutor: CCA-F 시험에 관한 모든 사실 질문(형식·문항 수·점수·합격 기준·합격률·응시 방법·접수·비용·출제 범위·
  도메인 비중)과 Claude·에이전트·프롬프트·도구·MCP·Claude Code 설계 개념, 방식 비교("A랑 B 중 뭐가 나아?"),
  샘플 문항 해설. 가이드에 없을 것 같은 질문도 concept_tutor로 보낸다 (가이드를 검색해 "가이드에 없음"을 근거와 함께 답한다)
- quiz_master: 연습 문제·퀴즈 요청, 그리고 출제된 문제에 대한 답 제출("답은 B", "C", "b요")
- study_coach: 내 진도·정답률·약점 확인, 남은 기간에 맞춘 학습 계획·시간 배분
- general_agent: 인사, 이 도구의 기능 안내, 그리고 Claude·AI·시험과 명백히 무관한 주제(날씨, 요리, 주식 등)만
- 시험이나 Claude 개발과 조금이라도 관련 있으면 general_agent가 아니다. 애매하면 concept_tutor로 보낸다

[배분 예시]
- "응시료 얼마야?" → concept_tutor (시험 사실 질문)
- "시험 결과는 어떻게 알려줘?" → concept_tutor (시험 사실 질문)
- "tool description이랑 system prompt 중 어디에 규칙을 넣는 게 좋아?" → concept_tutor (설계 비교)
- "안녕, 뭐 할 수 있어?" → general_agent
- "오늘 날씨 어때?" → general_agent (무관한 주제)
- "내 약점 어디야?" → study_coach
- "TS 2.3 문제 하나 내줘" → quiz_master

[규칙]
- 한 번에 한 에이전트에게만 넘긴다 (동시 호출 금지). 같은 에이전트를 같은 요청으로 두 번 부르지 않는다.
- 개념 질문과 문제 요청이 섞이면 concept_tutor → (돌아온 뒤) quiz_master 순서로 둘 다 처리한다.
- 각 에이전트에게 넘길 때 필요한 정보(TS 번호, 도메인, 시나리오)를 빠뜨리지 않는다.
- 문제를 직접 만들거나 정답을 말하지 않는다.
- 필요한 에이전트 처리가 모두 끝나면 하위 답변을 반복하거나 요약하지 말고 "완료" 한 단어만 출력한다.
  (사용자에게는 하위 에이전트의 답변이 그대로 전달된다)"""


def single_handoff(state: dict) -> dict:
    """post_model_hook: Supervisor가 핸드오프를 한 번에 여러 개 내면 첫 번째만 남긴다 (프로그램적 강제, TS 1.4).
    Bedrock Converse는 parallel_tool_calls 끄기를 지원하지 않아 프롬프트만으로는 동시 핸드오프를 막을 수 없다.
    남은 요청은 첫 에이전트가 돌아온 뒤 Supervisor가 다음 턴에 이어서 넘긴다."""
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or len(last.tool_calls) <= 1:
        return {}
    first = last.tool_calls[0]
    trimmed = AIMessage(content=config.get_text(last), tool_calls=[first], id=last.id, name=last.name,
                        response_metadata=last.response_metadata, usage_metadata=last.usage_metadata)
    return {"messages": [trimmed]}


# 출제 요청 표현. 하위 에이전트가 끝난 뒤 이 요청이 남아 있으면 Supervisor로 돌아가 quiz_master에게 넘긴다.
QUIZ_INTENT = re.compile(r"(문제|퀴즈|문항|quiz).{0,12}(내|줘|출제|풀어|하나|더)", re.I)


def after_agent(state: dict) -> str:
    """하위 에이전트가 돌아온 뒤: 남은 일이 없으면 Supervisor를 다시 부르지 않고 끝낸다 (v2-7, 마무리 LLM 호출 생략).
    Supervisor의 마무리 답("완료")은 원래 답변에 쓰지 않았다(turn_answer). 질문에 출제 요청이 있는데 이번 턴에
    quiz_master가 아직 돌지 않았으면(개념 + 출제 복합 요청, 평가 13번) Supervisor로 돌아간다."""
    messages = state["messages"]
    ran = {m.name for m in this_turn(messages) if isinstance(m, AIMessage) and m.name in AGENTS}
    if "quiz_master" not in ran and QUIZ_INTENT.search(last_question(messages)):
        return "supervisor"
    return END


@dataclass
class CoachApp:
    graph: object
    bank: QuizBankClient

    async def close(self) -> None:
        await self.bank.close()

    async def run(self, question: str, user_id: str, thread_id: str, tracer) -> str:
        """한 턴: 입력 가드 → 그래프 → 답변 조립 → 출력 검사.
        - 1차 규칙에 걸리면 그래프를 부르지 않는다 (도구 호출 0건).
        - 규칙을 통과하면 2차 LLM 판별과 그래프를 동시에 시작한다 (응답 시간 단축). 앱 도구는 guards.wait_input_ok()에서
          판별 통과를 기다리므로, LLM이 차단하면 도구는 실행되지 않고 그래프를 취소한 뒤 이번 턴 메시지를 되돌린다."""
        d = guards.rule_check(question)
        if d.action != "pass":
            guards.audit(d)
            tracer.add_step({"step": "guard", "action": d.action, "rule": d.rule, "layer": d.layer})
            return guards.guard_reply(d)
        cfg = {"configurable": {"thread_id": thread_id, "user_id": user_id},
               "callbacks": [tracer], "recursion_limit": RECURSION_LIMIT}
        before = await self.graph.aget_state(cfg)
        prev = before.values or {}
        # 요약에 묻히면 안 되는 사실은 코드가 따로 보존한다 (memory.extract_facts, TS 5.1)
        facts = memory.extract_facts(question, prev.get("case_facts"))
        # 출제 대기 중의 답 입력("B")은 LLM 판별을 건너뛰고, 나머지는 판별·선행 검색을 그래프와 동시에 시작한다 (v2-7)
        answer_only = bool(prev.get("pending_quiz_id")) and guards.is_answer_only(question)
        guard_task = asyncio.create_task(guards.answer_only_pass() if answer_only else guards.llm_guard(question))
        prefetch = None if answer_only else retriever.start_prefetch(question, callbacks=[tracer])
        graph_task = None
        try:
            # 긴 thread는 그래프 시작 전에 요약 (요약 LLM은 입력 가드 판별과 동시에 돈다)
            compacted = await memory.compact(prev.get("messages", []), facts)
            if compacted:
                new_msgs, info = compacted
                if new_msgs:
                    await self.graph.aupdate_state(
                        cfg, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_msgs]}, as_node="supervisor")
                tracer.add_step({"step": "summarize", **info})
            gate = asyncio.get_running_loop().create_future()
            token = guards.INPUT_GATE.set(gate)
            token_pre = retriever.PREFETCH.set(prefetch)
            try:
                graph_task = asyncio.create_task(
                    self.graph.ainvoke({"messages": [HumanMessage(question)], "case_facts": facts}, cfg))
            finally:
                retriever.PREFETCH.reset(token_pre)
                guards.INPUT_GATE.reset(token)
            d = await guard_task
        except BaseException:          # run 자체가 취소되거나 요약 적용이 실패하면 남은 task를 정리한다
            guard_task.cancel()
            if graph_task is not None:
                graph_task.cancel()
            if prefetch is not None:
                prefetch.task.cancel()
            raise
        tracer.add_step({"step": "guard", "action": d.action, "rule": d.rule, "layer": d.layer})
        if d.action != "pass":
            graph_task.cancel()
            if prefetch is not None:
                prefetch.task.cancel()
            with contextlib.suppress(BaseException):
                await graph_task
            await self._rollback(cfg, before)
            tracer.discard_graph_run()
            return guards.guard_reply(d)
        gate.set_result(True)
        try:
            result = await graph_task
        finally:
            if prefetch is not None and not prefetch.task.done():
                prefetch.task.cancel()      # 개념 질문이 아니어서 쓰지 않은 선행 검색
        answer, report = guards.output_check(turn_answer(result["messages"]))
        tracer.add_step({"step": "output_check", **report})
        return answer

    async def _rollback(self, cfg: dict, before) -> None:
        """차단된 턴의 흔적을 되돌린다: messages와 case_facts를 차단 전 그대로 (다음 턴이 차단된 입력을 보지 않게).
        턴 시작 요약(memory.compact)이 이미 messages를 바꿨을 수 있으므로 id 비교가 아니라 통째로 복원한다.
        도구는 실행되지 않았으므로 pending_quiz_id는 그대로다."""
        prev = before.values or {}
        prev_msgs = prev.get("messages", [])
        now = (await self.graph.aget_state(cfg)).values or {}
        if [m.id for m in now.get("messages", [])] == [m.id for m in prev_msgs] \
                and now.get("case_facts") == prev.get("case_facts"):
            return
        await self.graph.aupdate_state(
            cfg, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *prev_msgs],
                  "case_facts": prev.get("case_facts") or {}},
            as_node="supervisor")


async def build_app() -> CoachApp:
    bank = await QuizBankClient().start()
    supervisor = create_supervisor(
        [build_concept_tutor(), build_quiz_master(bank), build_study_coach(bank), build_general_agent()],
        model=get_runtime_llm(),
        prompt=SUPERVISOR_PROMPT,
        state_schema=CoachState,
        output_mode="last_message",
        post_model_hook=single_handoff,
    )
    # 하위 에이전트 → Supervisor 고정 엣지를 after_agent 분기로 바꾼다 (create_supervisor는 미컴파일 builder를 돌려줌)
    for name in AGENTS:
        supervisor.edges.discard((name, "supervisor"))
        supervisor.add_conditional_edges(name, after_agent, ["supervisor", END])
    return CoachApp(supervisor.compile(checkpointer=InMemorySaver()), bank)


def turn_answer(messages: list) -> str:
    """이번 턴(마지막 사용자 메시지 이후)의 하위 에이전트 답변을 순서대로 이어 붙인다.
    - 하위 에이전트의 최종 텍스트(도구 호출 없는 AIMessage)
    - return_direct 도구 결과(출제 문항·채점 결과·학습 계획, 코드가 서식화)
    Supervisor의 마지막 메시지는 쓰지 않는다 (재작성 방지). 하위 답변이 없으면 마지막 메시지."""
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    parts = []
    for m in messages[start + 1:]:
        text = config.get_text(m).strip()
        if not text:
            continue
        if isinstance(m, AIMessage) and m.name in AGENTS and not m.tool_calls:
            parts.append(text)
        elif isinstance(m, ToolMessage) and m.name in DIRECT_TOOLS:
            parts.append(text)
    return "\n\n".join(parts) if parts else config.get_text(messages[-1]).strip()
