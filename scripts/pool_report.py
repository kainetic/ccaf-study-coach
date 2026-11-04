"""문제 풀 품질 리포트 (DESIGN §5-2 사람 표본 검토, §9-3 지표) → evaluation/quiz_pool_report.md

실행: python scripts/pool_report.py

매니페스트·문제 풀·표본 CSV에서 지표를 계산해 리포트의 자동 절(<!-- AUTO:START --> ~ <!-- AUTO:END -->)만
다시 쓴다. 그 밖의 수기 절(예외 기록, 향후 개선, 사람 검토 결과)은 그대로 두므로 언제든 다시 실행해도 된다.
LLM·임베딩 호출 없음.
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config  # noqa: E402
from src.quiz_gen import REBALANCE_SELECT, correct_vs_longest, load_corpus, now_iso, short_model  # noqa: E402
from src.schemas import LABELS, QuizItem, QuizManifest  # noqa: E402
from build_quiz_pool import EXPECTED_TOTAL, LONGEST_TARGET, MANIFEST_PATH, POOL_PATH, longest_is_correct  # noqa: E402

REPORT_PATH = config.ROOT / "evaluation" / "quiz_pool_report.md"
SAMPLE_PATH = config.ROOT / "evaluation" / "human_review_sample.csv"
START, END = "<!-- AUTO:START -->", "<!-- AUTO:END -->"

MANUAL_TEMPLATE = """# 문제 풀 품질 리포트

> DESIGN §5-2(사람 표본 검토), §9-3(문제 풀 품질 지표). 자동 절은 `python scripts/pool_report.py`가 다시 쓴다.

{auto}

## 예외 기록 (수기)

## 향후 개선 (수기)

