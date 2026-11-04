"""general_agent: 인사·기능 안내·범위 밖 질문 응대 (Day 6 general_agent 재사용, G5). 도구 없음."""
from langchain.agents import create_agent

from src.agents import SubAgentState
from src.guards import OFF_TOPIC
from src.llm import get_runtime_llm

PROMPT = f"""너는 CCA-F 학습 코치(비공식 학습 도구)의 일반 응대 담당이다.
인사와 잡담에는 짧고 친절하게 답하고, 할 수 있는 일을 안내한다:
1) 시험 개념 질문(가이드 근거 표기) 2) 사전 검토된 문제 풀에서 연습 문제 출제와 채점 3) 진도·약점 확인과 학습 계획.
Anthropic 공식 튜터가 아니며 합격을 보장하지 않는다.
CCA-F 시험·Claude 학습과 무관한 질문에는 답하지 말고 아래 문장만 그대로 출력한다:
{OFF_TOPIC}"""


def build_general_agent():
    return create_agent(get_runtime_llm(), [], system_prompt=PROMPT, state_schema=SubAgentState, name="general_agent")
