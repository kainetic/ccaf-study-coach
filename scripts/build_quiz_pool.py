"""문제 풀 사전 생성 (DESIGN §5-2). 오프라인 전용 — 런타임에는 문제를 만들지 않는다.

실행:
    python scripts/build_quiz_pool.py --dry-run          # 슬롯 270개 배정만 확인 (LLM 호출·파일 쓰기 없음)
    python scripts/build_quiz_pool.py --pilot 10         # 파일럿: 도메인당 2슬롯을 매번 새로 생성
    python scripts/build_quiz_pool.py --resume           # pending 슬롯을 순서대로 (기본 240 → 추가 30 → 보충)
    python scripts/build_quiz_pool.py --resume --limit 20
    python scripts/build_quiz_pool.py --fill-gaps        # 승인 문제가 없는 세부 항목만 보충 (기본/추가가 모두 끝난 뒤)
    python scripts/build_quiz_pool.py --reset-stale      # 이전 prompt_version으로 처리된 슬롯을 pending으로 (LLM 호출 없음)
    python scripts/build_quiz_pool.py --rebalance-length # 승인 후 정답 길이 재조정 (정답이 가장 긴 오답보다 5% 넘게 긴 문항)
    python scripts/build_quiz_pool.py --exception-slot TS-5.2-K2:1   # 미커버 항목에 시나리오 고정 예외 슬롯
    공통 옵션: --concurrency N(기본 2), --no-dedupe. 세부는 --help, DESIGN §5-2

상태는 슬롯 하나가 끝날 때마다 data/quiz_pool_manifest.json, data/quiz_pool.json에 원자적으로 저장된다.
중단(Ctrl+C)하면 진행 중이던 슬롯은 pending으로 남고 --resume으로 이어진다.
"""
import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402  (.env 로드)
from src.prompts import prompt_version  # noqa: E402
from src.quiz_gen import (  # noqa: E402
    DOMAIN_SCENARIOS, Corpus, CorpusError, ModelPool, QuizGenerator, TransientError, check_domain_scenarios,
    short_model,
    FORBIDDEN_SCENARIOS, ScenarioRanker, dedupe, keywords, load_corpus, mismatched_scenarios, now_iso,
    order_scenarios, pair_extra_bullets, pick_fewshot, rejected_for_scenario, seeded_rng,
    REBALANCE_SELECT, correct_vs_longest,
)
from src.schemas import LABELS, ManifestSlot, QuizItem, QuizManifest  # noqa: E402

POOL_PATH = config.DATA_DIR / "quiz_pool.json"
MANIFEST_PATH = config.DATA_DIR / "quiz_pool_manifest.json"

EXTRA_PER_DOMAIN = {1: 8, 2: 5, 3: 6, 4: 6, 5: 5}          # 추가 슬롯 30 (DESIGN §5-2 표)
EXPECTED_TOTAL = {1: 56, 2: 48, 3: 55, 4: 53, 5: 58}         # 기본 + 추가
TARGET_SEED = "ccaf-pool-v1"
# 세부 항목 원문이 특정 시나리오를 전제하는 경우 순환 대신 고정 시작 시나리오 (DESIGN §5-2).
# 순환 위치(cursor)는 그대로 진행하므로 다른 슬롯의 배정은 바뀌지 않는다.
SCENARIO_OVERRIDES = {
    "TS-1.4-S2": 1,     # "multi-concern customer requests" → SC-1 고객 지원
}
COVERAGE_TARGET = 228                                        # 240의 95%
LONGEST_TARGET = 0.35                                        # 정답이 가장 긴 보기 비율 상한 (DESIGN §9-3)
# 사후 작업(정답 길이 재조정·예외 슬롯) 모델 순서: us Sonnet 4.5 할당량을 아끼려고 제외, global 4.5 우선
MAINT_MODEL_CHAIN = [m for m in config.QUIZ_GEN_MODEL_CHAIN if m != config.QUIZ_GEN_MODEL_ID]
TRANSIENT_STOP = 3                                           # 일시 오류로 연속 실패한 슬롯이 이 수가 되면 실행 중단
DEFAULT_CONCURRENCY = 2                                      # 공용 Bedrock 할당량에서 동시 4개는 쓰로틀링이 남


# ---------------------------------------------------------------- 슬롯 배정

def ts_key(task_id: str) -> tuple[int, int]:
    major, minor = task_id.removeprefix("TS-").split(".")
    return int(major), int(minor)


def bullet_key(bullet_id: str) -> tuple[int, int]:
    suffix = bullet_id.rsplit("-", 1)[1]                     # "K2"
    return (0 if suffix[0] == "K" else 1), int(suffix[1:])


def qid(task_id: str, suffix: str) -> str:
    return f"Q-{task_id.removeprefix('TS-')}-{suffix}"


def fill_target(quiz_id: str) -> str:
    return LABELS[int(hashlib.sha256(quiz_id.encode()).hexdigest(), 16) % 4]