## 사람 표본 검토 결과 (수기 — 검토 후 기입)
"""


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.1%})" if b else f"{a}/0"


def build_auto(manifest: QuizManifest, pool: dict[str, QuizItem]) -> str:
    corpus = load_corpus()
    slots = manifest.slots
    approved = [i for i in pool.values() if i.status == "approved"]
    processed = [s for s in slots if s.status != "pending"]
    st = Counter(s.status for s in slots)
    L: list[str] = [START, "", f"_생성 시각: {now_iso()} · 매니페스트 updated_at {manifest.updated_at}_", ""]

    # 1. 규모·커버리지
    covered = {b for i in approved for b in i.focus_bullet_ids}
    uncovered = [b.bullet_id for b in corpus.bullets if b.bullet_id not in covered]
    L += ["## 1. 규모와 커버리지", "",
          "| 지표 | 값 | 목표 |", "|---|---|---|",
          f"| 승인 문항 | **{len(approved)}** | 270 (최소 250) |",
          f"| 슬롯 상태 | approved {st['approved']} · rejected {st['rejected']} · duplicate {st['duplicate']} · pending {st['pending']} | – |",
          f"| 승인율 (승인 ÷ 처리 슬롯) | {pct(st['approved'], len(processed))} | – |",
          f"| 세부 항목 커버리지 | **{pct(len(covered), len(corpus.bullets))}** | 228 이상 (95%) |",
          f"| 미커버 세부 항목 | {', '.join(uncovered) or '없음'} | – |", ""]

    # 2. 도메인·시나리오·슬롯 유형
    L += ["## 2. 도메인 · 시나리오 · 슬롯 유형별 승인", "",
          "| 도메인 | 승인 | 목표 |", "|---|---|---|"]
    L += [f"| D{d} | {sum(1 for i in approved if i.domain == d)} | {EXPECTED_TOTAL[d]} |" for d in range(1, 6)]
    sc = Counter(i.scenario_id for i in approved)
    L += ["", "시나리오별: " + " · ".join(f"SC-{k} {sc[k]}" for k in sorted(sc)), ""]
    by_type = Counter(s.slot_type for s in slots)
    ok_type = Counter(s.slot_type for s in slots if s.status == "approved")
    L += ["슬롯 유형별 승인/전체: " + " · ".join(f"{t} {ok_type[t]}/{by_type[t]}" for t in ("base", "extra", "fill", "exception") if by_type[t]), ""]

    # 3. 실패 유형·재시도·repair
    fails = Counter(p for s in slots for p in s.detected_patterns if ":" not in p)
    retries = Counter(i.review_retries for i in approved)
    rep, rep_ok = sum(s.repairs for s in slots), sum(s.repairs_ok for s in slots)
    L += ["## 3. 실패 유형 · 재시도 · repair", "",
          "실패 유형 (시도 단위, 검토 패턴 + 코드 세부 패턴): "
          + " · ".join(f"`{k}` {v}" for k, v in fails.most_common()), "",
          "승인 문항의 재시도 횟수: " + " · ".join(f"{k}회 {retries[k]}" for k in sorted(retries)), "",
          f"생성 단계 길이 repair 성공률: {pct(rep_ok, rep)}", ""]
    mism = [s for s in slots if "scenario_mismatch" in s.detected_patterns]
    md = Counter(s.domain for s in mism)
    L += [f"시나리오 불일치가 한 번이라도 난 슬롯: {pct(len(mism), len(processed))} — 도메인별 "
          + " · ".join(f"D{d} {md[d]}" for d in range(1, 6)), ""]

    # 4. 정답 위치·길이 단서
    ac = Counter(i.answer for i in approved)
    n = len(approved)
    longest = sum(1 for i in approved if longest_is_correct(i))
    over5 = sum(1 for i in approved if correct_vs_longest(i) > REBALANCE_SELECT)
    reb_ok = sum(1 for s in slots if "length_cue:rebalanced" in s.detected_patterns)
    reb_fail = sum(1 for s in slots if "length_cue:rebalance_failed" in s.detected_patterns)
    L += ["## 4. 정답 위치 · 길이 단서", "",
          "| 지표 | 값 | 목표 |", "|---|---|---|",
          "| 정답 위치 | " + " · ".join(f"{k} {ac[k]} ({ac[k] / n:.0%})" for k in LABELS) + " | 각 20~30% |",
          f"| **정답이 가장 긴 보기** (동률 포함) | **{pct(longest, n)}** | {LONGEST_TARGET:.0%} 이하 (우연 25%) |",
          f"| 정답이 가장 긴 오답보다 5% 넘게 긴 보기 (보조) | {pct(over5, n)} | – |",
          f"| 정답 길이 재조정 (`--rebalance-length`) | 성공 {reb_ok} · 실패(원본 유지) {reb_fail} | – |", ""]

    # 5. 중복·모델
    dups = [s.quiz_id for s in slots if s.status == "duplicate"]
    gen_c = Counter(short_model(i.generator_model) for i in approved)
    rev_c = Counter(short_model(s.reviewer_model) if s.reviewer_model else "(기록 전, us/sonnet-4-5)"
                    for s in slots if s.status == "approved")
    L += ["## 5. 중복 · 모델", "",
          f"중복 제거: {len(dups)}개 ({', '.join(dups) or '없음'})", "",
          "승인 문항 생성 모델: " + " · ".join(f"{k} {v}" for k, v in gen_c.most_common()), "",
          "승인 문항 검토 모델(마지막 검토): " + " · ".join(f"{k} {v}" for k, v in rev_c.most_common()), ""]

    # 6. 사람 표본
    L += ["## 6. 사람 표본 검토 표본", ""]
    if SAMPLE_PATH.is_file():
        rows = list(csv.DictReader(SAMPLE_PATH.open(encoding="utf-8-sig")))
        strata = Counter(r["stratum"] for r in rows)
        rep_s = Counter(r["stratum"] for r in rows if r["repaired"] == "Y")
        done = [r for r in rows if r["verdict"].strip()]
        bad = [r for r in done if r["verdict"].strip().lower() == "needs_fix"]
        L += [f"`{SAMPLE_PATH.relative_to(config.ROOT).as_posix()}` — {len(rows)}문항 (재시도 없음 10% + 재시도 20%, 층마다 repair 문항 최소 1개)", "",
              "| 층 | 표본 | repair 포함 |", "|---|---|---|"]
        L += [f"| {k} | {v} | {rep_s[k]} |" for k, v in sorted(strata.items())]
        L += ["", f"검토 완료 {len(done)}/{len(rows)} · needs_fix {len(bad)}"
              + (f" · 오류율 {len(bad) / len(done):.1%}" if done else "") + " (목표 0건, 최대 1건)", ""]
    else:
        L += ["표본 CSV 없음 — `python scripts/review_sample.py`로 생성", ""]
    L.append(END)
    return "\n".join(L)


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    manifest = QuizManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
    pool = {d["quiz_id"]: QuizItem.model_validate(d) for d in json.loads(POOL_PATH.read_text(encoding="utf-8"))}
    auto = build_auto(manifest, pool)
    if REPORT_PATH.is_file():
        text = REPORT_PATH.read_text(encoding="utf-8")
        if START in text and END in text:
            head, rest = text.split(START, 1)
            _, tail = rest.split(END, 1)
            text = head + auto + tail
        else:
            print(f"[report] {REPORT_PATH.name}에 자동 절 표시가 없음 — 덮어쓰지 않고 중단", file=sys.stderr)
            return 1
    else:
        text = MANUAL_TEMPLATE.format(auto=auto)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"[report] {REPORT_PATH.relative_to(config.ROOT).as_posix()} 갱신 (자동 절만)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
