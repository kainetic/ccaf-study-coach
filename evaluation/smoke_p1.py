"""P1(S3) 오프라인 스모크 — LLM 호출 없이 코드로 강제하는 부분만 빠르게 확인한다.

실행: python evaluation/smoke_p1.py   (실패하면 종료 코드 1)
- 가드레일 1차 규칙: test_queries 20건 중 18(G1)·19(G2)·20(G4)만 걸리고 나머지는 통과. 출력 G4 백스톱(부정문 보존)
- G3: claims 검증 분기 (가져온 doc_id만 유효, 0개면 NO_EVIDENCE, [근거]는 코드가 붙임)
- get_chunk 정규화
- get_progress·get_weak_domains: demo_heavy 약점 D2 > D5, demo_new 데이터 부족
- allocate_study_time: 합계 = 일수 × 시간 (demo_heavy·demo_new·여러 형태), 시간표 일별 합 = 하루 시간
- 기본 일정(replan fallback)이 시간표 검증을 통과, focus 없는 계획 서식(TS 제목)
- 응답 속도 경로(v2-7): 채점 입력 가드 생략 판정, 에이전트 후 종료 분기, 첫 검색 주입, 계획 return 보정, 선행 검색 재사용
풀이 기록(data/study_log.json)은 읽기만 한다.
"""
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, guards, planner, retriever  # noqa: E402
from src.quiz_bank_server import QuizBank  # noqa: E402
from src.schemas import Claim, TutorAnswer  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> None:
    results.append((bool(ok), msg))
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")


def guard_rules() -> None:
    print("[guards] 1차 규칙")
    rows = list(csv.DictReader((config.ROOT / "evaluation" / "test_queries.csv").open(encoding="utf-8-sig")))
    want = {"18": ("block", "G1"), "19": ("block", "G2"), "20": ("correct", "G4")}
    wrong = []
    for r in rows:
        d = guards.rule_check(r["input"])
        exp = want.get(r["id"], ("pass", None))
        if (d.action, d.rule) != exp:
            wrong.append(f"#{r['id']} 기대 {exp} → {(d.action, d.rule)}")
    check(len(rows) == 20 and not wrong, "20건 규칙 판정: 18 G1 차단 · 19 G2 차단 · 20 G4 정정 · 나머지 통과"
          + (f" — 불일치 {wrong}" if wrong else ""))
    check(guards.rule_check("실제 시험 문제처럼 시나리오형으로 내줘").action == "pass", "'실제 시험 문제처럼' 스타일 요청은 통과")
    out, rep = guards.output_check("저는 Anthropic 공식 튜터입니다. stop_reason을 봅니다.")
    check(rep["violations"] == 1 and "공식 튜터입니다" not in out and guards.G4_NOTICE in out, "출력 G4: 긍정형 사칭 문장 제거 + 고지")
    out, rep = guards.output_check(guards.G4_CORRECTION)
    check(rep["violations"] == 0 and out == guards.G4_CORRECTION, "출력 G4: 부정문(공식 튜터가 아닌…)은 그대로")