def build_slots(corpus: Corpus) -> list[ManifestSlot]:
    """기본 240 + 추가 30. 같은 코퍼스면 항상 같은 결과."""
    slots: list[ManifestSlot] = []
    cursor: dict[str, int] = {}                              # TS별 시나리오 순환 위치

    def add(slot_type, task_id, domain, focus, suffix):
        cands = DOMAIN_SCENARIOS[domain]
        i = cursor.get(task_id, 0)
        cursor[task_id] = i + 1
        scenario = cands[i % len(cands)]
        if slot_type == "base" and focus[0] in SCENARIO_OVERRIDES:
            scenario = SCENARIO_OVERRIDES[focus[0]]
        slots.append(ManifestSlot(
            quiz_id=qid(task_id, suffix), slot_type=slot_type, task_id=task_id, domain=domain,
            focus_bullet_ids=focus, scenario_id=scenario, scenario_candidates=cands,
            target_answer="A", order=len(slots),
        ))

    ts_ids = sorted(corpus.bullets_by_ts, key=ts_key)
    for tid in ts_ids:
        for b in sorted(corpus.bullets_by_ts[tid], key=lambda b: bullet_key(b.bullet_id)):
            add("base", tid, b.domain, [b.bullet_id], b.bullet_id.rsplit("-", 1)[1])

    for d, n_extra in EXTRA_PER_DOMAIN.items():
        # 세부 항목 수가 적은 TS부터 라운드로빈 배정
        ranked = sorted((t for t in ts_ids if corpus.bullets_by_ts[t][0].domain == d),
                        key=lambda t: (len(corpus.bullets_by_ts[t]), ts_key(t)))
        counts = Counter(ranked[k % len(ranked)] for k in range(n_extra))
        for tid in sorted(counts, key=ts_key):
            # 같은 TS 안에서 키워드 겹침이 가장 큰 K–S 쌍 (이미 쓴 쌍은 피함)
            pairs = pair_extra_bullets(corpus.bullets_by_ts[tid], counts[tid])
            if len(pairs) < counts[tid]:
                raise CorpusError(f"{tid}: K/S 쌍이 부족해 추가 슬롯 {counts[tid]}개를 만들 수 없음")
            for j, (k_id, s_id, _) in enumerate(pairs, 1):
                add("extra", tid, d, [k_id, s_id], f"X{j}")

    # 정답 위치 목표값: A~D 균등 리스트를 고정 시드로 섞어 순서대로 배정
    n = len(slots)
    labels = [LABELS[i % 4] for i in range(n)]
    seeded_rng(TARGET_SEED).shuffle(labels)
    for s, lab in zip(slots, labels):
        s.target_answer = lab
    return slots


def plan_errors(slots: list[ManifestSlot], corpus: Corpus) -> list[str]:
    errs = check_domain_scenarios(corpus)
    per_dom = Counter(s.domain for s in slots if s.slot_type in ("base", "extra"))
    for d, exp in EXPECTED_TOTAL.items():
        if per_dom.get(d, 0) != exp:
            errs.append(f"D{d}: 슬롯 {per_dom.get(d, 0)} (기대 {exp})")
    ids = [s.quiz_id for s in slots]
    if len(set(ids)) != len(ids):
        errs.append("quiz_id 중복")
    for s in slots:
        bad = FORBIDDEN_SCENARIOS.get(s.task_id, set()) & ({s.scenario_id} | set(s.scenario_candidates))
        if bad:
            errs.append(f"{s.quiz_id}: 금지 조합 {s.task_id} × SC-{sorted(bad)}")
    for s in slots:
        if s.scenario_id not in DOMAIN_SCENARIOS[s.domain]:
            errs.append(f"{s.quiz_id}: SC-{s.scenario_id}는 D{s.domain} 배정 시나리오가 아님")
        for b in s.focus_bullet_ids:
            if b not in corpus.bullet_by_id:
                errs.append(f"{s.quiz_id}: 알 수 없는 세부 항목 {b}")
    return errs


# ---------------------------------------------------------------- 파일 입출력

def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def summarize(manifest: QuizManifest) -> dict[str, int]:
    c = Counter(s.status for s in manifest.slots)
    return {k: c.get(k, 0) for k in ("pending", "approved", "rejected", "duplicate", "needs_fix")}


def save(manifest: QuizManifest, pool: dict[str, QuizItem]) -> None:
    manifest.updated_at = now_iso()
    manifest.summary = summarize(manifest)
    atomic_write(MANIFEST_PATH, manifest.model_dump_json(indent=2))
    order = {s.quiz_id: s.order for s in manifest.slots}
    items = sorted(pool.values(), key=lambda i: order.get(i.quiz_id, 10**9))
    atomic_write(POOL_PATH, json.dumps([i.model_dump() for i in items], ensure_ascii=False, indent=2))


def sync_definitions(manifest: QuizManifest, built: list[ManifestSlot], allow_processed: bool) -> None:
    """base/extra 슬롯 정의(focus_bullet_ids, scenario_candidates)를 현재 build_slots 결과에 맞춘다.
    pending 슬롯은 갱신하고, 처리된 슬롯의 정의가 바뀌었으면 오류 (--reset-stale에서는 허용)."""
    fresh = {s.quiz_id: s for s in built}
    updated, conflicts = [], []
    for s in manifest.slots:
        f = fresh.get(s.quiz_id)
        if f is None or (s.focus_bullet_ids, s.scenario_candidates) == (f.focus_bullet_ids, f.scenario_candidates):
            continue
        if s.status == "pending" or allow_processed:
            s.focus_bullet_ids, s.scenario_candidates = f.focus_bullet_ids, f.scenario_candidates
            if s.status == "pending":
                s.scenario_id = f.scenario_id
            updated.append(s.quiz_id)
        else:
            conflicts.append(s.quiz_id)
    if conflicts:
        raise CorpusError(f"처리된 슬롯의 정의가 바뀜 {conflicts[:5]}{'…' if len(conflicts) > 5 else ''} "
                          "— --reset-stale 후 다시 실행")
    if updated:
        print(f"[manifest] 슬롯 정의 동기화 {len(updated)}개: {', '.join(updated[:6])}{' …' if len(updated) > 6 else ''}")


