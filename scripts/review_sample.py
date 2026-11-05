"""사람 표본 검토 (DESIGN §5-2, TS 5.5 층화 표본): xlsx 양식 생성과 검토 결과 반영

실행:
    python scripts/review_sample.py             # 표본 추출 → evaluation/review_sample.xlsx + human_review_results.csv(빈 판정)
    python scripts/review_sample.py --force     # 검토 입력이 있어도 덮어씀
    python scripts/review_sample.py --apply     # xlsx 검토 결과 → 문제 풀·매니페스트·결과 CSV·리포트
    python scripts/review_sample.py --apply --dry-run [--xlsx 경로]   # 검증·집계만 (파일 쓰기 없음)

층 = 도메인 × {재시도 없음(review_retries=0), 재시도 있음}. 표본 수는 재시도 없음 층 10%, 재시도 층 20% (올림).
층마다 repair 문항(생성 단계 길이 repair 또는 승인 후 정답 길이 재조정)을 최소 1개 먼저 넣고, 나머지는 층 안에서
고정 시드로 무작위 추출한다 (재현 가능). LLM 호출 없음.

xlsx 시트1 "검토"는 정답 없이 먼저 풀어 보는 용도(내 답·판정·사유 드롭다운), 시트2 "정답·근거"는 정답·해설·
초점 세부 항목 원문·source_excerpt. 세부 항목 원문(가이드 문장)이 들어가므로 xlsx는 커밋하지 않는다(DESIGN §2-4).
검토 결과는 가이드 원문 없이 evaluation/human_review_results.csv에 남기고, 리포트는 이 CSV만 읽는다.

판정: OK / 경미 / 오류. 성공 기준의 "오류"는 판정=오류 전부(대표 사유: 정답복수·근거불일치), 경미는 별도 집계.
오류 → 문항·슬롯 status needs_fix (출제 대상에서 제외). 같은 사유가 한 도메인에서 2건 이상(오류+경미)이면 경고.
"""
import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from src import config  # noqa: E402
from src.quiz_gen import load_corpus, now_iso, seeded_rng  # noqa: E402
from src.schemas import LABELS, QuizItem, QuizManifest  # noqa: E402
from build_quiz_pool import MANIFEST_PATH, POOL_PATH, save  # noqa: E402

XLSX_PATH = config.ROOT / "evaluation" / "review_sample.xlsx"
RESULTS_PATH = config.ROOT / "evaluation" / "human_review_results.csv"
SEED = "ccaf-review-v1"
RATE_FIRST, RATE_RETRY = 0.10, 0.20

VERDICTS = ("OK", "경미", "오류")
ERROR_VERDICT = "오류"                                   # 성공 기준에 세는 판정 (목표 0건, 최대 1건)
REASONS = {                                             # 사유 → 검토 패턴 slug (프롬프트 피드백 재사용)
    "정답복수": "multiple_correct",
    "근거불일치": "answer_not_supported",
    "오답뻔함": "distractor_obviously_wrong",
    "초점이탈": "off_focus_bullet",
    "시나리오부적합": "scenario_mismatch",
    "기타": "other",
}
SAME_REASON_ALERT = 2                                   # 같은 도메인·같은 사유가 이 수 이상이면 경고 (DESIGN §5-2)

SHEET_REVIEW, SHEET_KEY = "검토", "정답·근거"
REVIEW_HEAD = (["quiz_id", "층", "시나리오", "지문"] + [f"보기 {l}" for l in LABELS]
               + ["내 답", "판정", "사유", "메모"])
INPUT_HEAD = ("내 답", "판정", "사유", "메모")
KEY_HEAD = ["quiz_id", "정답"] + [f"해설 {l}" for l in LABELS] + ["초점 세부 항목 원문", "source_excerpt"]

RESULT_FIELDS = ["quiz_id", "stratum", "domain", "task_id", "focus_bullet_ids", "scenario_id", "repaired",
                 "rebalanced", "answer", "my_answer", "verdict", "reason", "reason_slug", "memo", "applied_at"]
RESULT_INPUTS = ("my_answer", "verdict", "reason", "memo")


# ---------------------------------------------------------------- 표본 추출

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


def stratum_label(key: str, item: QuizItem) -> str:
    d, kind = key.split("-")
    return f"{d} · {'재시도' if kind == 'retry' else '재시도 없음'}" + (" · repair" if is_repaired(item) else "")