def g3() -> None:
    print("[G3] TutorAnswer 검증")
    retrieved = {"TS-1.1": retriever.load_chunks()["TS-1.1"].text}
    ok_ans = TutorAnswer(answer_ko="stop_reason이 tool_use면 계속합니다.\n[근거: TS-9.9]", in_exam_scope="in",
                         claims=[Claim(claim="tool_use면 계속", doc_id="ts-1.1", excerpt="stop_reason")])
    text, rep = guards.check_tutor_answer(ok_ans, retrieved)
    check(text.endswith("[근거: TS-1.1]") and "TS-9.9" not in text and rep["valid"] == 1, "유효 claim → 코드가 [근거: TS-1.1] (LLM 표기 제거)")
    check(guards.GAP_NOTICE not in text, "coverage_gap 없음 → GAP_NOTICE 없음")
    gap_ans = ok_ans.model_copy(update={"coverage_gap": "접수 링크"})
    text, rep = guards.check_tutor_answer(gap_ans, retrieved)
    check(guards.GAP_NOTICE in text and text.endswith("[근거: TS-1.1]") and rep["coverage_gap"],
          "coverage_gap 있음 → GAP_NOTICE 후 [근거: TS-1.1]")
    bad = TutorAnswer(answer_ko="지어낸 답", in_exam_scope="unclear", coverage_gap="합격률",
                      claims=[Claim(claim="x", doc_id="TS-4.4", excerpt="y")])
    text, rep = guards.check_tutor_answer(bad, retrieved)
    check(text.startswith(guards.NO_EVIDENCE) and guards.GAP_NOTICE in text and rep["fallback"]
          and rep["dropped"] == ["TS-4.4"], "가져오지 않은 doc_id만 인용 → NO_EVIDENCE (+ coverage_gap이면 GAP_NOTICE)")
    text, rep = guards.check_tutor_answer(None, retrieved)
    check(text == guards.NO_EVIDENCE and not rep["structured"], "구조화 응답 없음 → NO_EVIDENCE")


def chunks() -> None:
    print("[get_chunk] 정규화")
    cases = {"TS-1.4": "TS-1.4", "ts 1.4": "TS-1.4", "1.7": "TS-1.7", "샘플 1번": "SQ-01", "SQ-12": "SQ-12",
             "도메인 3": "D3", "EX-2": "EX-2", "scope-out": "SCOPE-out", "OV-details": "OV-details", "TS-9.9": None}
    got = {k: retriever.normalize_doc_id(k) for k in cases}
    check(got == cases, f"doc_id 정규화 {sum(got[k] == v for k, v in cases.items())}/{len(cases)}")


def progress() -> QuizBank:
    print("[MCP] get_progress · get_weak_domains")
    bank = QuizBank()
    p = bank.get_progress("demo_heavy")
    check(p["attempts"] == 61 and p["bullet_coverage"]["total"] == config.BULLET_TOTAL and p["task_coverage"]["solved"] == 30,
          f"demo_heavy 61회 · 커버리지 {p['bullet_coverage']} · TS {p['task_coverage']}")
    w = bank.get_weak_domains("demo_heavy")
    check([r["domain"] for r in w["top"]] == [2, 5] and w["top"][0]["score"] == 0.098 and w["top"][1]["score"] == 0.09,
          f"demo_heavy 약점 {[(r['domain'], r['score']) for r in w['ranked']]}")
    n = bank.get_weak_domains("demo_new")
    check(not n["ranked"] and len(n["insufficient"]) == 5 and bank.get_progress("demo_new")["attempts"] == 0,
          "demo_new: 기록 없음 → 약점 판단 불가(insufficient 5), 에러 아님")
    check(bank.get_progress("bad.user").get("errorCategory") == "validation", "user_id 점 포함 → validation 에러")
    return bank