def load_state(corpus: Corpus, allow_processed_change: bool = False) -> tuple[QuizManifest, dict[str, QuizItem]]:
    built = build_slots(corpus)
    if MANIFEST_PATH.is_file():
        manifest = QuizManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
        have = {s.quiz_id for s in manifest.slots if s.slot_type != "fill"}
        want = {s.quiz_id for s in built}
        if have != want:
            raise CorpusError(f"매니페스트 슬롯이 현재 코퍼스와 다름 (누락 {len(want - have)}, 초과 {len(have - want)}). "
                              "가이드가 바뀌었으면 매니페스트를 백업 후 다시 만들 것")
        sync_definitions(manifest, built, allow_processed_change)
    else:
        ts = now_iso()
        manifest = QuizManifest(guide_version=config.GUIDE_VERSION, generator_model=config.QUIZ_GEN_MODEL_ID,
                                created_at=ts, updated_at=ts, slots=built)
        print(f"[manifest] 새로 생성: 슬롯 {len(built)}개 → {MANIFEST_PATH.name}")
    pool: dict[str, QuizItem] = {}
    if POOL_PATH.is_file():
        for d in json.loads(POOL_PATH.read_text(encoding="utf-8") or "[]"):
            it = QuizItem.model_validate(d)
            pool[it.quiz_id] = it
    return manifest, pool


# ---------------------------------------------------------------- 대상 선정

