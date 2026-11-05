"""v2-3 긴 대화 스모크: 턴 시작 요약(memory.compact) + case_facts 보존.

같은 thread에서 5턴을 보낸다. 요약 기준을 낮춰(CCAF_SUMMARY_TRIGGER=8) 5번째 턴 시작 때 요약이 일어나게 하고,
1번째 턴에서 말한 "7일 · 하루 2시간"이 요약으로 잘려 나간 뒤에도 학습 계획이 다시 묻지 않고 그 값으로 만들어지는지 본다.

실행:
    python evaluation/smoke_memory.py        # in-process, 실제 Bedrock 호출, study_log는 임시 사본

실패하면 종료 코드 1.
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# src.config를 import하기 전에 설정 (요약 기준·풀이 기록 임시 사본)
os.environ.setdefault("CCAF_SUMMARY_TRIGGER", "8")
_tmp = Path(tempfile.mkdtemp(prefix="ccaf_memory_")) / "study_log.json"
shutil.copy(ROOT / "data" / "study_log.json", _tmp)
os.environ["CCAF_STUDY_LOG"] = str(_tmp)

from langchain_core.messages import SystemMessage  # noqa: E402

from src import config, memory  # noqa: E402

USER = "smoke_memory"
TURNS = [
    "시험까지 7일 남았고 하루 2시간씩 공부할 거야. 일단 agentic loop에서 stop_reason으로 종료를 어떻게 판단해?",
    "도메인 1에서 연습 문제 하나 내줘",
    "답은 A",
    "MCP 도구 에러 응답에서 isError는 언제 써?",
    "이제 내 학습 계획 짜줘",
]

results: list[tuple[bool, str]] = []


def check(ok, msg: str) -> bool:
    results.append((bool(ok), msg))
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return bool(ok)


def steps(out: dict, name: str) -> list[dict]:
    return [s for s in out["trace"] if s["step"] == name]


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    print(f"[memory] SUMMARY_TRIGGER_MESSAGES={config.SUMMARY_TRIGGER_MESSAGES} · KEEP_TURNS={config.SUMMARY_KEEP_TURNS}"
          f" · study_log {config.STUDY_LOG_PATH}")
    from fastapi.testclient import TestClient
    import src.api as api_mod
    thread = f"memory-{uuid.uuid4().hex[:8]}"

    with TestClient(api_mod.api) as client:
        def ask(q: str) -> dict:
            r = client.post("/query", json={"question": q, "user_id": USER, "thread_id": thread}, timeout=600)
            r.raise_for_status()
            out = r.json()
            print(f"\n[Q] {q}\n[A] {out['answer'][:300]}\n[trace] {json.dumps(out['trace'], ensure_ascii=False)[:400]}")
            return out

        def state() -> dict:
            st = client.portal.call(api_mod.coach.graph.aget_state, {"configurable": {"thread_id": thread}})
            return st.values or {}

        outs = []
        sizes = []
        for q in TURNS[:-1]:
            outs.append(ask(q))
            sizes.append(len(state().get("messages", [])))
        st = state()
        check(st.get("case_facts", {}).get("days") == 7 and st["case_facts"].get("hours_per_day") == 2.0,
              f"case_facts 보존: {st.get('case_facts')}")
        check(not any(steps(o, "summarize") for o in outs[:2]), "초반 턴(기준 이하)에는 요약 없음")
        before = len(st.get("messages", []))

        o5 = ask(TURNS[-1])
        summ = [s for s in steps(o5, "summarize") if s.get("status") == "ok"]
        check(summ, f"5번째 턴 시작 때 요약 실행 {summ[:1]}")
        st = state()
        msgs = st.get("messages", [])
        first = msgs[0] if msgs else None
        check(isinstance(first, SystemMessage) and first.content.startswith(memory.SUMMARY_HEADER),
              "thread 첫 메시지가 요약 블록(SystemMessage)")
        check(first is not None and "남은 일수: 7일" in first.content and "하루 공부 시간: 2시간" in first.content,
              "요약 블록에 case_facts(7일 · 하루 2시간)가 코드로 남음")
        check(not any(TURNS[0][:15] in str(getattr(m, "content", "")) for m in msgs[1:]),
              "1번째 턴 원문은 thread에서 빠짐 (요약으로 대체)")
        check(len(msgs) < before + 4, f"메시지 수 축소: 요약 전 {before} (+5번째 턴) → {len(msgs)} · 턴별 {sizes}")
        alloc = steps(o5, "allocate") + steps(o5, "plan")      # 둘 다 total_hours를 남긴다
        check(any(s.get("agent") == "study_coach" for s in steps(o5, "route")), "5번째 턴 → study_coach")
        check(alloc and alloc[-1].get("total_hours") == 14,
              f"다시 묻지 않고 7일 × 2시간 = 14시간으로 배분 ({alloc[-1] if alloc else '-'})")

    n_ok = sum(ok for ok, _ in results)
    print(f"\n===== smoke_memory: {n_ok}/{len(results)} PASS =====")
    for ok, msg in results:
        if not ok:
            print(f"  FAIL: {msg}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
