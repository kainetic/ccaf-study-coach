"""장기 대화 요약 + 핵심 사실 보존 (DESIGN §4·§5-1, TS 5.1, v2-3).

- case_facts: 요약에 묻히면 안 되는 사실(남은 일수, 하루 공부 시간, 관심 도메인·TS)을 사용자 입력에서
  정규식으로 뽑아 그래프 상태(CoachState.case_facts)에 따로 둔다. LLM 요약 품질과 상관없이 코드가 보존한다.
- compact: CoachApp.run이 턴 시작 전에 부른다. thread 메시지가 config.SUMMARY_TRIGGER_MESSAGES를 넘으면
  최근 SUMMARY_KEEP_TURNS개 턴은 그대로 두고 그 앞을 LLM 1회로 요약해 SystemMessage(요약 + case_facts 블록) 하나로
  바꾸고, run이 바깥 그래프 상태에 aupdate_state로 적용한다. (Supervisor pre_model_hook에서 지우면 내부 그래프에만
  적용되고 바깥 thread에는 옛 메시지가 남는다 — 그래서 그래프 밖에서 한다.) 요약 LLM은 입력 가드 판별과 동시에 돈다.
  하위 에이전트도 같은 messages를 받으므로 study_coach가 앞에서 들은 일수·시간을 다시 묻지 않는다. 자르는 지점은 사용자 입력 경계라 도구 호출 쌍이 끊기지 않는다.
- 요약 LLM이 실패하면 원래 messages를 그대로 쓴다 (대화는 계속).
"""
import re
from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from src import config

SUMMARY_HEADER = "[이전 대화 요약]"
FACTS_HEADER = "[핵심 사실 — case_facts, 코드 보존]"
MAX_LINE = 600                       # 요약 입력에서 메시지 하나당 최대 글자 수
MAX_FOCUS = 5                        # 관심 도메인·TS는 최근 것부터 이 개수까지

SUMMARY_PROMPT = """다음은 CCA-F 시험 학습 코치와 사용자의 이전 대화다. 다음 대화에 필요한 내용만 한국어 10줄 이내로 요약하라.
- 사용자가 물은 주제, 풀었던 문제의 TS·정오, 받은 학습 계획의 요지를 남긴다.
- 대화에 없는 수치·사실을 새로 만들지 않는다. 채점 전인 문제의 정답을 추측해 쓰지 않는다.
- 남은 일수·하루 공부 시간 같은 사실은 별도로 보존되므로 요약에서 빠져도 된다.

[대화]
{dialog}"""

# ---------------------------------------------------------------- case_facts (정규식, 코드)

DAYS = [
    (re.compile(r"일주일"), lambda m: 7),
    (re.compile(r"(\d{1,2})\s*주\s*(?:일\s*)?(?:남|뒤|후|동안|간|이내|안)"), lambda m: int(m[1]) * 7),
    (re.compile(r"(\d{1,3})\s*일\s*(?:남|뒤|후|동안|간|이내|안|정도)"), lambda m: int(m[1])),
]
HOURS = re.compile(r"(?:하루\s*(?:에\s*)?|매일\s*)(\d{1,2}(?:\.\d)?)\s*시간|(\d{1,2}(?:\.\d)?)\s*시간\s*씩")
DOMAIN = re.compile(r"(?:도메인|domain)\s*-?\s*([1-5])(?!\s*\.\s*\d)|\bD([1-5])\b", re.I)
TASK = re.compile(r"(?:\bTS|task\s*statement)\s*-?\s*([1-5])\s*\.\s*([1-7])", re.I)


def _recent_unique(prev: list, new: list) -> list:
    out = [x for x in prev if x not in new] + new
    return out[-MAX_FOCUS:]


def extract_facts(text: str, prev: Optional[dict] = None) -> dict:
    """사용자 입력 한 개에서 사실을 뽑아 이전 값과 합친다. 일수·시간은 새 값이 덮어쓰고, 도메인·TS는 최근 순으로 쌓는다."""
    facts = dict(prev or {})
    for pat, conv in DAYS:
        if m := pat.search(text):
            facts["days"] = conv(m)
            break
    if m := HOURS.search(text):
        facts["hours_per_day"] = float(m[1] or m[2])
    doms = [int(a or b) for a, b in DOMAIN.findall(text)]
    if doms:
        facts["domains"] = _recent_unique(facts.get("domains", []), list(dict.fromkeys(doms)))
    tasks = [f"TS-{a}.{b}" for a, b in TASK.findall(text)]
    if tasks:
        facts["task_ids"] = _recent_unique(facts.get("task_ids", []), list(dict.fromkeys(tasks)))
    return facts