def select_pilot(slots: list[ManifestSlot], n: int) -> list[ManifestSlot]:
    """도메인마다 서로 다른 TS의 기본 슬롯에서 K/S를 번갈아, 시나리오가 겹치지 않게 고른다."""
    picks = []
    for d in range(1, 6):
        per_d = n // 5 + (1 if d <= n % 5 else 0)
        base = [s for s in slots if s.slot_type == "base" and s.domain == d]
        ts_ids = sorted({s.task_id for s in base}, key=ts_key)
        used_sc: set[int] = set()
        for k in range(per_d):
            tid = ts_ids[(k * len(ts_ids)) // max(per_d, 1) % len(ts_ids)]
            kind = "K" if k % 2 == 0 else "S"
            cands = [s for s in base if s.task_id == tid and s.quiz_id.rsplit("-", 1)[1].startswith(kind)
                     and s not in picks]
            pick = next((s for s in cands if s.scenario_id not in used_sc), cands[0] if cands else None)
            if pick:
                picks.append(pick)
                used_sc.add(pick.scenario_id)
    return picks


def reset_stale(manifest: QuizManifest, pool: dict[str, QuizItem], built: list[ManifestSlot],
                pilot_ids: set[str]) -> tuple[list[str], list[str]]:
    """현재 prompt_version과 다른 버전으로 처리된 슬롯을 되돌린다 (LLM 호출 없음).
    base/extra는 pending으로 초기화하고, fill 슬롯은 삭제한다 (기본 슬롯이 다시 실패하면 --fill-gaps가 다시 만든다)."""
    pv = prompt_version()
    fresh = {s.quiz_id: s for s in built}
    reset, removed = [], []
    for s in list(manifest.slots):
        if s.status == "pending" or s.prompt_version == pv:
            continue
        pool.pop(s.quiz_id, None)
        if s.slot_type in ("fill", "exception"):
            manifest.slots.remove(s)
            removed.append(s.quiz_id)
            continue
        f = fresh[s.quiz_id]
        s.status, s.attempts, s.repairs, s.repairs_ok = "pending", 0, 0, 0
        s.detected_patterns, s.last_errors = [], []
        s.focus_bullet_ids, s.scenario_candidates, s.scenario_id = f.focus_bullet_ids, f.scenario_candidates, f.scenario_id
        s.prompt_version, s.updated_at = None, now_iso()
        reset.append(s.quiz_id)
    for s in manifest.slots:
        s.pilot = s.quiz_id in pilot_ids
    return reset, removed


def stale_count(manifest: QuizManifest) -> int:
    pv = prompt_version()
    return sum(1 for s in manifest.slots if s.status != "pending" and s.prompt_version != pv)


def add_fill_slots(manifest: QuizManifest, pool: dict[str, QuizItem], corpus: Corpus,
                   ranker=None) -> list[ManifestSlot]:
    """공백 = 승인 문항의 focus에 없고 그 항목을 포함한 pending 슬롯도 없는 세부 항목. 항목당 base+fill 최대 2슬롯.
    직전 슬롯이 scenario_mismatch로 최종 탈락했으면 primary 시나리오 제한을 풀고, ranker(LLM)가 6개 중
    세부 항목 내용에 가장 맞다고 고른 시나리오부터 쓴다 (금지 조합 제외, 이미 불일치한 시나리오는 맨 뒤)."""
    covered = {b for it in pool.values() if it.status == "approved" for b in it.focus_bullet_ids}
    pending = {b for s in manifest.slots if s.status == "pending" for b in s.focus_bullet_ids}
    existing = {s.quiz_id for s in manifest.slots}
    next_order = max(s.order for s in manifest.slots) + 1
    new = []
    for b in sorted(corpus.bullets, key=lambda b: (ts_key(b.task_id), bullet_key(b.bullet_id))):
        if b.bullet_id in covered or b.bullet_id in pending:
            continue
        own = [s for s in manifest.slots if s.slot_type in ("base", "fill") and s.focus_bullet_ids == [b.bullet_id]]
        quiz_id = qid(b.task_id, b.bullet_id.rsplit("-", 1)[1] + "-F")
        if len(own) >= 2 or quiz_id in existing:
            continue
        cands = DOMAIN_SCENARIOS[b.domain]
        if own and rejected_for_scenario(own[-1]) and ranker is not None:
            failed = mismatched_scenarios(own[-1])
            ranking, reason = ranker.rank(b)
            cands = order_scenarios(ranking, b.task_id, failed)
            sc = cands[0]
            print(f"[fill-gaps] {quiz_id}: 시나리오 불일치 탈락({', '.join(f'SC-{n}' for n in failed)}) "
                  f"→ 순위 {['SC-%d' % n for n in cands]} — {reason}")
        elif not own:
            sc = cands[0]
        else:
            # 직전 슬롯의 마지막 시나리오가 시나리오 문제로 실패했거나 중복이면 다음 후보, 아니면 그대로 재사용
            prev, pats = own[-1].scenario_id, own[-1].detected_patterns
            last = pats[-1].split(":")[0] if pats else ""
            if last in ("scenario_mismatch", "duplicate_of") and prev in cands:
                sc = cands[(cands.index(prev) + 1) % len(cands)]
            else:
                sc = prev if prev in cands else cands[0]
        slot = ManifestSlot(quiz_id=quiz_id, slot_type="fill", task_id=b.task_id, domain=b.domain,
                            focus_bullet_ids=[b.bullet_id], scenario_id=sc, scenario_candidates=cands,
                            target_answer=fill_target(quiz_id), order=next_order)
        next_order += 1
        manifest.slots.append(slot)
        new.append(slot)
    return new



def add_exception_slot(manifest: QuizManifest, pool: dict[str, QuizItem], corpus: Corpus, spec: str) -> ManifestSlot:
    """미커버 세부 항목 하나에 시나리오를 고정한 예외 슬롯 (항목당 base+fill 2슬롯 규칙의 명시적 예외, DESIGN §5-2).
    spec 예: "TS-5.2-K2:1" → Q-5.2-K2-E, scenario_candidates=[1] (재시도에서도 SC-1)."""
    try:
        bid, sc_txt = spec.rsplit(":", 1)
        sc = int(sc_txt)
    except ValueError:
        raise CorpusError(f"형식은 BULLET_ID:SC (예: TS-5.2-K2:1), 입력 {spec!r}")
    b = corpus.bullet_by_id.get(bid)
    if b is None:
        raise CorpusError(f"알 수 없는 세부 항목 {bid}")
    if sc not in range(1, 7) or sc in FORBIDDEN_SCENARIOS.get(b.task_id, set()):
        raise CorpusError(f"{b.task_id} × SC-{sc}는 허용되지 않는 조합")
    covered = {x for it in pool.values() if it.status == "approved" for x in it.focus_bullet_ids}
    if bid in covered:
        raise CorpusError(f"{bid}는 이미 승인 문항이 있음 — 예외 슬롯은 미커버 항목에만")
    quiz_id = qid(b.task_id, bid.rsplit("-", 1)[1] + "-E")
    if any(s.quiz_id == quiz_id for s in manifest.slots):
        raise CorpusError(f"{quiz_id}가 이미 있음")
    slot = ManifestSlot(quiz_id=quiz_id, slot_type="exception", task_id=b.task_id, domain=b.domain,
                        focus_bullet_ids=[bid], scenario_id=sc, scenario_candidates=[sc],
                        target_answer=fill_target(quiz_id), order=max(s.order for s in manifest.slots) + 1)
    manifest.slots.append(slot)
    return slot


def rebalance_targets(manifest: QuizManifest, pool: dict[str, QuizItem]) -> list[QuizItem]:
    """정답이 가장 긴 오답보다 5% 넘게 긴 승인 문항 (이미 재조정을 시도한 문항은 제외 → 재실행해도 멱등)."""
    order = {s.quiz_id: s.order for s in manifest.slots}
    out = [it for it in pool.values()
           if it.status == "approved" and correct_vs_longest(it) > REBALANCE_SELECT
           and not any(p.startswith("length_cue:") for p in it.detected_patterns)]
    return sorted(out, key=lambda it: order.get(it.quiz_id, 10**9))


def run_rebalance(gen: QuizGenerator, manifest: QuizManifest, pool: dict[str, QuizItem],
                  targets: list[QuizItem], concurrency: int) -> bool:
    """재조정 실행. 성공하면 보기 교체, 실패하면 원래 문항 유지. 일시 오류로 연속 실패하면 run_slots와 같이 중단."""
    if not targets:
        print("[rebalance] 처리할 문항 없음")
        return True
    by_id = {s.quiz_id: s for s in manifest.slots}
    ex = ThreadPoolExecutor(max_workers=concurrency)
    futs = {ex.submit(gen.rebalance_item, by_id[it.quiz_id], it): it for it in targets}
    done = ok_n = 0
    streak, stopped = 0, False
    try:
        for fut in as_completed(futs):
            it = futs[fut]
            if fut.cancelled():
                continue
            done += 1
            s = by_id[it.quiz_id]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 — 문항은 그대로, 다음 실행에서 다시 대상
                print(f"  [{done}/{len(targets)}] {it.quiz_id}: 오류 → 그대로 유지 ({type(e).__name__}: {str(e)[:120]})")
                streak = streak + 1 if isinstance(e, TransientError) else 0
                if streak >= TRANSIENT_STOP and not stopped:
                    stopped = True
                    n_cancel = sum(f.cancel() for f in futs)
                    print(f"[rebalance] 일시 오류 연속 {streak}회 — 중단 (취소 {n_cancel}개)", flush=True)
                continue
            streak = 0
            if r.ok:
                ok_n += 1
                pool[it.quiz_id] = r.item
                s.detected_patterns = s.detected_patterns + ["length_cue:rebalanced"]
                s.reviewer_model = r.reviewer_model
                print(f"  [{done}/{len(targets)}] {it.quiz_id}: 재조정 {r.before:.2f} → {r.after:.2f} "
                      f"(호출 {r.calls}, 검토 {short_model(r.reviewer_model)})")
            else:
                pool[it.quiz_id] = it.model_copy(
                    update={"detected_patterns": it.detected_patterns + ["length_cue:rebalance_failed"]})
                s.detected_patterns = s.detected_patterns + ["length_cue:rebalance_failed"]
                print(f"  [{done}/{len(targets)}] {it.quiz_id}: 실패 → 원래 문항 유지 ({r.reason})")
            s.updated_at = now_iso()
            save(manifest, pool)
    except KeyboardInterrupt:
        save(manifest, pool)
        print("\n[rebalance] 중단 — 처리된 문항은 저장됨. 다시 실행하면 나머지만 처리", flush=True)
        os._exit(130)
    ex.shutdown(wait=True)
    print(f"[rebalance] 성공 {ok_n} / 처리 {done} / 대상 {len(targets)}")
    return not stopped

# ---------------------------------------------------------------- 실행

def run_slots(gen: QuizGenerator, manifest: QuizManifest, pool: dict[str, QuizItem],
              targets: list[ManifestSlot], concurrency: int, verbose: bool) -> bool:
    """슬롯들을 실행한다. 일시 오류(쓰로틀링 등)로 연속 TRANSIENT_STOP개 슬롯이 실패하면
    새 슬롯 제출을 멈추고 진행 중인 것만 마무리한 뒤 False를 반환한다 (나머지는 pending 유지)."""
    if not targets:
        print("[run] 처리할 슬롯 없음")
        return True
    pv = prompt_version()
    print(f"[run] 슬롯 {len(targets)}개, 동시 {concurrency}, prompt_version {pv}")

    def avoid_for(s: ManifestSlot) -> list[str]:
        if s.slot_type not in ("fill", "exception"):
            return []
        return [it.stem for it in pool.values() if it.status == "approved" and it.task_id == s.task_id]

    ex = ThreadPoolExecutor(max_workers=concurrency)
    futs = {ex.submit(gen.run_slot, s, avoid_for(s)): s for s in targets}
    done = 0
    transient_streak = 0
    stopped = False
    try:
        for fut in as_completed(futs):
            s = futs[fut]
            if fut.cancelled():                     # 중단 후 시작 전에 취소된 슬롯: pending 그대로
                continue
            done += 1
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 — 슬롯은 pending으로 남김 (attempts 증가 없음)
                s.last_errors = [f"[error] {type(e).__name__}: {str(e)[:300]}"]
                s.updated_at = now_iso()
                save(manifest, pool)
                print(f"  [{done}/{len(targets)}] {s.quiz_id}: 오류 → pending 유지 ({type(e).__name__}: {str(e)[:120]})")
                transient_streak = transient_streak + 1 if isinstance(e, TransientError) else 0
                if transient_streak >= TRANSIENT_STOP and not stopped:
                    stopped = True
                    n_cancel = sum(f.cancel() for f in futs)
                    print(f"[run] 일시 오류로 연속 {transient_streak}개 슬롯 실패 — 새 슬롯 제출 중단 "
                          f"(취소 {n_cancel}개, 진행 중인 슬롯만 마무리)", flush=True)
                continue
            transient_streak = 0
            s.status, s.attempts, s.scenario_id = r.status, r.attempts, r.scenario_id
            s.repairs, s.repairs_ok = r.repairs, r.repairs_ok
            s.generator_model, s.reviewer_model = r.generator_model, r.reviewer_model
            s.detected_patterns, s.last_errors = r.detected_patterns, r.last_errors
            s.prompt_version, s.updated_at = pv, now_iso()
            if r.item is not None:
                pool[s.quiz_id] = r.item
            else:
                pool.pop(s.quiz_id, None)          # 코드 검증 단계 실패: 매니페스트에만 기록
            save(manifest, pool)
            pats = f" {r.detected_patterns}" if r.detected_patterns else ""
            rep = f", repair {r.repairs_ok}/{r.repairs}" if r.repairs else ""
            print(f"  [{done}/{len(targets)}] {s.quiz_id} SC-{r.scenario_id}: {r.status} (시도 {r.attempts}{rep}){pats}")
            if verbose:
                print_item(r, pool.get(s.quiz_id))
    except KeyboardInterrupt:
        save(manifest, pool)
        print("\n[run] 중단 — 완료된 슬롯은 저장됨, 진행 중이던 슬롯은 pending. --resume으로 이어서 실행", flush=True)
        os._exit(130)
    ex.shutdown(wait=True)
    if stopped:
        print("[run] 쓰로틀링으로 중단됨 — 할당량이 회복된 뒤(수 분 후) --resume으로 이어서 실행. "
              "필요하면 --concurrency 1", flush=True)
    return not stopped


def print_item(r, item: QuizItem | None) -> None:
    for line in r.log:
        print(f"      · {line}")
    if item is None:
        print(f"      (문항 없음) {r.last_errors[:2]}")
        return
    print(f"      focus {item.focus_bullet_ids} · SC-{item.scenario_id} · status {item.status}")
    print(f"      Q: {item.stem}")
    for o in item.options:
        mark = "*" if o.label == item.answer else " "
        print(f"      {mark}{o.label}) {o.text}")
        print(f"          - {o.rationale}")
    print(f"      excerpt: {item.source_excerpt}")
    if r.status != "approved":
        for e in r.last_errors:
            print(f"      ! {e}")
    print()


def apply_dedupe(manifest: QuizManifest, pool: dict[str, QuizItem]) -> int:
    order = {s.quiz_id: s.order for s in manifest.slots}
    emb = config.get_embeddings()
    dups = dedupe(list(pool.values()), order, emb.embed_documents)
    by_id = {s.quiz_id: s for s in manifest.slots}
    for dup_id, orig_id, sim in dups:
        tag = f"duplicate_of:{orig_id}"
        it = pool[dup_id]
        pool[dup_id] = it.model_copy(update={"status": "duplicate", "detected_patterns": it.detected_patterns + [tag]})
        if (s := by_id.get(dup_id)) is not None:
            s.status = "duplicate"
            s.detected_patterns = s.detected_patterns + [tag]
            s.updated_at = now_iso()
        print(f"[dedupe] {dup_id} ≈ {orig_id} (cos {sim:.3f}) → duplicate")
    if dups:
        save(manifest, pool)
    return len(dups)


# ---------------------------------------------------------------- 리포트

def print_plan(slots: list[ManifestSlot], corpus: Corpus) -> None:
    print("\n[slots] 도메인  기본  추가  합계(기대)  시나리오 분포")
    for d in range(1, 6):
        ds = [s for s in slots if s.domain == d]
        base = sum(1 for s in ds if s.slot_type == "base")
        extra = sum(1 for s in ds if s.slot_type == "extra")
        sc = Counter(s.scenario_id for s in ds)
        print(f"  D{d}      {base:>4}  {extra:>4}  {base + extra:>4}({EXPECTED_TOTAL[d]})   "
              + ", ".join(f"SC-{k}:{v}" for k, v in sorted(sc.items())))
    print(f"  합계    {sum(1 for s in slots if s.slot_type == 'base'):>4}  "
          f"{sum(1 for s in slots if s.slot_type == 'extra'):>4}  {len(slots):>4}")
    tc = Counter(s.target_answer for s in slots)
    print("[slots] 정답 위치 목표: " + ", ".join(f"{k}:{tc[k]}" for k in LABELS))
    print("[slots] 추가 슬롯 (겹친 키워드):")
    for s in (s for s in slots if s.slot_type == "extra"):
        k, sk = (corpus.bullet_by_id[b] for b in s.focus_bullet_ids)
        shared = sorted(keywords(k.text) & keywords(sk.text))
        warn = "  <-- 겹침 0" if not shared else ""
        print(f"  {s.quiz_id:<10} {k.bullet_id}+{sk.bullet_id.rsplit('-', 1)[1]:<4} {len(shared)}: {', '.join(shared)}{warn}")
    print("[fewshot] 시나리오별 SQ: " + ", ".join(f"SC-{k}:{v}" for k, v in sorted(corpus.sq_by_scenario.items())))


def print_summary(manifest: QuizManifest, pool: dict[str, QuizItem], corpus: Corpus, n_dups: int,
                  models: "ModelPool | None" = None) -> None:
    summ = summarize(manifest)
    approved = [i for i in pool.values() if i.status == "approved"]
    tried = sum(v for k, v in summ.items() if k != "pending")
    print("\n[summary] 상태: " + ", ".join(f"{k} {v}" for k, v in summ.items()))
    print("[summary] 도메인별 승인/목표: " + ", ".join(
        f"D{d} {sum(1 for i in approved if i.domain == d)}/{EXPECTED_TOTAL[d]}" for d in range(1, 6)))
    covered = {b for i in approved for b in i.focus_bullet_ids}
    print(f"[summary] 세부 항목 커버리지: {len(covered)}/{len(corpus.bullets)} "
          f"({len(covered) / len(corpus.bullets):.1%}, 목표 {COVERAGE_TARGET} 이상)")
    if tried:
        print(f"[summary] 승인율: {summ['approved']}/{tried} ({summ['approved'] / tried:.1%})")
    # 실패 유형: 코드 세부 패턴 그대로, repair 성공 표시는 따로, duplicate_of:Q-…는 duplicate로 묶음
    pats = Counter("duplicate" if p.startswith("duplicate_of:") else p
                   for s in manifest.slots for p in s.detected_patterns)
    if pats:
        print("[summary] 실패 유형: " + ", ".join(f"{k} {v}" for k, v in pats.most_common()))
    rep, rep_ok = sum(s.repairs for s in manifest.slots), sum(s.repairs_ok for s in manifest.slots)
    if rep:
        print(f"[summary] 길이 repair: {rep_ok}/{rep} 성공 ({rep_ok / rep:.0%})")
    if approved:
        ac = Counter(i.answer for i in approved)
        dist = ", ".join(f"{k} {ac[k]} ({ac[k] / len(approved):.0%})" for k in LABELS)
        print(f"[summary] 정답 위치: {dist}")
        if len(approved) >= 20 and any(not 0.20 <= ac[k] / len(approved) <= 0.30 for k in LABELS):
            print("[summary] 경고: 정답 위치 분포가 20~30% 범위를 벗어남")
        longest = sum(1 for i in approved if longest_is_correct(i))
        print(f"[summary] 정답이 가장 긴 보기: {longest}/{len(approved)} ({longest / len(approved):.0%}, "
              f"목표 {LONGEST_TARGET:.0%} 이하, 우연 수준 25%)")
        over5 = sum(1 for i in approved if correct_vs_longest(i) > REBALANCE_SELECT)
        print(f"[summary] 정답이 가장 긴 오답보다 5% 넘게 긴 보기: {over5}/{len(approved)} ({over5 / len(approved):.0%}, "
              "보조 지표)")
    print(f"[summary] 이번 실행 중복 판정: {n_dups}")
    print_model_summary(manifest, pool, models)
    if n_stale := stale_count(manifest):
        print(f"[summary] 경고: 현재 프롬프트({prompt_version()})와 다른 버전으로 처리된 슬롯 {n_stale}개 "
              "— 필요하면 --reset-stale")


def print_model_summary(manifest: QuizManifest, pool: dict[str, QuizItem], models: "ModelPool | None") -> None:
    """생성·검토 모델별 승인 수와 승인율, 이번 실행의 모델별 호출·쓰로틀·전환 (DESIGN §9-3)."""
    gen_c, gen_tot, rev_c = Counter(), Counter(), Counter()
    for s in manifest.slots:
        if s.status == "pending":
            continue
        # 모델 기록 도입 전 슬롯은 기본 모델(us Sonnet 4.5)로 처리됨
        g = s.generator_model or (pool[s.quiz_id].generator_model if s.quiz_id in pool else config.QUIZ_GEN_MODEL_ID)
        gen_tot[short_model(g)] += 1
        if s.status == "approved":
            gen_c[short_model(g)] += 1
            rev_c[short_model(s.reviewer_model) if s.reviewer_model else "us/sonnet-4-5(추정: reviewer_model 기록 도입 전, 당시 us만 사용)"] += 1
    if gen_tot:
        print("[summary] 생성 모델별 승인/처리: " + ", ".join(
            f"{m} {gen_c[m]}/{n} ({gen_c[m] / n:.0%})" for m, n in gen_tot.most_common()))
        print("[summary] 승인 문항 검토 모델: " + ", ".join(f"{m} {n}" for m, n in rev_c.most_common()))
    if models is not None:
        print(f"[summary] 이번 실행 모델 호출: {models.report()}")

def longest_is_correct(item: QuizItem) -> bool:
    """정답 보기가 가장 긴 보기인지 (동률이면 가장 긴 것으로 침)."""
    lens = {o.label: len(o.text) for o in item.options}
    return lens[item.answer] >= max(lens.values())


# ---------------------------------------------------------------- main

def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="슬롯 배정만 확인 (LLM·파일 쓰기 없음)")
    mode.add_argument("--pilot", type=int, nargs="?", const=10, metavar="N", help="파일럿 N슬롯 재생성 (기본 10)")
    mode.add_argument("--resume", action="store_true", help="pending 슬롯을 순서대로 생성")
    mode.add_argument("--fill-gaps", action="store_true",
                      help="승인 문제가 없는 세부 항목만 보충 (기본/추가 슬롯이 모두 끝난 뒤에만)")
    mode.add_argument("--rebalance-length", action="store_true",
                      help="정답이 가장 긴 오답보다 5%% 넘게 긴 승인 문항의 보기를 재조정 (목표 0.95, 재검토 필수)")
    mode.add_argument("--exception-slot", metavar="BULLET_ID:SC",
                      help="미커버 세부 항목에 시나리오 고정 예외 슬롯 1개 생성 (예: TS-5.2-K2:1)")
    mode.add_argument("--reset-stale", action="store_true",
                      help="현재 prompt_version과 다른 버전으로 처리된 슬롯을 pending으로 되돌림 (fill 슬롯은 삭제)")
    ap.add_argument("--limit", type=int, help="--resume·--rebalance-length에서 처리할 최대 수")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"동시 실행 슬롯 수 (기본 {DEFAULT_CONCURRENCY})")
    ap.add_argument("--no-dedupe", action="store_true", help="실행 후 중복 제거 생략")
    args = ap.parse_args()

    try:
        corpus = load_corpus()
        slots = build_slots(corpus)
    except CorpusError as e:
        print(f"[build] {e}", file=sys.stderr)
        return 1
    errs = plan_errors(slots, corpus)
    if errs:
        print("[build] 슬롯 배정 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print_plan(slots, corpus)
        gen = QuizGenerator(corpus)              # 클라이언트만 만들고 호출하지 않음 (체인 4개)
        for s in (slots[0], next(s for s in slots if s.slot_type == "extra")):
            msgs = gen.generation_messages(s, s.scenario_id, [], [])
            print(f"[dry-run] {s.quiz_id} 생성 프롬프트 {len(msgs[0].content) + len(msgs[1].content)}자, "
                  f"few-shot {pick_fewshot(s.quiz_id, s.domain, s.scenario_id, corpus)}")
        print(f"[dry-run] prompt_version {prompt_version()} — 파일 변경 없음")
        return 0

    try:
        manifest, pool = load_state(corpus, allow_processed_change=args.reset_stale)
    except CorpusError as e:
        print(f"[build] {e}", file=sys.stderr)
        return 1

    if args.reset_stale:
        reset, removed = reset_stale(manifest, pool, slots, {s.quiz_id for s in select_pilot(slots, 10)})
        save(manifest, pool)
        print(f"[reset-stale] prompt_version {prompt_version()} 기준: 리셋 {len(reset)}개, 삭제 {len(removed)}개")
        if reset:
            print("  리셋: " + ", ".join(reset))
        if removed:
            print("  삭제: " + ", ".join(removed))
        print(f"[reset-stale] 매니페스트 {summarize(manifest)}, pool {len(pool)}개")
        return 0

    if args.fill_gaps:
        left = [s for s in manifest.slots if s.status == "pending" and s.slot_type in ("base", "extra")]
        if left:
            print(f"[fill-gaps] 거부: 기본/추가 슬롯 {len(left)}개가 아직 pending — 먼저 --resume으로 끝낼 것",
                  file=sys.stderr)
            return 1

    if n_stale := stale_count(manifest):
        print(f"[build] 경고: 현재 프롬프트({prompt_version()})와 다른 버전으로 처리된 슬롯 {n_stale}개 "
              "— 새 프롬프트로 다시 만들려면 --reset-stale")
    maint = args.rebalance_length or bool(args.exception_slot)
    # 생성·검토·repair·시나리오 순위가 함께 쓰는 Sonnet 체인 (사후 작업은 us Sonnet 4.5 제외)
    models = ModelPool(MAINT_MODEL_CHAIN) if maint else ModelPool()
    print("[build] 모델 체인: " + " → ".join(short_model(m) for m in models.model_ids))
    gen = QuizGenerator(corpus, models)

    if args.pilot is not None:
        by_id = {s.quiz_id: s for s in manifest.slots}
        # 새로 만든 슬롯(원래 시나리오)으로 고른다 — 재시도로 바뀐 scenario_id 때문에 선택이 흔들리지 않게
        targets = [by_id[s.quiz_id] for s in select_pilot(slots, args.pilot)]
        fresh = {s.quiz_id: s for s in slots}
        for s in targets:                          # 프롬프트 조정 반복용: 매번 새로 생성
            s.pilot, s.status, s.attempts, s.repairs, s.repairs_ok = True, "pending", 0, 0, 0
            s.scenario_id = fresh[s.quiz_id].scenario_id
            s.detected_patterns, s.last_errors = [], []
            pool.pop(s.quiz_id, None)
        save(manifest, pool)
        ok = run_slots(gen, manifest, pool, targets, args.concurrency, verbose=True)
    elif args.rebalance_length:
        targets = rebalance_targets(manifest, pool)
        if args.limit is not None:
            targets = targets[:args.limit]
        print(f"[rebalance] 대상 {len(targets)}개 (정답 > 가장 긴 오답 × {REBALANCE_SELECT}, 처리 이력 없는 승인 문항)")
        ok = run_rebalance(gen, manifest, pool, targets, args.concurrency)
    elif args.exception_slot:
        try:
            slot = add_exception_slot(manifest, pool, corpus, args.exception_slot)
        except CorpusError as e:
            print(f"[exception] 거부: {e}", file=sys.stderr)
            return 1
        print(f"[exception] {slot.quiz_id}: {slot.focus_bullet_ids[0]} SC-{slot.scenario_id} 고정 (항목당 2슬롯 규칙 예외)")
        save(manifest, pool)
        ok = run_slots(gen, manifest, pool, [slot], args.concurrency, verbose=True)
    elif args.resume:
        rank = {"base": 0, "extra": 1, "fill": 2, "exception": 3}
        targets = sorted((s for s in manifest.slots if s.status == "pending"),
                         key=lambda s: (rank[s.slot_type], s.order))
        if args.limit is not None:
            targets = targets[:args.limit]
        save(manifest, pool)
        ok = run_slots(gen, manifest, pool, targets, args.concurrency, verbose=False)
    else:
        new = add_fill_slots(manifest, pool, corpus, ranker=ScenarioRanker(corpus, models))
        targets = [s for s in manifest.slots if s.slot_type == "fill" and s.status == "pending"]
        print(f"[fill-gaps] 새 보충 슬롯 {len(new)}개, 처리 대상 {len(targets)}개")
        save(manifest, pool)
        ok = run_slots(gen, manifest, pool, targets, args.concurrency, verbose=False)

    # 쓰로틀링으로 멈췄으면 임베딩 호출(중복 제거)도 건너뛴다 — 다음 실행 끝에 수행됨
    # 재조정은 지문을 바꾸지 않으므로 중복 제거를 다시 할 필요가 없다
    n_dups = 0 if (args.no_dedupe or not ok or args.rebalance_length) else apply_dedupe(manifest, pool)
    print_summary(manifest, pool, corpus, n_dups, models)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
