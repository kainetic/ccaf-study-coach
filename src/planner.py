"""학습 계획 (DESIGN §5-5). Day 7 plan_and_execute.py의 plan → execute → replan → finalize 구조.

- allocate_study_time: 도메인별 시간 배분 (결정적 계산, LLM 계산 금지). 몫 ∝ 시험 비중 × 약점 계수
  (출제 가중과 같은 식 quiz_bank_server.weakness_factor), 0.5시간 단위 최대 잔여법 → 합계가 항상 총시간과 같다.
- 일자별 도메인 시간표(grid)도 코드가 만든다 (각 도메인 시간을 기간 전체에 고르게 펼침).
- plan(LLM): grid의 각 (일차, 도메인) 칸에 어떤 TS를 얼마나, 무엇에 초점을 둘지 고른다 (약한 TS 우선).
- execute(코드): TS 유효성·도메인·칸 시간 검증, 남은 문제 수·연결 Exercise 붙이기.
- replan: 위반이 있으면 위반 목록을 주고 LLM 수정 1회 → 그래도 어긋나면 결정적 기본 배정(정답률 낮은 TS부터)으로 대체.
- finalize(코드 서식화): 약점 근거 → 시간 배분 → 일자별 계획 → 연결 Exercise. 결과는 make_study_plan(return_direct)이 그대로 답변으로 쓴다.
"""
import math
import re
from functools import lru_cache
from operator import add
from typing import Annotated, Optional, TypedDict

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.graph import END, START, StateGraph

from src import config, retriever
from src.quiz_bank_server import MIN_ATTEMPTS_FOR_WEAKNESS, WEAKNESS_FLOOR, weakness_factor
from src.schemas import DomainAllocation, StudyAllocation, StudyPlanDraft

UNIT = 0.5
MAX_DAYS, MAX_HOURS_PER_DAY = 30, 12
MAX_REVISIONS = 1
EPS = 1e-6


# ---------------------------------------------------------------- 배분 (결정적)

def validate_request(days, hours_per_day) -> Optional[str]:
    if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        return f"days는 1~{MAX_DAYS} 사이 정수여야 합니다: {days!r}"
    if not isinstance(hours_per_day, (int, float)) or not UNIT <= hours_per_day <= MAX_HOURS_PER_DAY:
        return f"hours_per_day는 {UNIT}~{MAX_HOURS_PER_DAY}시간이어야 합니다: {hours_per_day!r}"
    if abs(hours_per_day / UNIT - round(hours_per_day / UNIT)) > EPS:
        return f"hours_per_day는 {UNIT}시간 단위여야 합니다: {hours_per_day!r}"
    return None


def allocate_study_time(days: int, hours_per_day: float, domain_stats: list[dict],
                        weights: dict[int, float] = config.DOMAIN_WEIGHTS) -> StudyAllocation:
    """domain_stats: get_progress의 domains (domain, attempts, correct, accuracy)."""
    if err := validate_request(days, hours_per_day):
        raise ValueError(err)
    total = days * hours_per_day
    stats = {d["domain"]: d for d in domain_stats}
    rows = []
    for d, w in weights.items():
        s = stats.get(d, {"attempts": 0, "correct": 0, "accuracy": None})
        rows.append({"domain": d, "weight": w, "attempts": s["attempts"], "accuracy": s["accuracy"],
                     "factor": weakness_factor(s["attempts"], s["correct"])})
    score = {r["domain"]: r["weight"] * r["factor"] for r in rows}
    units = round(total / UNIT)
    raw = {d: units * v / sum(score.values()) for d, v in score.items()}
    got = {d: math.floor(v) for d, v in raw.items()}
    for d in sorted(raw, key=lambda d: (-(raw[d] - got[d]), -score[d], d))[:units - sum(got.values())]:
        got[d] += 1
    return StudyAllocation(
        days=days, hours_per_day=hours_per_day, total_hours=total, unit=UNIT,
        formula=(f"시간 몫 ∝ 시험 비중 × 약점 계수 (시도 {MIN_ATTEMPTS_FOR_WEAKNESS}회 이상: max({WEAKNESS_FLOOR}, 1 − 정답률), "
                 f"미만: 1.0), {UNIT}시간 단위 최대 잔여법"),
        domains=[DomainAllocation(name=config.DOMAIN_NAMES[r["domain"]], hours=got[r["domain"]] * UNIT, **r) for r in rows])


BLOCK = 1.0                    # 시간표 블록 (하루에 도메인이 너무 잘게 쪼개지지 않게)


