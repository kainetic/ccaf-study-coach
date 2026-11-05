"""P1(S3) 오프라인 스모크 — LLM 호출 없이 코드로 강제하는 부분만 빠르게 확인한다.

실행: python evaluation/smoke_p1.py   (실패하면 종료 코드 1)
- 가드레일 1차 규칙: test_queries 20건 중 18(G1)·19(G2)·20(G4)만 걸리고 나머지는 통과. 출력 G4 백스톱(부정문 보존)
- G3: claims 검증 분기 (가져온 doc_id만 유효, 0개면 NO_EVIDENCE, [근거]는 코드가 붙임)
- get_chunk 정규화
- get_progress·get_weak_domains: demo_heavy 약점 D2 > D5, demo_new 데이터 부족
- allocate_study_time: 합계 = 일수 × 시간 (demo_heavy·demo_new·여러 형태), 시간표 일별 합 = 하루 시간
- 기본 일정(replan fallback)이 시간표 검증을 통과
풀이 기록(data/study_log.json)은 읽기만 한다.
"""
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


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    guard_rules()
    g3()
    chunks()
    allocation(progress())
    n_ok = sum(ok for ok, _ in results)
    print(f"\n===== smoke_p1: {n_ok}/{len(results)} PASS =====")
    for ok, msg in results:
        if not ok:
            print(f"  FAIL: {msg}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
