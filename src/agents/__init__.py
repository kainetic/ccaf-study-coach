"""하위 에이전트 (DESIGN §3-1). 에이전트당 도구 4개 이하."""
from typing import NotRequired, Optional

from langchain.agents import AgentState
from langchain_core.messages import HumanMessage


class SubAgentState(AgentState):
    """하위 에이전트 상태. pending_quiz_id만 Supervisor 상태(CoachState)와 공유한다. 정답은 두지 않는다."""
    pending_quiz_id: NotRequired[Optional[str]]


def this_turn(messages: list) -> list:
    """마지막 사용자 메시지 이후(이번 턴)의 메시지."""
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[start + 1:]


def last_question(messages: list) -> str:
    """이번 턴의 사용자 질문 원문 (없으면 빈 문자열)."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""
