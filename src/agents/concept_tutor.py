"""concept_tutor: 개념 설명·샘플 문항 해설·범위 안내 (DESIGN §3-1, §5-1).

S3(P1): TutorAnswer 구조화 출력(ToolStrategy) + G3.
- 모델은 search_guide/get_chunk로 근거를 가져온 뒤 TutorAnswer(answer_ko, claims, in_exam_scope, coverage_gap)로 답한다.
- TutorAnswerGuard(after_agent)가 이번 턴에 실제로 가져온 doc_id를 가리키는 claim만 유효로 보고(src/guards.check_tutor_answer),
  유효 claim이 0개면 답변 대신 "가이드 근거 없음" 고정 문구를 쓴다. [근거: …] 표기도 코드가 붙인다.
- 결과는 name="concept_tutor"인 AIMessage로 마지막에 추가 → Supervisor(last_message)와 agent.turn_answer가 이 메시지를 쓴다.
"""
import re
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import config, retriever
from src.agents import SubAgentState
from src.guards import check_tutor_answer
from src.llm import get_runtime_llm
from src.schemas import TutorAnswer
from src.tools import get_chunk, search_guide

NAME = "concept_tutor"
SOURCE_TOOLS = ("search_guide", "get_chunk")
DOC_HEADER = re.compile(r"^\[([A-Za-z0-9.\-]+)\] ", re.MULTILINE)

PROMPT = """너는 Claude Certified Architect – Foundations(CCA-F) 시험 공부를 돕는 개념 튜터다. (비공식 학습 도구)

[순서]
1. 먼저 search_guide로 공식 시험 가이드를 검색한다. 질문이 문서 번호(TS 1.4, 샘플 1번, 도메인 3, Exercise 2,
   시험 형식·범위)를 가리키면 get_chunk(doc_id)로 그 청크를 바로 가져와도 된다. 검색은 보통 1회, 많아야 2회.
2. 검색 결과를 받은 뒤에 TutorAnswer로 답한다. 검색 도구와 TutorAnswer를 같은 차례에 함께 호출하지 않는다.

[TutorAnswer 작성 규칙]
- answer_ko: 한국어, 핵심 용어 영어 병기(예: 프로그램적 강제(programmatic enforcement)). 결론 먼저, 필요하면 짧은 목록.
  [근거: …] 표기는 쓰지 않는다(코드가 claims로 붙인다). 검색 과정("검색해 보겠습니다")을 쓰지 않는다.
- claims: 답변의 핵심 주장마다 근거가 된 검색 결과의 doc_id(결과 머리의 [doc_id] 그대로)와 그 원문에서 그대로 옮긴
  짧은 영어 구간(excerpt). 검색 결과에 없는 내용은 claim으로 만들지 않는다.
- 검색 결과에 근거가 없으면 지어내지 않는다. 질문 중 가이드에 없는 부분은 coverage_gap에 적고(예: "합격률·평균 점수"),
  가이드로 확인 가능한 사실만 answer_ko와 claims에 쓴다. 수치·링크·합격률을 추정하지 않는다.
- in_exam_scope: 시험 범위 질문은 SCOPE-in / SCOPE-out 청크를 근거로 in·out을 정한다. 판단이 어려우면 unclear.
- 샘플 문항(SQ-xx) 해설은 sample_question 청크의 정답과 해설을 그대로 따른다.
- 질문에 연습 문제 출제·채점 요청이 섞여 있으면 개념 부분만 답하고, 문제 요청에 대해서는 한 마디도 쓰지 않는다
  (담당자 안내·"별도로 요청하라"는 말 금지. 문제는 다른 담당이 같은 답변 뒤에 이어서 낸다)."""


def _this_turn(messages: list) -> list:
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[start + 1:]


def retrieved_docs(messages: list) -> dict[str, str]:
    """이번 턴에 search_guide/get_chunk가 돌려준 doc_id → 청크 원문."""
    chunks = retriever.load_chunks()
    out: dict[str, str] = {}
    for m in _this_turn(messages):
        if isinstance(m, ToolMessage) and m.name in SOURCE_TOOLS:
            for did in DOC_HEADER.findall(config.get_text(m)):
                if did in chunks:
                    out[did] = chunks[did].text
    return out


class TutorAnswerGuard(AgentMiddleware):
    """after_agent: TutorAnswer → G3 검증 → 사용자 답변 AIMessage."""

    async def aafter_agent(self, state: dict, runtime: Any) -> dict | None:
        text, report = check_tutor_answer(state.get("structured_response"), retrieved_docs(state["messages"]))
        await adispatch_custom_event("output_check", report)
        return {"messages": [AIMessage(content=text, name=NAME)]}

    def after_agent(self, state: dict, runtime: Any) -> dict | None:   # 동기 실행 경로 (테스트용)
        text, _ = check_tutor_answer(state.get("structured_response"), retrieved_docs(state["messages"]))
        return {"messages": [AIMessage(content=text, name=NAME)]}


def build_concept_tutor():
    return create_agent(get_runtime_llm(), [search_guide, get_chunk], system_prompt=PROMPT,
                        response_format=ToolStrategy(TutorAnswer), middleware=[TutorAnswerGuard()],
                        state_schema=SubAgentState, name=NAME)