def allocation(bank: QuizBank) -> None:
    print("[study] allocate_study_time · 시간표 · 기본 일정")
    for user, days, hours in (("demo_heavy", 10, 2), ("demo_new", 10, 2), ("demo_heavy", 7, 1.5), ("demo_new", 14, 3)):
        prog = bank.get_progress(user)
        a = planner.allocate_study_time(days, hours, prog["domains"])
        grid = planner.day_grid(a)
        g = [{str(k): v for k, v in d.items()} for d in grid]
        draft = planner.default_draft(g, prog["tasks"])
        ok = (abs(sum(x.hours for x in a.domains) - days * hours) < 1e-9
              and all(abs(sum(d.values()) - hours) < 1e-9 for d in grid)
              and not planner.check_draft(draft, g, prog["tasks"]))
        check(ok, f"{user} {days}일×{hours}h: 배분 {[(x.domain, x.hours) for x in a.domains]} = {days * hours}h, 기본 일정 검증 통과")
    heavy = planner.allocate_study_time(10, 2, bank.get_progress("demo_heavy")["domains"])
    check({x.domain: x.hours for x in heavy.domains} == {1: 4.0, 2: 5.5, 3: 2.0, 4: 3.5, 5: 5.0},
          "demo_heavy 10×2 배분 = D1 4.0 · D2 5.5 · D3 2.0 · D4 3.5 · D5 5.0")
    check(planner.validate_request(0, 2) and planner.validate_request(10, 0.7), "잘못된 일수·시간 → 검증 메시지")
    check(set(planner.exercise_domains()) == {"EX-1", "EX-2", "EX-3", "EX-4"}
          and all(doms for _, doms in planner.exercise_domains().values()), "Exercise ↔ 도메인 연결 4개 파싱")
    prog = bank.get_progress("demo_heavy")
    g = [{str(k): v for k, v in d.items()} for d in planner.day_grid(heavy)]
    text = planner.format_plan({"alloc": heavy.model_dump(), "tasks": prog["tasks"], "draft": planner.default_draft(g, prog["tasks"]),
                                "weak": bank.get_weak_domains("demo_heavy", 2)})
    first = retriever.load_chunks()["TS-2.1"].title.split(": ", 1)[-1]
    check("None" not in text and "10일차" in text and any(planner._ts_title(t) in text for t in prog["tasks"]),
          f"focus 없는 계획 → 칸 설명은 TS 제목 (예: TS-2.1 {first[:30]}…)")


