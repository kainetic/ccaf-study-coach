"""더미 사용자 풀이 기록 시드 (DESIGN §8, S1 후반) → data/study_log.json

실행:
    python scripts/seed_study_log.py             # demo_heavy·demo_new 생성 (이미 있으면 중단)
    python scripts/seed_study_log.py --force     # demo 사용자 기록을 다시 만듦 (다른 사용자는 보존)
    python scripts/seed_study_log.py --dry-run   # 집계만 출력, 파일 쓰기 없음

- demo_new: 기록 없음 (test_queries 5번 — 기록이 없으면 시험 비중만으로 출제)
- demo_heavy: 지난 2주(2026-10-21 ~ 11-03) 61회 풀이. 모든 도메인 3회 이상이고 약점이 분명하게 드러나게
  구성 (test_queries 7번 — get_weak_domains·allocate_study_time이 기록을 근거로 약점 제시).
  약점 점수 = 시험 비중 × (1 − 정답률) → D2(0.098) > D5(0.090) > D1(0.068) > D4(0.067) > D3(0.033).
  D5는 비중이 가장 낮지만 정답률이 낮아 2위 (정답률만 보는지, 비중×약점을 보는지 구분)
- 승인(approved) 문항만, 중복 없이, 고정 시드라 다시 돌려도 같은 결과. 계산은 코드로 (LLM 호출 없음)
"""
import argparse
import json
import random
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.schemas import QuizIssued, QuizItem, StudyAttempt, StudyLog, UserStudyLog  # noqa: E402

SEED = "ccaf-seed-v1"
HEAVY, NEW = "demo_heavy", "demo_new"
# 도메인 → (풀이 수, 정답 수)
PROFILE = {1: (16, 12), 2: (11, 5), 3: (12, 10), 4: (12, 8), 5: (10, 4)}
START = datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)     # 20:00 KST
DAYS = 14
MIN_ATTEMPTS = 3                                                # 이보다 적으면 "데이터 부족" (§3-2)


def pick_items(approved: list[QuizItem], domain: int, n: int, rng: random.Random) -> list[QuizItem]:
    """도메인 안에서 TS를 고르게 돌아가며 n개 (TS 순서·TS 안 문항 순서는 시드로 섞음)."""
    by_ts: dict[str, list[QuizItem]] = defaultdict(list)
    for i in sorted((i for i in approved if i.domain == domain), key=lambda i: i.quiz_id):
        by_ts[i.task_id].append(i)
    for items in by_ts.values():
        rng.shuffle(items)
    order = sorted(by_ts)
    rng.shuffle(order)
    out: list[QuizItem] = []
    while len(out) < n:
        for t in order:
            if by_ts[t] and len(out) < n:
                out.append(by_ts[t].pop())
    return out


def build_heavy(approved: list[QuizItem]) -> UserStudyLog:
    rng = random.Random(SEED)
    plan: list[tuple[QuizItem, bool]] = []
    for d, (n, n_ok) in PROFILE.items():
        items = pick_items(approved, d, n, rng)
        ok_idx = set(rng.sample(range(n), n_ok))
        plan += [(it, k in ok_idx) for k, it in enumerate(items)]
    rng.shuffle(plan)

    user = UserStudyLog()
    per_day: Counter = Counter()
    for k, (it, ok) in enumerate(plan):
        day = k * DAYS // len(plan)                             # 14일에 고르게 (하루 4~5문제)
        slot = per_day[day]
        per_day[day] += 1
        answered = START + timedelta(days=day, minutes=4 * slot + rng.randint(1, 3))
        choice = it.answer if ok else rng.choice([l for l in "ABCD" if l != it.answer])
        user.issued.append(QuizIssued(quiz_id=it.quiz_id, issued_at=(answered - timedelta(minutes=1)).isoformat()))
        user.attempts.append(StudyAttempt(quiz_id=it.quiz_id, task_id=it.task_id, domain=it.domain,
                                          focus_bullet_ids=it.focus_bullet_ids, choice=choice, correct=ok,
                                          answered_at=answered.isoformat()))
    return user


