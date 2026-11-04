"""20건 인-아웃 평가 (DESIGN §9-1). Day 7 run_eval.py(ToolRecorder 방식) 확장.

실행:
    python evaluation/run_eval.py                    # 20건, label dev
    python evaluation/run_eval.py --label round1     # S4 round1 기준선
    python evaluation/run_eval.py --ids 7,13         # 일부만
    python evaluation/run_eval.py --url http://localhost:8000   # 실행 중인 서버 (기록 격리는 서버 쪽 책임)

판정 (case PASS = 셋 다 통과):
  ① 도구: expected_tools의 `;`는 모두 호출, `|`는 그중 하나 이상, 빈 값은 앱 도구 호출 0건 (trace의 tools step)
  ② forbidden: 코드 검사(출제 문항의 정답 노출) + LLM judge 항목별 위반 없음
     + guardrail 문항(note의 G1·G2·G4)은 고정 문구(src/guards.py)가 답변에 그대로 있어야 함
  ③ expected_traits: LLM judge 항목별 전부 충족

- 풀이 기록 격리: in-process 실행이면 data/study_log.json을 임시 파일로 복사해 CCAF_STUDY_LOG로 쓴다
  (출제 기록이 커밋된 파일에 남지 않음. demo_heavy 기록은 그대로 보임)
- user_id: note의 `user_id=…` (5번 demo_new, 7번 demo_heavy), 없으면 eval_user. 문항마다 새 thread
- 결과: evaluation/results/<label>_eval.json (커밋용: 판정·사유·도구·doc_id, 답변 본문과 가이드 원문 없음 — DESIGN §2-4)
        logs/eval/<label>_full.json (비커밋: 답변·contexts 원문 포함, `ragas_smoke.py --from`이 재사용)
"""
import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

DEFAULT_USER = "eval_user"
FACT_STEPS = ("guard", "route", "get_quiz", "submit", "progress", "allocate", "plan", "output_check", "tools")


def isolate_study_log() -> Path:
    """src를 import하기 전에 불러야 한다 (config가 import 시점에 CCAF_STUDY_LOG를 읽고, MCP 자식 프로세스가 환경을 물려받음)."""
    tmp = Path(tempfile.mkdtemp(prefix="ccaf_eval_")) / "study_log.json"
    shutil.copy(ROOT / "data" / "study_log.json", tmp)
    os.environ["CCAF_STUDY_LOG"] = str(tmp)
    return tmp