def day_grid(alloc: StudyAllocation) -> list[dict[int, float]]:
    """일차별 {도메인: 시간}. 도메인 시간을 1시간 블록(+0.5 나머지)으로 나눠 기간 전체에 고르게 펼친 뒤
    약점 큰 도메인부터 하루 용량만큼 채운다 (블록이 하루를 넘으면 다음 날로 나눔). 합계는 배분과 같다."""
    order = {a.domain: i for i, a in enumerate(sorted(alloc.domains, key=lambda a: (-a.weight * a.factor, a.domain)))}
    blocks = []
    for a in alloc.domains:
        sizes = [BLOCK] * int(a.hours // BLOCK) + ([a.hours % BLOCK] if a.hours % BLOCK > EPS else [])
        blocks += [((k + 0.5) / len(sizes), order[a.domain], a.domain, s) for k, s in enumerate(sizes)]
    grid: list[dict[int, float]] = [{} for _ in range(alloc.days)]
    i, left = 0, alloc.hours_per_day
    for _, _, d, size in sorted(blocks):
        while size > EPS:
            take = min(size, left)
            grid[i][d] = grid[i].get(d, 0) + take
            size, left = size - take, left - take
            if left <= EPS and i < alloc.days - 1:
                i, left = i + 1, alloc.hours_per_day
    return grid


@lru_cache(maxsize=1)
def exercise_domains() -> dict[str, tuple[str, list[int]]]:
    """EX 청크의 "Domains reinforced: Domain 1 (...), Domain 2 (...)"를 파싱 → {EX-1: (제목, [1, 2, 5])}."""
    out = {}
    for did, c in retriever.load_chunks().items():
        if did.startswith("EX-"):
            m = re.search(r"Domains reinforced:(.*)", c.text)
            doms = [int(x) for x in re.findall(r"Domain\s*([1-5])", m[1])] if m else []
            out[did] = (c.title.split(": ", 1)[-1], doms)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- Plan-Execute 서브그래프

class PlanState(TypedDict, total=False):
    alloc: dict                 # StudyAllocation
    grid: list[dict]            # [{domain(str): hours}]
    tasks: dict                 # get_progress tasks {TS: {domain, attempts, correct, remaining}}
    weak: dict                  # get_weak_domains 결과
    user_id: str
    draft: list[dict]           # StudyPlanDraft.days
    violations: list[str]
    revisions: int
    fallback: bool
    events: Annotated[list[str], add]
    final: str


def _ts_title(tid: str) -> str:
    """TS 청크 제목 (가이드 Task Statement 문장). 계획의 칸 설명으로 쓴다 (LLM이 초점 문장을 쓰지 않음, v2-7)."""
    c = retriever.load_chunks().get(tid)
    return c.title.split(": ", 1)[-1] if c else ""


def _ts_line(tid: str, t: dict) -> str:
    acc = f"{t['correct'] / t['attempts']:.0%}" if t["attempts"] else "미시도"
    return f"{tid} {_ts_title(tid)} (시도 {t['attempts']}, 정답률 {acc}, 남은 문제 {t['remaining']})"


def _grid_text(grid: list[dict]) -> str:
    return "\n".join(f"- {i + 1}일차: " + ", ".join(f"D{d} {h:.1f}h" for d, h in day.items()) for i, day in enumerate(grid))


PLAN_PROMPT = """너는 CCA-F 시험 학습 계획을 짜는 planner다. 일자별 도메인 시간표(grid)는 이미 코드로 정해져 있다.
각 일차의 각 도메인 칸에 그 도메인의 TS와 시간만 배정하라 (focus는 쓰지 않는다. 설명 문장 없이 task_id·hours만).

[규칙]
- 일차 수는 {days}일, day는 1~{days}. grid에 있는 (일차, 도메인) 칸만 채운다.
- 한 칸의 시간은 그 칸의 TS 항목 hours 합과 정확히 같아야 한다. hours는 0.5 단위. 한 칸에 TS는 1~2개.
- TS는 반드시 그 칸 도메인 소속(TS-<도메인>.x)이어야 한다.
- 정답률이 낮거나 아직 시도하지 않은 TS를 앞쪽 일차에 먼저, 같은 TS를 너무 자주 반복하지 않는다.

[도메인별 시간 배분] (합계 {total:.1f}시간)
{alloc}

[일자별 도메인 시간표 grid]
{grid}

[도메인별 TS 기록]
{tasks}
{fix}"""


def _llm():
    from src.llm import get_runtime_llm
    return get_runtime_llm().with_structured_output(StudyPlanDraft)


async def _ask_llm(state: PlanState, fix: str = "") -> list[dict]:
    alloc = StudyAllocation.model_validate(state["alloc"])
    by_dom: dict[int, list[str]] = {}
    for tid, t in state["tasks"].items():
        by_dom.setdefault(t["domain"], []).append(_ts_line(tid, t))
    prompt = PLAN_PROMPT.format(
        days=alloc.days, total=alloc.total_hours,
        alloc="\n".join(f"- D{a.domain} {a.name}: {a.hours:.1f}h" for a in alloc.domains),
        grid=_grid_text([{int(k): v for k, v in day.items()} for day in state["grid"]]),
        tasks="\n".join(f"D{d}: " + "; ".join(v) for d, v in sorted(by_dom.items())), fix=fix)
    draft = await _llm().ainvoke(prompt)
    return [d.model_dump() for d in draft.days]


async def plan_node(state: PlanState) -> dict:
    try:
        return {"draft": await _ask_llm(state), "revisions": 0, "events": ["plan"]}
    except Exception as e:   # noqa: BLE001 — 계획 LLM 실패는 기본 배정으로 대체
        return {"draft": [], "revisions": MAX_REVISIONS, "events": [f"plan_error:{type(e).__name__}"]}


def check_draft(draft: list[dict], grid: list[dict], tasks: dict) -> list[str]:
    v: list[str] = []
    days = {d.get("day"): d for d in draft}
    for i, cell in enumerate(grid, start=1):
        day = days.get(i)
        if day is None:
            v.append(f"{i}일차가 없음")
            continue
        got: dict[int, float] = {}
        for it in day["items"]:
            tid = it["task_id"].strip().upper().replace(" ", "-")
            if tid not in tasks:
                v.append(f"{i}일차: 알 수 없는 TS {it['task_id']}")
                continue
            h = it["hours"]
            if h <= 0 or abs(h / UNIT - round(h / UNIT)) > EPS:
                v.append(f"{i}일차 {tid}: hours {h}는 0.5 단위가 아님")
            d = tasks[tid]["domain"]
            got[d] = got.get(d, 0) + h
        want = {int(k): x for k, x in cell.items()}
        for d in sorted(set(want) | set(got)):
            if abs(got.get(d, 0) - want.get(d, 0)) > EPS:
                v.append(f"{i}일차 D{d}: 배정 {got.get(d, 0):.1f}h ≠ 시간표 {want.get(d, 0):.1f}h")
    extra = sorted(k for k in days if not isinstance(k, int) or not 1 <= k <= len(grid))
    if extra:
        v.append(f"시간표에 없는 일차: {extra}")
    return v


def execute_node(state: PlanState) -> dict:
    return {"violations": check_draft(state.get("draft") or [], state["grid"], state["tasks"]), "events": ["execute"]}


def default_draft(grid: list[dict], tasks: dict) -> list[dict]:
    """결정적 기본 배정: 도메인마다 정답률 낮은(미시도는 0.5로 봄) → 남은 문제 많은 TS 순으로 돌아가며 배정."""
    queues: dict[int, list[str]] = {}
    for tid, t in sorted(tasks.items()):
        queues.setdefault(t["domain"], []).append(tid)
    for d, q in queues.items():
        q.sort(key=lambda tid: (tasks[tid]["correct"] / tasks[tid]["attempts"] if tasks[tid]["attempts"] else 0.5,
                                -tasks[tid]["remaining"], tid))
    pos = {d: 0 for d in queues}
    out = []
    for i, cell in enumerate(grid, start=1):
        items = []
        for d, h in ((int(k), x) for k, x in cell.items()):
            tid = queues[d][pos[d] % len(queues[d])]
            pos[d] += 1
            items.append({"task_id": tid, "hours": h, "focus": None})
        out.append({"day": i, "items": items})
    return out


async def replan_node(state: PlanState) -> dict:
    n = state.get("revisions", 0)
    if n < MAX_REVISIONS:
        fix = ("\n[이전 초안의 위반 — 모두 고쳐서 전체 계획을 다시 내라]\n"
               + "\n".join(f"- {x}" for x in state["violations"][:20]))
        try:
            return {"draft": await _ask_llm(state, fix), "revisions": n + 1, "events": ["revise"]}
        except Exception as e:   # noqa: BLE001
            pass
    return {"draft": default_draft(state["grid"], state["tasks"]), "revisions": n, "fallback": True,
            "events": ["fallback"]}


def after_execute(state: PlanState) -> str:
    return "replan" if state["violations"] else "finalize"


def _pick_exercise(domain: int, day: int) -> Optional[str]:
    cands = [ex for ex, (_, doms) in exercise_domains().items() if domain in doms]
    return cands[day % len(cands)] if cands else None


def format_plan(state: PlanState) -> str:
    alloc = StudyAllocation.model_validate(state["alloc"])
    tasks, weak = state["tasks"], state["weak"]
    lines = [f"**학습 계획** ({alloc.days}일 × {alloc.hours_per_day:g}시간 = 총 {alloc.total_hours:g}시간)", ""]

    lines.append("**약점 근거** (풀이 기록 기준, 약점 점수 = 시험 비중 × (1 − 정답률))")
    if weak.get("ranked"):
        lines += ["| 순위 | 도메인 | 비중 | 시도 | 정답률 | 약점 점수 |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(weak["ranked"], start=1):
            lines.append(f"| {i} | D{r['domain']} {r['name']} | {r['weight']:.0%} | {r['attempts']} | "
                         f"{r['accuracy']:.0%} | {r['score']:.3f} |")
    if weak.get("insufficient"):
        lines.append("기록 부족(시도 3회 미만, 시험 비중만 반영): "
                     + ", ".join(f"D{r['domain']}({r['attempts']}회)" for r in weak["insufficient"]))
    lines += ["", f"**시간 배분** ({alloc.formula})", "| 도메인 | 비중 | 약점 계수 | 시간 |", "|---|---|---|---|"]
    for a in sorted(alloc.domains, key=lambda a: a.domain):
        lines.append(f"| D{a.domain} {a.name} | {a.weight:.0%} | {a.factor:.2f} | {a.hours:.1f}h |")
    lines += [f"| **합계** | | | **{sum(a.hours for a in alloc.domains):.1f}h** |", "", "**일자별 계획**"]

    used_ex: set[str] = set()
    for day in sorted(state["draft"], key=lambda d: d["day"]):
        items = day["items"]
        main = max(items, key=lambda it: it["hours"]) if items else None
        parts = []
        for it in items:
            tid = it["task_id"].strip().upper().replace(" ", "-")
            parts.append(f"{tid} {it['hours']:.1f}h — {it.get('focus') or _ts_title(tid)} (남은 문제 {tasks[tid]['remaining']}개)")
        ex = _pick_exercise(tasks[main["task_id"].strip().upper().replace(" ", "-")]["domain"], day["day"]) if main else None
        if ex:
            used_ex.add(ex)
        total = sum(it["hours"] for it in items)
        lines.append(f"- **{day['day']}일차** ({total:.1f}h): " + " · ".join(parts) + (f" · 실습 {ex}" if ex else ""))

    lines += ["", "**연결 Preparation Exercise** (가이드의 Domains reinforced 기준)"]
    for ex, (title, doms) in exercise_domains().items():
        if ex in used_ex:
            lines.append(f"- {ex} {title} — " + ", ".join(f"D{d}" for d in doms))
    lines += ["", "각 TS 문제는 \"TS-2.1 문제 내줘\"처럼 요청하면 사전 검토된 풀에서 출제합니다."
              + (" (계획 초안이 시간표와 맞지 않아 기본 배정을 사용했습니다)" if state.get("fallback") else "")]
    return "\n".join(lines)


def finalize_node(state: PlanState) -> dict:
    return {"final": format_plan(state), "events": ["finalize"]}


@lru_cache(maxsize=1)
def plan_graph():
    g = StateGraph(PlanState)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("replan", replan_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", after_execute, {"replan": "replan", "finalize": "finalize"})
    g.add_edge("replan", "execute")
    g.add_edge("finalize", END)
    return g.compile()


async def make_study_plan(alloc: StudyAllocation, progress: dict, weak: dict) -> tuple[str, dict]:
    """반환 (서식화된 계획, trace용 요약)."""
    grid = [{str(k): v for k, v in day.items()} for day in day_grid(alloc)]
    out = await plan_graph().ainvoke({"alloc": alloc.model_dump(), "grid": grid, "tasks": progress["tasks"],
                                      "weak": weak, "events": []}, {"recursion_limit": 20})
    summary = {"events": out["events"], "revisions": out.get("revisions", 0), "fallback": bool(out.get("fallback")),
               "allocation": {f"D{a.domain}": a.hours for a in alloc.domains}, "total_hours": alloc.total_hours,
               "weak_top": [f"D{r['domain']}" for r in weak.get("top", [])]}
    await adispatch_custom_event("plan", summary)
    return out["final"], summary