def weakness(user: UserStudyLog) -> list[tuple[int, int, float, float | None]]:
    """(도메인, 시도, 정답률, 약점 점수 또는 None=데이터 부족). 점수 내림차순."""
    rows = []
    for d, w in config.DOMAIN_WEIGHTS.items():
        mine = [a for a in user.attempts if a.domain == d]
        acc = sum(a.correct for a in mine) / len(mine) if mine else 0.0
        rows.append((d, len(mine), acc, round(w * (1 - acc), 3) if len(mine) >= MIN_ATTEMPTS else None))
    return sorted(rows, key=lambda r: (r[3] is None, -(r[3] or 0)))


def check_with_bank(log: StudyLog) -> None:
    """임시 사본으로 QuizBank가 demo_heavy에게 이미 푼 문항을 내지 않는지 확인 (실제 파일은 건드리지 않음)."""
    from src.quiz_bank_server import QuizBank
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "study_log.json"
        p.write_text(log.model_dump_json(indent=2), encoding="utf-8")
        bank = QuizBank(log_path=p)
        solved = {a.quiz_id for a in log.users[HEAVY].attempts}
        picks = [bank.get_quiz(HEAVY) for _ in range(10)]
        ids = [r["quiz"]["quiz_id"] for r in picks if r.get("status") == "ok"]
        doms = Counter(r["domain"] for r in picks if r.get("status") == "ok")
        new = bank.get_quiz(NEW)
        print(f"[check] demo_heavy 출제 10회: 이미 푼 문항 {sum(i in solved for i in ids)}개 · 도메인 {dict(sorted(doms.items()))}")
        print(f"[check] demo_new 출제: {new.get('status')} {new.get('quiz', {}).get('quiz_id')}")
        if any(i in solved for i in ids) or len(ids) != 10 or new.get("status") != "ok":
            raise SystemExit("[check] 실패")


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="demo 사용자 기록이 있어도 다시 만듦")
    ap.add_argument("--dry-run", action="store_true", help="집계만 출력")
    args = ap.parse_args()

    path = config.STUDY_LOG_PATH
    log = StudyLog.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else StudyLog()
    existing = [u for u in (HEAVY, NEW) if u in log.users and (log.users[u].attempts or log.users[u].issued)]
    if existing and not (args.force or args.dry_run):
        print(f"[seed] {existing} 기록이 이미 있음 — 다시 만들려면 --force", file=sys.stderr)
        return 1

    approved = [i for i in (QuizItem.model_validate(d) for d in json.loads(config.QUIZ_POOL_PATH.read_text(encoding="utf-8")))
                if i.status == "approved"]
    heavy = build_heavy(approved)
    ids = [a.quiz_id for a in heavy.attempts]
    assert len(ids) == len(set(ids)) == sum(n for n, _ in PROFILE.values()), "중복 또는 개수 오류"
    log.users[HEAVY], log.users[NEW] = heavy, UserStudyLog()

    n_ok = sum(a.correct for a in heavy.attempts)
    print(f"[seed] {HEAVY}: {len(heavy.attempts)}회 · 정답 {n_ok} ({n_ok / len(heavy.attempts):.0%}) · "
          f"{heavy.attempts[0].answered_at[:10]} ~ {heavy.attempts[-1].answered_at[:10]} · "
          f"TS {len({a.task_id for a in heavy.attempts})}개")
    print("[seed] 도메인  시도  정답률  약점 점수(비중×(1−정답률))")
    for rank, (d, n, acc, score) in enumerate(weakness(heavy), start=1):
        print(f"  {rank}. D{d}   {n:>4}  {acc:>5.0%}  {score if score is not None else '데이터 부족'}")
    print(f"[seed] {NEW}: 기록 없음")
    check_with_bank(log)

    if args.dry_run:
        print("[seed] --dry-run: 파일을 쓰지 않음")
        return 0
    if path.is_file():
        shutil.copyfile(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(log.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    path.with_suffix(".json.bak").unlink(missing_ok=True)
    print(f"[seed] {path.relative_to(config.ROOT).as_posix()} 저장 (다른 사용자 {len(log.users) - 2}명 보존)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
