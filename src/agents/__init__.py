"""하위 에이전트 (DESIGN §3-1). 에이전트당 도구 4개 이하."""
from typing import NotRequired, Optional

from langchain.agents import AgentState


class SubAgentState(AgentState):
    """하위 에이전트 상태. pending_quiz_id만 Supervisor 상태(CoachState)와 공유한다. 정답은 두지 않는다."""
    pending_quiz_id: NotRequired[Optional[str]]
