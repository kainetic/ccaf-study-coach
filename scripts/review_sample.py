"""사람 표본 검토용 CSV 생성 (DESIGN §5-2, TS 5.5 층화 표본) → evaluation/human_review_sample.csv

실행:
    python scripts/review_sample.py           # 표본 추출 + CSV 생성
    python scripts/review_sample.py --force   # 검토 입력이 있는 기존 CSV도 덮어씀

층 = 도메인 × {재시도 없음(review_retries=0), 재시도 있음}. 표본 수는 재시도 없음 층 10%, 재시도 층 20% (올림).
층마다 repair 문항(생성 단계 길이 repair 또는 승인 후 정답 길이 재조정)을 최소 1개 먼저 넣고, 나머지는 층 안에서
고정 시드로 무작위 추출한다 (재현 가능). LLM 호출 없음.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config  # noqa: E402
from src.quiz_gen import correct_vs_longest, seeded_rng, short_model  # noqa: E402
from src.schemas import LABELS, QuizItem  # noqa: E402
from build_quiz_pool import POOL_PATH  # noqa: E402

OUT_PATH = config.ROOT / "evaluation" / "human_review_sample.csv"
SEED = "ccaf-review-v1"
RATE_FIRST, RATE_RETRY = 0.10, 0.20
REVIEW_COLS = ["verdict", "error_type", "comment"]   # 검토자가 채움: verdict = ok | needs_fix


def is_repaired(item: QuizItem) -> bool:
    return any(p == "answer_too_long:repaired" or p == "length_cue:rebalanced" for p in item.detected_patterns)


def stratum(item: QuizItem) -> str:
    return f"D{item.domain}-{'retry' if item.review_retries > 0 else 'first'}"


def draw(items: list[QuizItem], rate: float, key: str) -> tuple[list[QuizItem], bool]:
    """층 하나에서 추출. repair 문항 1개를 먼저 넣는다. 반환: (표본, repair 포함 여부)."""
    if not items:
        return [], False
    rng = seeded_rng(SEED, key)
    pool = sorted(items, key=lambda i: i.quiz_id)
    k = max(1, math.ceil(rate * len(pool)))
    picked: list[QuizItem] = []
    repaired = [i for i in pool if is_repaired(i)]
    if repaired:
        picked.append(rng.choice(repaired))
    rest = [i for i in pool if i not in picked]
    picked += rng.sample(rest, min(len(rest), k - len(picked)))
    return sorted(picked, key=lambda i: i.quiz_id), bool(repaired)


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="검토 입력이 있는 기존 CSV도 덮어씀")
    args = ap.parse_args()

    if OUT_PATH.is_file() and not args.force:
        rows = list(csv.DictReader(OUT_PATH.open(encoding="utf-8-sig")))
        if any(r.get(c, "").strip() for r in rows for c in REVIEW_COLS):
            print(f"[sample] {OUT_PATH.name}에 검토 입력이 있음 — 보호를 위해 중단 (--force로 덮어쓰기)", file=sys.stderr)
            return 1

    items =[QuizItem.model_validate(d) for d in json.loads(POOL_PATH.read_text(encoding="utf-8"))]
    approved = [i for i in items if i.status == "approved"]

    sample: list[tuple[str, QuizItem]] = []
    print("[sample] 층        승인  표본  repair 포함")
    for d in range(1, 6):
        for kind, rate in (("first", RATE_FIRST), ("retry", RATE_RETRY)):
            key = f"D{d}-{kind}"
            members = [i for i in approved if stratum(i) == key]
            picked, has_rep = draw(members, rate, key)
            n_rep = sum(map(is_repaired, picked))
            print(f"  {key:<10} {len(members):>4}  {len(picked):>4}  {n_rep}{'' if has_rep else ' (층에 repair 문항 없음)'}")
            sample += [(key, i) for i in picked]

    # 가이드 원문(세부 항목 문장)은 공개 저장소에 넣지 않으므로 열로 두지 않는다 (DESIGN §2-4).
    # 검토자는 focus_bullet_ids로 로컬 data/guide_bullets.jsonl 또는 가이드 PDF를 참조한다.
    fields = (["quiz_id", "stratum", "domain", "task_id", "focus_bullet_ids", "scenario_id",
               "stem"] + [f"option_{l}" for l in LABELS] + ["answer"] + [f"rationale_{l}" for l in LABELS]
              + ["source_excerpt", "review_retries", "repaired", "rebalanced", "correct_vs_longest",
                 "generator_model"] + REVIEW_COLS)
    with OUT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for key, i in sample:
            opts = {o.label: o for o in i.options}
            w.writerow({
                "quiz_id": i.quiz_id, "stratum": key, "domain": i.domain, "task_id": i.task_id,
                "focus_bullet_ids": " ".join(i.focus_bullet_ids),
                "scenario_id": f"SC-{i.scenario_id}", "stem": i.stem,
                **{f"option_{l}": opts[l].text for l in LABELS}, "answer": i.answer,
                **{f"rationale_{l}": opts[l].rationale for l in LABELS},
                "source_excerpt": i.source_excerpt, "review_retries": i.review_retries,
                "repaired": "Y" if is_repaired(i) else "",
                "rebalanced": "Y" if "length_cue:rebalanced" in i.detected_patterns else "",
                "correct_vs_longest": f"{correct_vs_longest(i):.2f}",
                "generator_model": short_model(i.generator_model),
                **{c: "" for c in REVIEW_COLS},
            })
    print(f"[sample] {len(sample)}문항 → {OUT_PATH.relative_to(config.ROOT).as_posix()} (utf-8-sig, Excel용)")
    print("[sample] 검토 방법: verdict에 ok / needs_fix, needs_fix면 error_type·comment 기입 → python scripts/pool_report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