def load_pool() -> dict[str, QuizItem]:
    return {d["quiz_id"]: QuizItem.model_validate(d) for d in json.loads(POOL_PATH.read_text(encoding="utf-8"))}


def read_results() -> list[dict]:
    if not RESULTS_PATH.is_file():
        return []
    return list(csv.DictReader(RESULTS_PATH.open(encoding="utf-8-sig")))


def write_results(rows: list[dict]) -> None:
    tmp = RESULTS_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(RESULTS_PATH)


# ---------------------------------------------------------------- xlsx 생성

def xlsx_has_input(path: Path) -> bool:
    if not path.is_file():
        return False
    ws = load_workbook(path, read_only=True)[SHEET_REVIEW]
    rows = ws.iter_rows(values_only=True)
    head = list(next(rows))
    idx = [head.index(c) for c in INPUT_HEAD if c in head]
    return any(r[i] not in (None, "") for r in rows for i in idx)


def write_xlsx(path: Path, sample: list[tuple[str, QuizItem]], corpus) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_REVIEW
    key_ws = wb.create_sheet(SHEET_KEY)
    head_font, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    input_fill = PatternFill("solid", fgColor="FFF2CC")

    ws.append(REVIEW_HEAD)
    key_ws.append(KEY_HEAD)
    for key, i in sample:
        opts = {o.label: o for o in i.options}
        sc = corpus.chunks[f"SC-{i.scenario_id}"].title.split(": ", 1)[-1]
        ws.append([i.quiz_id, stratum_label(key, i), f"SC-{i.scenario_id} {sc}", i.stem]
                  + [opts[l].text for l in LABELS] + [None] * len(INPUT_HEAD))
        bullets = "\n".join(f"{b}: {corpus.bullet_by_id[b].text}" for b in i.focus_bullet_ids)
        key_ws.append([i.quiz_id, i.answer] + [opts[l].rationale for l in LABELS] + [bullets, i.source_excerpt])

    n = len(sample) + 1
    col = {h: chr(ord("A") + k) for k, h in enumerate(REVIEW_HEAD)}
    for h, values in (("내 답", LABELS), ("판정", VERDICTS), ("사유", tuple(REASONS))):
        dv = DataValidation(type="list", formula1='"' + ",".join(values) + '"', allow_blank=True,
                            showErrorMessage=True, errorTitle=h, error=f"{' / '.join(values)} 중에서 고르세요")
        ws.add_data_validation(dv)
        dv.add(f"{col[h]}2:{col[h]}{n}")

    widths = {"quiz_id": 14, "층": 22, "시나리오": 26, "지문": 70, "내 답": 7, "판정": 8, "사유": 14, "메모": 40,
              **{f"보기 {l}": 40 for l in LABELS}}
    for h, c in col.items():
        ws.column_dimensions[c].width = widths[h]
    key_widths = [14, 6, 45, 45, 45, 45, 60, 60]
    for k, w in enumerate(key_widths):
        key_ws.column_dimensions[chr(ord("A") + k)].width = w
    for sheet in (ws, key_ws):
        sheet.freeze_panes = "B2"
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = wrap
        for cell in sheet[1]:
            cell.font = head_font
    for h in INPUT_HEAD:
        for r in range(1, n + 1):
            ws[f"{col[h]}{r}"].fill = input_fill
    tmp = path.with_name(path.stem + ".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)


def make_sample(force: bool) -> int:
    if not force:
        if xlsx_has_input(XLSX_PATH):
            print(f"[sample] {XLSX_PATH.name}에 검토 입력이 있음 — 보호를 위해 중단 (--force로 덮어쓰기)", file=sys.stderr)
            return 1
        if any(r.get(c, "").strip() for r in read_results() for c in RESULT_INPUTS):
            print(f"[sample] {RESULTS_PATH.name}에 반영된 검토 결과가 있음 — 보호를 위해 중단 (--force로 덮어쓰기)",
                  file=sys.stderr)
            return 1

    corpus = load_corpus()
    approved = [i for i in load_pool().values() if i.status == "approved"]
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

    try:
        write_xlsx(XLSX_PATH, sample, corpus)
    except PermissionError:
        XLSX_PATH.with_name(XLSX_PATH.stem + ".tmp.xlsx").unlink(missing_ok=True)
        print(f"[sample] {XLSX_PATH.name}에 쓸 수 없음 — Excel에서 열려 있으면 닫고 다시 실행 (결과 CSV도 바꾸지 않음)",
              file=sys.stderr)
        return 1
    write_results([{
        "quiz_id": i.quiz_id, "stratum": key, "domain": i.domain, "task_id": i.task_id,
        "focus_bullet_ids": " ".join(i.focus_bullet_ids), "scenario_id": f"SC-{i.scenario_id}",
        "repaired": "Y" if is_repaired(i) else "",
        "rebalanced": "Y" if "length_cue:rebalanced" in i.detected_patterns else "",
        "answer": i.answer, **{c: "" for c in RESULT_FIELDS[9:]},
    } for key, i in sample])
    rel = lambda p: p.relative_to(config.ROOT).as_posix()  # noqa: E731
    print(f"[sample] {len(sample)}문항 → {rel(XLSX_PATH)} (시트 '{SHEET_REVIEW}'·'{SHEET_KEY}', 커밋 안 함) "
          f"+ {rel(RESULTS_PATH)} (빈 판정)")
    print("[sample] 검토: 시트1에서 먼저 내 답 → 시트2로 정답·근거 확인 → 판정·사유·메모 → "
          "python scripts/review_sample.py --apply")
    return 0


# ---------------------------------------------------------------- 결과 반영

def cell(v) -> str:
    return "" if v is None else str(v).strip()


def read_reviews(path: Path) -> tuple[list[dict], list[str]]:
    """시트1 입력을 읽어 검증. 반환: (행 목록, 오류 메시지 목록)."""
    ws = load_workbook(path, read_only=True, data_only=True)[SHEET_REVIEW]
    rows = ws.iter_rows(values_only=True)
    head = [cell(h) for h in next(rows)]
    missing = [h for h in ["quiz_id", *INPUT_HEAD] if h not in head]
    if missing:
        return [], [f"시트 '{SHEET_REVIEW}'에 열 없음: {missing}"]
    out, errs = [], []
    for n, r in enumerate(rows, start=2):
        v = {h: cell(r[head.index(h)]) for h in ["quiz_id", *INPUT_HEAD]}
        if not v["quiz_id"]:
            continue
        verdict, reason, mine, memo = v["판정"], v["사유"], v["내 답"].upper(), v["메모"]
        where = f"{n}행 {v['quiz_id']}"
        if mine and mine not in LABELS:
            errs.append(f"{where}: 내 답 {mine!r} — A~D만 허용")
        if verdict and verdict not in VERDICTS:
            errs.append(f"{where}: 판정 {verdict!r} — {'/'.join(VERDICTS)}만 허용")
        if reason and reason not in REASONS:
            errs.append(f"{where}: 사유 {reason!r} — {'/'.join(REASONS)}만 허용")
        if verdict in ("경미", "오류") and not reason:
            errs.append(f"{where}: 판정 {verdict}에는 사유가 필요")
        if reason == "기타" and not memo:
            errs.append(f"{where}: 사유 기타에는 메모가 필요")
        if not verdict and (reason or memo):
            errs.append(f"{where}: 판정 없이 사유·메모만 있음")
        out.append({"row": n, "quiz_id": v["quiz_id"], "my_answer": mine, "verdict": verdict,
                    "reason": reason, "memo": memo})
    return out, errs


def same_reason_alerts(rows: list[dict]) -> list[tuple[str, str, list[str]]]:
    """판정 오류·경미 행에서 (도메인, 사유) 별로 SAME_REASON_ALERT건 이상인 것. pool_report도 같이 쓴다."""
    groups: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        if r.get("verdict") in ("경미", "오류") and r.get("reason"):
            groups.setdefault((f"D{r['domain']}", r["reason"]), []).append(f"{r['quiz_id']}({r['verdict']})")
    return [(d, reason, ids) for (d, reason), ids in sorted(groups.items()) if len(ids) >= SAME_REASON_ALERT]


def apply_reviews(xlsx: Path, dry_run: bool) -> int:
    if not xlsx.is_file():
        print(f"[apply] {xlsx} 없음 — 먼저 python scripts/review_sample.py", file=sys.stderr)
        return 1
    results = read_results()
    if not results:
        print(f"[apply] {RESULTS_PATH.name} 없음 — 먼저 python scripts/review_sample.py", file=sys.stderr)
        return 1
    by_id = {r["quiz_id"]: r for r in results}
    reviews, errs = read_reviews(xlsx)
    pool = load_pool()
    manifest = QuizManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
    slots = {s.quiz_id: s for s in manifest.slots}
    for rv in reviews:
        if rv["quiz_id"] not in pool or rv["quiz_id"] not in slots:
            errs.append(f"{rv['row']}행 {rv['quiz_id']}: 문제 풀·매니페스트에 없는 quiz_id")
        elif rv["quiz_id"] not in by_id:
            errs.append(f"{rv['row']}행 {rv['quiz_id']}: 결과 CSV 표본에 없는 quiz_id (xlsx와 CSV가 다른 표본)")
    if errs:
        print(f"[apply] 입력 오류 {len(errs)}건 — 아무것도 바꾸지 않고 중단", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    stamp = now_iso()
    changed: list[str] = []
    for rv in reviews:
        r = by_id[rv["quiz_id"]]
        r.update(my_answer=rv["my_answer"], verdict=rv["verdict"], reason=rv["reason"],
                 reason_slug=REASONS.get(rv["reason"], ""), memo=rv["memo"],
                 applied_at=stamp if rv["verdict"] else "")
        if not rv["verdict"]:
            continue
        item, slot = pool[rv["quiz_id"]], slots[rv["quiz_id"]]
        item.human_checked = True
        new = "needs_fix" if rv["verdict"] == ERROR_VERDICT else ("approved" if item.status == "needs_fix" else item.status)
        if new != item.status:
            changed.append(f"{item.quiz_id} {item.status} → {new}")
            item.status = slot.status = new

    done = [r for r in results if r["verdict"]]
    vc = Counter(r["verdict"] for r in done)
    n_err = vc[ERROR_VERDICT]
    print(f"[apply] 검토 {len(done)}/{len(results)} · " + " · ".join(f"{v} {vc[v]}" for v in VERDICTS))
    print(f"[apply] 성공 기준 오류(판정=오류) {n_err}건 (목표 0, 최대 1) → {('충족' if n_err <= 1 else '미충족') if done else '검토 전'}"
          f" · 경미 {vc['경미']}건 (별도 집계)")
    rc = Counter((r["verdict"], r["reason"]) for r in done if r["reason"])
    if rc:
        print("[apply] 사유 분포: " + " · ".join(f"{reason}({v}) {k}" for (v, reason), k in rc.most_common()))
    answered = [r for r in done if r["my_answer"]]
    diff = [r for r in answered if r["my_answer"] != r["answer"]]
    print(f"[apply] 내 답 ≠ 정답: {len(diff)}/{len(answered)}건" if answered
          else "[apply] 내 답 ≠ 정답: 비교 없음 (내 답 기입 0건)")
    for r in diff:
        print(f"  - {r['quiz_id']} 내 답 {r['my_answer']} · 정답 {r['answer']} · 판정 {r['verdict']}")
    for d, reason, ids in same_reason_alerts(results):
        print(f"[apply] 경고: {d}에서 사유 '{reason}' {len(ids)}건 ({', '.join(ids)}) "
              "— DESIGN §5-2: 해당 도메인 프롬프트 수정 후 재생성 검토")
    for c in changed:
        print(f"[apply] 상태 변경: {c}")

    if dry_run:
        print("[apply] --dry-run: 파일을 쓰지 않음")
        return 0
    save(manifest, pool)
    write_results(results)
    print(f"[apply] 반영: {POOL_PATH.name}·{MANIFEST_PATH.name}(human_checked {len(done)}, 상태 변경 {len(changed)}) "
          f"· {RESULTS_PATH.name}")
    from pool_report import refresh
    return refresh()


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="검토 입력이 있는 기존 xlsx·결과 CSV도 덮어씀")
    ap.add_argument("--apply", action="store_true", help="xlsx 검토 결과를 풀·매니페스트·결과 CSV·리포트에 반영")
    ap.add_argument("--dry-run", action="store_true", help="--apply에서 검증·집계만 하고 파일을 쓰지 않음")
    ap.add_argument("--xlsx", type=Path, default=XLSX_PATH, help="--apply에서 읽을 xlsx (기본 evaluation/review_sample.xlsx)")
    args = ap.parse_args()
    if args.apply:
        return apply_reviews(args.xlsx, args.dry_run)
    if args.dry_run:
        ap.error("--dry-run은 --apply와 함께 사용")
    return make_sample(args.force)


if __name__ == "__main__":
    sys.exit(main())