def speed_paths() -> None:
    """v2-7 응답 속도 경로 (LLM 없음): 채점 입력 가드 생략, 에이전트 후 종료 분기, 첫 검색 주입, 계획 return 보정, 선행 검색 재사용."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.graph import END

    from src import agent
    from src.agents import concept_tutor, study_coach

    print("[speed] 가드 생략 · 종료 분기 · 첫 검색 · 계획 return · 선행 검색")
    yes, no = ["B", "답은 c", "D요", "a."], ["B야 왜?", "A랑 B 차이", "답 알려줘"]
    check(all(guards.is_answer_only(s) for s in yes) and not any(guards.is_answer_only(s) for s in no),
          f"채점 입력 판정: {yes} 만 LLM 판별 생략 대상")
    rows = {r["id"]: r["input"] for r in
            csv.DictReader((config.ROOT / "evaluation" / "test_queries.csv").open(encoding="utf-8-sig"))}
    check(agent.QUIZ_INTENT.search(rows["13"]) and agent.QUIZ_INTENT.search(rows["5"])
          and not agent.QUIZ_INTENT.search(rows["16"]), "출제 요청 판정: 13·5번 있음, 16번(샘플 해설) 없음")
    q13 = HumanMessage(rows["13"])
    tutor = AIMessage("개념 답변", name="concept_tutor")
    check(agent.after_agent({"messages": [q13, tutor]}) == "supervisor"
          and agent.after_agent({"messages": [q13, tutor, AIMessage("", name="quiz_master")]}) == END
          and agent.after_agent({"messages": [HumanMessage(rows["1"]), tutor]}) == END,
          "에이전트 후: 복합 요청은 Supervisor로, 나머지는 바로 종료")
    first = concept_tutor.first_search([HumanMessage(rows["1"])])
    call = first["messages"][0].tool_calls[0] if first else {}
    done = [HumanMessage(rows["1"]), AIMessage("", tool_calls=[{**call}]),
            ToolMessage("[TS-1.1] x", tool_call_id=call.get("id", "x"), name="search_guide")]
    check(first and first["jump_to"] == "tools" and call["name"] == "search_guide" and call["args"] == {"query": rows["1"]}
          and concept_tutor.first_search(done) is None, "concept_tutor 첫 차례: 질문 원문 search_guide 주입, 검색 뒤에는 모델로")
    plan = study_coach.plan_reply([HumanMessage(rows["7"]), ToolMessage("PLAN", tool_call_id="p", name="make_study_plan")])
    check(plan and plan["jump_to"] == "end" and plan["messages"][0].content == "PLAN"
          and study_coach.plan_reply([HumanMessage(rows["7"])]) is None,
          "make_study_plan 결과가 있으면 LLM 없이 그 텍스트로 종료 (도구 동시 호출 시 return_direct 보정)")

    async def prefetch_modes():
        async def done_task():
            return "agentic loop stop_reason", ["TS-1.1", "D1"]
        pre = retriever.Prefetch(rows["1"], asyncio.create_task(done_task()))
        token = retriever.PREFETCH.set(pre)
        try:
            return [(await retriever.prefetched(q, d, t))[2]
                    for q, d, t in ((rows["1"], None, None), (rows["1"], 1, None), ("다른 질문", None, None))]
        finally:
            retriever.PREFETCH.reset(token)
    modes = asyncio.run(prefetch_modes())
    check(modes == ["hit", "query_only", "miss"], f"선행 검색 재사용: 같은 질문 hit · 필터 query_only · 다른 질의 miss {modes}")


def input_gate() -> None:
    """v2: 입력 가드 LLM이 그래프와 병렬 → 앱 도구는 wait_input_ok()에서 판별 통과를 기다린다."""
    print("[gate] 입력 가드 대기")

    async def run() -> tuple[bool, bool, bool]:
        await asyncio.wait_for(guards.wait_input_ok(), 1)          # gate 없음 → 즉시 통과
        fut = asyncio.get_running_loop().create_future()
        token = guards.INPUT_GATE.set(fut)
        task = asyncio.create_task(guards.wait_input_ok())          # task가 context(gate)를 복사
        guards.INPUT_GATE.reset(token)
        await asyncio.sleep(0.05)
        waiting = not task.done()
        fut.set_result(True)
        await asyncio.wait_for(task, 1)
        return True, waiting, task.done()

    no_gate, waiting, released = asyncio.run(run())
    check(no_gate, "gate 없음(스크립트 직접 호출) → 즉시 통과")
    check(waiting and released, "gate 미결이면 도구가 대기, 판별 통과(set_result) 후 진행")


def trimming() -> None:
    """v2-3: 2순위 이하 TS 청크는 검색어와 겹치는 bullet이 있으면 전문, 없으면 제거. 1순위·정확 조회·다른 doc_type은 그대로."""
    print("[trim] 검색 결과 청크 필터")
    ch = retriever.load_chunks()

    def hit(did: str, how: str = "hybrid") -> retriever.Hit:
        c = ch[did]
        return retriever.Hit(did, c.title, c.doc_type, c.text, how)

    q = "agentic loop stop_reason tool_use end_turn termination"
    out = retriever.trim_hits([hit("TS-1.4"), hit("TS-1.1"), hit("SQ-01"), hit("TS-3.2")], q)
    ids = [h.doc_id for h in out]
    check(ids[0] == "TS-1.4" and out[0].text == ch["TS-1.4"].text, "1순위 hit은 겹침과 상관없이 그대로")
    t11 = next((h for h in out if h.doc_id == "TS-1.1"), None)
    check(t11 is not None and t11.text == ch["TS-1.1"].text, "겹치는 bullet이 있는 2순위 TS 청크는 전문 (bullet을 자르지 않음)")
    t33 = retriever.trim_hits([hit("SQ-06"), hit("TS-3.3")], "CLAUDE.md .claude/rules/ directory structure when to use")
    check(any(h.doc_id == "TS-3.3" and "reducing irrelevant context and token usage" in h.text for h in t33),
          "평가 8번 회귀 방지: TS-3.3의 '토큰 감소' bullet 유지")
    check("SQ-01" in ids and next(h for h in out if h.doc_id == "SQ-01").text == ch["SQ-01"].text, "샘플 문항(SQ)은 전문")
    check("TS-3.2" not in ids, "겹치는 bullet이 없는 TS 청크는 제거")
    ex = retriever.trim_hits([hit("TS-1.1"), hit("TS-3.2", how="exact")], q)
    check(ex[1].text == ch["TS-3.2"].text, "정확 조회(exact) hit은 전문")


def memory_checks() -> None:
    """v2-3: case_facts 추출(코드)과 턴 시작 요약 경계 (가짜 요약기, LLM 없음)."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from src import memory
    print("[memory] case_facts · 요약")
    f = memory.extract_facts("시험까지 7일 남았고 하루 2시간 공부할 수 있어")
    check(f.get("days") == 7 and f.get("hours_per_day") == 2.0, f"case_facts 추출: {f}")
    f = memory.extract_facts("도메인 2랑 TS 1.4 위주로 볼래. 일주일 남았어", f)
    check(f["days"] == 7 and f["hours_per_day"] == 2.0 and f["domains"] == [2] and f["task_ids"] == ["TS-1.4"],
          f"이전 값과 합침: {f}")
    check(memory.extract_facts("11월 7일에 봤던 문제", {}).get("days") is None, "날짜 표현(11월 7일)은 남은 일수로 보지 않음")

    msgs = []
    for i in range(5):                                  # 5턴, 턴마다 사용자 → 핸드오프 → 도구 → 답변
        msgs += [HumanMessage(f"질문 {i}", id=f"h{i}"),
                 AIMessage("", id=f"a{i}", name="supervisor",
                           tool_calls=[{"name": "transfer_to_concept_tutor", "args": {}, "id": f"c{i}"}]),
                 ToolMessage("ok", tool_call_id=f"c{i}", id=f"t{i}", name="transfer_to_concept_tutor"),
                 AIMessage(f"답변 {i}", id=f"r{i}", name="concept_tutor")]
    seen: list[str] = []

    async def fake(text: str) -> str:
        seen.append(text)
        return "사용자는 agentic loop를 물었다."

    async def broken(text: str) -> str:
        raise TimeoutError("bedrock")

    facts = {"days": 7, "hours_per_day": 2.0}
    new, info = asyncio.run(memory.compact(msgs, facts, summarize=fake, trigger=8, keep_turns=3))
    block, kept = new[0], new[1:]
    check(isinstance(block, SystemMessage) and "남은 일수: 7일" in block.content and "하루 공부 시간: 2시간" in block.content,
          "요약 블록에 case_facts가 코드로 렌더됨")
    start = [m.id for m in msgs].index("h2")
    check([m.id for m in kept] == [m.id for m in msgs[start:]] and info == {"status": "ok", "removed": start, "kept": len(kept)},
          f"최근 3턴(사용자 입력 경계)은 그대로, 도구 호출 쌍 보존 {info}")
    check(seen and "질문 0" in seen[0] and "질문 2" not in seen[0], "요약 입력은 잘린 앞부분만")
    check(asyncio.run(memory.compact(msgs[:8], facts, summarize=fake, trigger=8)) is None, "기준 이하 길이면 요약하지 않음")
    failed = asyncio.run(memory.compact(msgs, facts, summarize=broken, trigger=8, keep_turns=3))
    check(failed == ([], {"status": "error", "error": "TimeoutError"}), "요약 LLM 실패 → 원래 메시지 유지 (빈 목록 + 오류 기록)")


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    guard_rules()
    input_gate()
    trimming()
    memory_checks()
    g3()
    chunks()
    allocation(progress())
    speed_paths()
    n_ok = sum(ok for ok, _ in results)
    print(f"\n===== smoke_p1: {n_ok}/{len(results)} PASS =====")
    for ok, msg in results:
        if not ok:
            print(f"  FAIL: {msg}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