def split_items(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def load_cases(ids: set[str] | None) -> list[dict]:
    rows = list(csv.DictReader((ROOT / "evaluation" / "test_queries.csv").open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        if ids and r["id"] not in ids:
            continue
        m = re.search(r"user_id=([A-Za-z0-9_-]+)", r["note"])
        out.append({"id": r["id"], "category": r["category"], "input": r["input"],
                    "traits": split_items(r["expected_traits"]), "forbidden": split_items(r["forbidden"]),
                    "expected_tools": r["expected_tools"].strip(), "note": r["note"],
                    "user_id": m[1] if m else DEFAULT_USER})
    return out


# ---------------------------------------------------------------- 코드 판정

def check_tools(expected: str, called: list[str]) -> dict:
    if not expected:
        return {"ok": not called, "expected": "(도구 호출 없음)", "called": called}
    groups = [[t.strip() for t in g.split("|") if t.strip()] for g in expected.split(";") if g.strip()]
    missing = ["|".join(g) for g in groups if not any(t in called for t in g)]
    return {"ok": not missing, "expected": expected, "called": called, "missing": missing}


def code_checks(case: dict, answer: str, trace: list[dict], pool: dict) -> list[dict]:
    from src import guards
    out = []
    for s in trace:
        if s["step"] == "get_quiz" and s.get("status") == "ok":
            ans = pool[s["quiz_id"]]["answer"]
            leaked = re.search(rf"정답\s*(은|는|:|=)?\s*\(?\s*{ans}\b|answer\s*(is|:)\s*{ans}\b", answer, re.I)
            out.append({"check": f"정답 비노출 ({s['quiz_id']})", "ok": not leaked})
    if case["category"] == "guardrail":
        for rule, phrase in (("G1", guards.REFUSALS["G1"]), ("G2", guards.REFUSALS["G2"]), ("G4", guards.G4_CORRECTION)):
            if re.search(rf"\b{rule}\b", case["note"]):
                out.append({"check": f"{rule} 고정 문구", "ok": phrase in answer})
    return out


def facts_for_judge(trace: list[dict], contexts: list[dict]) -> dict:
    steps = [{k: v for k, v in s.items() if k != "t"} for s in trace if s["step"] in FACT_STEPS]
    return {"steps": steps, "retrieved_doc_ids": [c["doc_id"] for c in contexts]}


# ---------------------------------------------------------------- 실행

def make_client(url: str | None):
    if url:
        import httpx
        return None, httpx.Client(base_url=url, timeout=600)
    from fastapi.testclient import TestClient
    import src.api as api_mod
    ctx = TestClient(api_mod.api)
    return ctx, ctx.__enter__()


async def judge(case: dict, answer: str, facts: dict):
    from llm_judge import judge_case
    return await judge_case(case["input"], answer, case["traits"], case["forbidden"], facts)


def evaluate(case: dict, out: dict, latency: float, pool: dict) -> tuple[dict, dict]:
    answer, trace, contexts = out["answer"], out["trace"], out["contexts"]
    called = next((s["names"] for s in trace if s["step"] == "tools"), [])
    tools = check_tools(case["expected_tools"], called)
    code = code_checks(case, answer, trace, pool)
    try:
        verdict, judge_model = asyncio.run(judge(case, answer, facts_for_judge(trace, contexts)))
        traits = [{"item": t, "ok": v.ok, "reason": v.reason} for t, v in zip(case["traits"], verdict.traits)]
        forbidden = [{"item": f, "ok": v.ok, "reason": v.reason} for f, v in zip(case["forbidden"], verdict.forbidden)]
        judge_error = None
    except Exception as e:   # noqa: BLE001 — judge 실패는 그 문항 실패로 기록하고 계속
        traits = [{"item": t, "ok": False, "reason": "judge 실패"} for t in case["traits"]]
        forbidden = [{"item": f, "ok": False, "reason": "judge 실패"} for f in case["forbidden"]]
        judge_model, judge_error = None, f"{type(e).__name__}: {e}"[:300]
    llm = next((s for s in trace if s["step"] == "llm"), {})
    passed = tools["ok"] and all(c["ok"] for c in code) and all(t["ok"] for t in traits) and all(f["ok"] for f in forbidden)
    fail = ([f"도구: 누락 {tools.get('missing') or ''} 호출 {called}"] if not tools["ok"] else []) \
        + [f"코드: {c['check']}" for c in code if not c["ok"]] \
        + [f"trait: {t['item']} — {t['reason']}" for t in traits if not t["ok"]] \
        + [f"forbidden: {f['item']} — {f['reason']}" for f in forbidden if not f["ok"]]
    row = {"id": case["id"], "category": case["category"], "user_id": case["user_id"], "pass": passed,
           "fail_reasons": fail, "tools": tools, "code_checks": code, "traits": traits, "forbidden": forbidden,
           "route": [s["agent"] for s in trace if s["step"] == "route"],
           "guard": next(({k: s.get(k) for k in ("action", "rule", "layer")} for s in trace if s["step"] == "guard"), None),
           "get_quiz": [{k: s.get(k) for k in ("status", "quiz_id", "reason")} for s in trace if s["step"] == "get_quiz"],
           "g3": next(({k: s.get(k) for k in ("claims", "valid", "fallback", "excerpt_verbatim")}
                       for s in trace if s["step"] == "output_check" and s.get("rule") == "G3"), None),
           "retrieved_doc_ids": [c["doc_id"] for c in contexts], "answer_chars": len(answer),
           "latency_s": round(latency, 1), "llm_calls": llm.get("calls"), "tokens": {"input": llm.get("input"),
           "output": llm.get("output")}, "models": llm.get("models"), "judge_model": judge_model, "judge_error": judge_error}
    full = {**row, "input": case["input"], "answer": answer, "contexts": contexts, "trace": trace}
    return row, full


def summarize(rows: list[dict]) -> dict:
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["category"]][0] += r["pass"]
        by[r["category"]][1] += 1
    n_traits = sum(len(r["traits"]) for r in rows)
    models: dict[str, int] = defaultdict(int)
    for r in rows:
        for m, c in (r["models"] or {}).items():
            models[m] += c
    quiz = [q for r in rows for q in r["get_quiz"]]
    return {
        "passed": sum(r["pass"] for r in rows), "total": len(rows),
        "by_category": {k: {"passed": p, "total": n} for k, (p, n) in by.items()},
        "tools_ok": sum(r["tools"]["ok"] for r in rows),
        "traits_met": f"{sum(t['ok'] for r in rows for t in r['traits'])}/{n_traits}",
        "forbidden_clean": f"{sum(f['ok'] for r in rows for f in r['forbidden'])}/{sum(len(r['forbidden']) for r in rows)}",
        "get_quiz_no_match": f"{sum(q['status'] == 'no_match' for q in quiz)}/{len(quiz)}",
        "llm_calls": sum(r["llm_calls"] or 0 for r in rows),
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / max(1, len(rows)), 1),
        "models": dict(models),
    }


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="dev", help="결과 파일 이름 (예: round1)")
    ap.add_argument("--ids", help="쉼표로 구분한 문항 id (기본 전체)")
    ap.add_argument("--url", help="실행 중인 API 서버 주소 (없으면 in-process)")
    args = ap.parse_args()

    log_copy = None if args.url else isolate_study_log()
    from src import config  # noqa: E402 — 기록 격리 후 import

    cases = load_cases(set(args.ids.split(",")) if args.ids else None)
    pool = {d["quiz_id"]: d for d in json.loads(config.QUIZ_POOL_PATH.read_text(encoding="utf-8"))}
    print(f"[eval] {len(cases)}건 · label {args.label} · study_log {'서버 쪽' if args.url else log_copy}")

    rows, fulls = [], []
    ctx, client = make_client(args.url)
    try:
        for c in cases:
            t0 = time.perf_counter()
            r = client.post("/query", json={"question": c["input"], "user_id": c["user_id"],
                                            "thread_id": f"eval-{args.label}-{c['id']}-{uuid.uuid4().hex[:6]}"}, timeout=600)
            r.raise_for_status()
            row, full = evaluate(c, r.json(), time.perf_counter() - t0, pool)
            rows.append(row)
            fulls.append(full)
            print(f"  [{'PASS' if row['pass'] else 'FAIL'}] #{c['id']:>2} {c['category']:<9} "
                  f"route={row['route']} tools={row['tools']['called']} {row['latency_s']}s")
            for why in row["fail_reasons"]:
                print(f"         - {why[:160]}")
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    summary = summarize(rows)
    meta = {"label": args.label, "run_at": time.strftime("%Y-%m-%d %H:%M:%S"), "judge_version": _judge_version(),
            "model": config.MODEL_ID, "summary": summary}
    res_dir, full_dir = ROOT / "evaluation" / "results", ROOT / "logs" / "eval"
    res_dir.mkdir(parents=True, exist_ok=True)
    full_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / f"{args.label}_eval.json").write_text(json.dumps({**meta, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (full_dir / f"{args.label}_full.json").write_text(json.dumps({**meta, "cases": fulls}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n===== {args.label}: {summary['passed']}/{summary['total']} PASS =====")
    for k, v in summary["by_category"].items():
        print(f"  {k:<9} {v['passed']}/{v['total']}")
    print(f"  도구 {summary['tools_ok']}/{summary['total']} · traits {summary['traits_met']} · forbidden {summary['forbidden_clean']}"
          f" · no_match {summary['get_quiz_no_match']} · LLM {summary['llm_calls']}회 · 평균 {summary['avg_latency_s']}s")
    print(f"  모델 {summary['models']}")
    print(f"[eval] evaluation/results/{args.label}_eval.json · logs/eval/{args.label}_full.json")
    return 0


def _judge_version() -> str:
    from llm_judge import JUDGE_VERSION
    return JUDGE_VERSION


if __name__ == "__main__":
    sys.exit(main())