def render_facts(facts: Optional[dict]) -> str:
    facts = facts or {}
    lines = []
    if "days" in facts:
        lines.append(f"- 남은 일수: {facts['days']}일")
    if "hours_per_day" in facts:
        h = facts["hours_per_day"]
        lines.append(f"- 하루 공부 시간: {int(h) if float(h).is_integer() else h}시간")
    if facts.get("domains"):
        lines.append("- 관심 도메인: " + ", ".join(f"D{d}" for d in facts["domains"]))
    if facts.get("task_ids"):
        lines.append("- 관심 TS: " + ", ".join(facts["task_ids"]))
    return "\n".join(lines) if lines else "- (없음)"


# ---------------------------------------------------------------- 요약

def cut_index(messages: list[BaseMessage], keep_turns: int) -> int:
    """최근 keep_turns개 턴이 시작하는 위치(사용자 입력 HumanMessage). 그보다 앞이 없으면 0."""
    starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if len(starts) <= keep_turns:
        return 0
    return starts[-keep_turns]


def dialog_text(messages: list[BaseMessage]) -> str:
    """요약 LLM 입력. 사용자 입력·에이전트 답변·return_direct 결과(출제·채점·계획)만, 메시지당 MAX_LINE자."""
    lines = []
    for m in messages:
        text = config.get_text(m).strip()
        if not text:
            continue
        if isinstance(m, SystemMessage) and text.startswith(SUMMARY_HEADER):
            lines.append(f"(앞선 요약) {text[:MAX_LINE * 2]}")
        elif isinstance(m, HumanMessage):
            lines.append(f"사용자: {text[:MAX_LINE]}")
        elif isinstance(m, AIMessage) and not m.tool_calls and m.name and m.name != "supervisor":
            lines.append(f"{m.name}: {text[:MAX_LINE]}")
        elif isinstance(m, ToolMessage) and m.name in ("get_quiz", "submit_answer", "make_study_plan"):
            lines.append(f"{m.name}: {text[:MAX_LINE]}")
    return "\n".join(lines)


def summary_block(summary: str, facts: Optional[dict]) -> str:
    return f"{SUMMARY_HEADER}\n{summary.strip()}\n\n{FACTS_HEADER}\n{render_facts(facts)}"


async def _llm_summarize(text: str) -> str:
    from src.llm import get_runtime_llm
    return config.get_text(await get_runtime_llm().ainvoke(SUMMARY_PROMPT.format(dialog=text))).strip()


async def compact(messages: list[BaseMessage], facts: Optional[dict], *, summarize=_llm_summarize,
                  trigger: Optional[int] = None, keep_turns: Optional[int] = None) -> Optional[tuple[list, dict]]:
    """턴 시작 전 thread 메시지(이번 입력 제외)가 기준을 넘으면 (새 메시지 목록, trace 정보)를, 아니면 None.
    새 목록 = [SystemMessage(요약 + case_facts 블록), *최근 keep_turns개 턴]. 요약 LLM이 실패하면 ([], 오류 정보)."""
    limit = trigger if trigger is not None else config.SUMMARY_TRIGGER_MESSAGES
    keep = keep_turns if keep_turns is not None else config.SUMMARY_KEEP_TURNS
    if len(messages) <= limit:
        return None
    cut = cut_index(messages, keep)
    if cut <= 0:
        return None
    old, recent = messages[:cut], messages[cut:]
    try:
        summary = await summarize(dialog_text(old))
    except Exception as e:   # noqa: BLE001 — 요약 실패는 대화를 막지 않는다 (원래 메시지 유지)
        return [], {"status": "error", "error": type(e).__name__}
    block = SystemMessage(content=summary_block(summary, facts))
    return [block, *recent], {"status": "ok", "removed": len(old), "kept": len(recent)}
