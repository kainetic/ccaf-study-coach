"""P0(S2) 한 바퀴 스모크: /query 3회 "개념 질문 → 풀 출제 → 채점" + 채점 결과가 data/study_log.json에 기록되는지.

실행:
    python evaluation/smoke_p0.py                 # in-process (FastAPI TestClient, lifespan으로 그래프·MCP 서버 기동)
    python evaluation/smoke_p0.py --url http://localhost:8000   # 실행 중인 서버 (그래프 state 확인은 생략)
    python evaluation/smoke_p0.py --keep          # smoke 사용자 기록을 study_log.json에 남김 (기본은 확인 후 삭제)

LLM 없이 하는 확인(no_match·정답 비노출)도 함께 돈다. 실패하면 종료 코드 1.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.quiz_bank_server import QuizBank  # noqa: E402

USER = "smoke_p0"
Q1 = "agentic loop에서 stop_reason으로 반복 종료를 어떻게 판단해?"
Q2 = "도메인 1에서 시나리오형 연습 문제 하나 내줘"

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> bool:
    results.append((bool(ok), msg))
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return bool(ok)


def steps(out: dict, name: str) -> list[dict]:
    return [s for s in out["trace"] if s["step"] == name]


def load_pool() -> dict:
    return {d["quiz_id"]: d for d in json.loads(config.QUIZ_POOL_PATH.read_text(encoding="utf-8"))}


def user_attempts() -> list[dict]:
    if not config.STUDY_LOG_PATH.is_file():
        return []
    log = json.loads(config.STUDY_LOG_PATH.read_text(encoding="utf-8"))
    return log.get("users", {}).get(USER, {}).get("attempts", [])


def remove_user() -> None:
    if not config.STUDY_LOG_PATH.is_file():
        return
    log = json.loads(config.STUDY_LOG_PATH.read_text(encoding="utf-8"))
    if log.get("users", {}).pop(USER, None) is not None:
        tmp = config.STUDY_LOG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(config.STUDY_LOG_PATH)


def offline_checks() -> None:
    """LLM 없이: 풀에 없는 조합 → no_match + alternatives, 출제 결과에 정답·해설 없음."""
    print("[offline] QuizBank 순수 함수")
    bank = QuizBank()
    nm = bank.get_quiz(USER, task_id="TS-1.7", scenario_id=5)
    check(nm.get("status") == "no_match" and nm.get("alternatives")
          and all(a["task_id"] == "TS-1.7" for a in nm["alternatives"] if a["relaxed"] == "scenario_id"),
          f"TS-1.7 × SC-5 → no_match, alternatives {[(a['task_id'], a['scenario_id']) for a in nm.get('alternatives', [])]}")
    remove_user()


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="실행 중인 API 서버 주소 (없으면 in-process)")
    ap.add_argument("--keep", action="store_true", help="smoke 사용자 풀이 기록을 남김")
    args = ap.parse_args()

    offline_checks()
    pool = load_pool()
    before = len(user_attempts())
    thread = f"smoke-{uuid.uuid4().hex[:8]}"

    if args.url:
        import httpx
        client = httpx.Client(base_url=args.url, timeout=300)
        ctx = None
    else:
        from fastapi.testclient import TestClient
        import src.api as api_mod
        ctx = TestClient(api_mod.api)
        client = ctx.__enter__()

    def ask(q: str) -> dict:
        r = client.post("/query", json={"question": q, "user_id": USER, "thread_id": thread}, timeout=300)
        r.raise_for_status()
        out = r.json()
        print(f"\n[Q] {q}\n[A] {out['answer'][:700]}\n[trace] {json.dumps(out['trace'], ensure_ascii=False)[:500]}")
        return out

    def graph_state() -> dict | None:
        if args.url:
            return None
        import asyncio
        import src.api as api_mod
        st = client.portal.call(api_mod.coach.graph.aget_state, {"configurable": {"thread_id": thread}}) \
            if hasattr(client, "portal") else asyncio.run(api_mod.coach.graph.aget_state({"configurable": {"thread_id": thread}}))
        return st.values

    try:
        # 1) 개념 질문
        print("\n[1/3] 개념 질문")
        o1 = ask(Q1)
        check(any(s["agent"] == "concept_tutor" for s in steps(o1, "route")), "route → concept_tutor")
        check(steps(o1, "retrieve"), "search_guide 검색 실행 (retrieve step)")
        check(any(c["doc_id"] == "TS-1.1" for c in o1["contexts"]), "contexts에 TS-1.1")
        check("[근거:" in o1["answer"] and "TS-1.1" in o1["answer"], "답변에 [근거: … TS-1.1 …] 표기")

        # 2) 풀 출제
        print("\n[2/3] 풀 출제")
        o2 = ask(Q2)
        gq = [s for s in steps(o2, "get_quiz") if s["status"] == "ok"]
        check(any(s["agent"] == "quiz_master" for s in steps(o2, "route")), "route → quiz_master")
        if not check(gq, "get_quiz status ok"):
            return finish(args, before)
        quiz_id = gq[-1]["quiz_id"]
        item = pool[quiz_id]
        check(item["status"] == "approved" and item["domain"] == 1, f"출제 문항 {quiz_id}: approved · D1")
        check(all(f"{l}." in o2["answer"] or f"{l})" in o2["answer"] for l in "ABCD"), "답변에 보기 A~D")
        check(item["stem"][:60] in o2["answer"], "지문 원문 그대로 전달")
        check("정답은" not in o2["answer"] and "정답:" not in o2["answer"], "답변에 정답 미공개")
        st = graph_state()
        if st is not None:
            check(st.get("pending_quiz_id") == quiz_id, f"state.pending_quiz_id == {quiz_id}")
            dump = json.dumps([getattr(m, "content", "") for m in st["messages"]], ensure_ascii=False)
            leaked = [o["rationale"][:40] for o in item["options"] if o["rationale"][:40] in dump]
            check(not leaked and '"answer"' not in dump, "state(메시지)에 정답·해설 없음")
            check("answer" not in st, "state 키에 정답 없음")

        # 3) 채점 (일부러 오답)
        print("\n[3/3] 채점")
        wrong = next(l for l in "ABCD" if l != item["answer"])
        o3 = ask(f"답은 {wrong}")
        sub = [s for s in steps(o3, "submit") if s["status"] == "graded"]
        check(sub and sub[-1]["quiz_id"] == quiz_id and sub[-1]["correct"] is False, f"submit_answer 채점 {quiz_id} 선택 {wrong} → 오답")
        check(item["answer"] in o3["answer"], f"답변에 정답 {item['answer']} 공개 (채점 후)")
        att = user_attempts()
        new = att[before:]
        check(len(new) == 1 and new[0]["quiz_id"] == quiz_id and new[0]["choice"] == wrong and new[0]["correct"] is False,
              f"study_log.json 기록 (+{len(new)}건: {new[-1]['quiz_id'] if new else '-'})")
        st = graph_state()
        if st is not None:
            check(st.get("pending_quiz_id") is None, "채점 후 pending_quiz_id 비움")

        # 4) v2: 입력 가드 LLM이 그래프와 병렬 — LLM이 늦게 차단해도 도구는 실행되지 않고 이번 턴이 되돌려지는지
        if st is not None:
            print("\n[4] 입력 가드 LLM 차단 (병렬 실행 중 늦은 차단)")
            import asyncio
            import src.guards as guards
            from src.schemas import InputCheck

            async def late_block(text: str) -> InputCheck:
                await asyncio.sleep(8)          # 그래프가 도구 호출(gate 대기)까지 진행할 시간 (보통 약 5~7초)
                return InputCheck(category="injection", confidence=0.95, reason="smoke_p0 late block")

            gate_hits: list[str] = []
            orig_wait = guards.wait_input_ok

            async def counting_wait() -> None:
                gate_hits.append("wait")
                await orig_wait()

            ids_before = [m.id for m in st["messages"]]
            orig = guards.llm_check
            guards.llm_check, guards.wait_input_ok = late_block, counting_wait
            try:
                o4 = ask("TS 1.1 내용 설명해줘")
            finally:
                guards.llm_check, guards.wait_input_ok = orig, orig_wait
            check(gate_hits, f"도구가 gate에서 대기하다 취소됨 (gate 진입 {len(gate_hits)}회)")
            check(o4["answer"] == guards.REFUSALS["G2"], "늦은 차단 → G2 고정 문구")
            check(not o4["contexts"] and [s["names"] for s in steps(o4, "tools")] == [[]], "도구 0건 · contexts 없음")
            check([s.get("layer") for s in steps(o4, "guard")] == ["llm"] and not steps(o4, "route"),
                  "trace에 guard(llm)만 남음 (취소된 경로 기록 제거)")
            st4 = graph_state()
            check([m.id for m in st4["messages"]] == ids_before, "thread 메시지가 차단 전과 같음 (이번 턴 되돌림)")
            o5 = ask("안녕, 뭐 할 수 있어?")
            check(o5["answer"].strip() and any(s["agent"] == "general_agent" for s in steps(o5, "route")),
                  "차단 뒤 다음 정상 턴 동작 (general_agent)")
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
    return finish(args, before)


def finish(args, before: int) -> int:
    if not args.keep:
        remove_user()
    n_ok = sum(ok for ok, _ in results)
    print(f"\n===== smoke_p0: {n_ok}/{len(results)} PASS =====")
    for ok, msg in results:
        if not ok:
            print(f"  FAIL: {msg}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
