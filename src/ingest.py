"""가이드 PDF → 청크 62개 + 세부 항목 240개 → Chroma (DESIGN §2).

실행:
    python -m src.ingest            # 파싱 → 검증 → jsonl 저장 → Chroma 적재
    python -m src.ingest --dry-run  # 파싱 → 검증만 (Bedrock 호출·파일 쓰기 없음)

검증에 실패하면 jsonl도 Chroma도 건드리지 않고 종료 코드 1로 멈춘다.
"""
import argparse
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from pypdf import PdfReader

from src import config
from src.schemas import GuideBullet, GuideChunk

# ---------------------------------------------------------------- 상수

# 페이지 머리말/꼬리말 (pypdf는 한 줄로, 다른 추출기는 두 줄로 내놓는다)
HEADER_LINES = {"Claude Certification Program Exam guide", "Claude Certification Program", "Exam guide"}

BULLET_RE = re.compile(r"[•●·▪]")

# 구조 분할 앵커 (줄 단위, 머리말 제거 후)
ANCHORS = [
    ("ov_intro", re.compile(r"^Introduction$")),
    ("ov_details", re.compile(r"^Exam Details at a Glance$")),
    ("ov_candidate", re.compile(r"^Target Candidate Description$")),
    ("ov_content", re.compile(r"^Exam Content$")),
    ("scenarios_intro", re.compile(r"^Exam Scenarios$")),
    ("scenario", re.compile(r"^Scenario (\d+): (.+)$")),
    ("domain", re.compile(r"^Domain (\d): (.+)$")),
    ("ts", re.compile(r"^Task Statement (\d)\.(\d+): ")),
    ("sq_intro", re.compile(r"^Sample Questions$")),
    ("sq_scenario", re.compile(r"^Scenario: (.+)$")),
    ("sq", re.compile(r"^Question (\d+): ")),
    ("ex_intro", re.compile(r"^Preparation Exercises$")),
    ("ex", re.compile(r"^Exercise (\d+): (.+)$")),
    ("appendix", re.compile(r"^Appendix$")),
    ("scope_tech", re.compile(r"^Technologies and Concepts$")),
    ("scope_in", re.compile(r"^In-Scope Topics$")),
    ("scope_out", re.compile(r"^Out-of-Scope Topics$")),
    ("prep", re.compile(r"^Exam Preparation Recommendations$")),
    ("version", re.compile(r"^Version (\d+\.\d+)")),
]

# 줄을 이어 붙이지 않고 새 줄로 시작하는 블록 (불릿, 선택지, 번호 단계, 라벨)
BLOCK_START_RE = re.compile(
    r"^(•|[A-D]\) |\d+\. |Correct Answer:|Objective:|Steps:|Domains reinforced:|Primary domains:"
    r"|Knowledge of:|Skills in:|Scenario: |Question \d+:)"
)
WRAP_MIN_LEN = 60  # 이보다 짧은 줄은 줄바꿈이 아니라 의도된 줄 끝(표·제목)으로 본다

# DESIGN §2-1, §2-2 기대값
EXPECTED_DOC_TYPES = {
    "overview": 2, "domain": 5, "scenario": 6, "task_statement": 30,
    "sample_question": 12, "exercise": 4, "scope": 3,
}
EXPECTED_BULLETS = {1: (24, 24), 2: (20, 23), 3: (23, 26), 4: (22, 25), 5: (24, 29)}
TS_PER_DOMAIN = {1: 7, 2: 5, 3: 6, 4: 6, 5: 6}
BULLETS_PER_TS = (6, 10)
# 라벨은 콜론까지 포함해서 본다 ("Skills in .claude/skills/"는 실제 본문)
FORBIDDEN_IN_BULLET = ("Claude Certification Program", "Exam guide", "Knowledge of:",
                       "Skills in:", "Task Statement", "•")


class IngestError(Exception):
    pass


@dataclass
class Section:
    kind: str
    match: re.Match
    page: int
    lines: list[str] = field(default_factory=list)  # 앵커 줄 포함


# ---------------------------------------------------------------- 텍스트 유틸

def _append(out: str, line: str) -> str:
    """줄바꿈으로 끊긴 단어 처리: 'multi-' + 'agent' → 'multi-agent'."""
    if not out:
        return line
    if out.endswith("-") and line[:1].islower():
        return out + line
    return out + " " + line


def join_all(lines: list[str]) -> str:
    """모든 줄을 한 문단으로 합친다 (불릿 하나, 제목 등)."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if ln:
            out = _append(out, ln)
    return re.sub(r"\s+", " ", out).strip()


def reflow(lines: list[str]) -> str:
    """PDF 줄바꿈을 정리한다. 긴 줄 다음 줄은 이어 붙이고, 블록 시작·짧은 줄 뒤는 줄을 바꾼다."""
    paras: list[str] = []
    prev_len = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if paras and prev_len >= WRAP_MIN_LEN and not BLOCK_START_RE.match(ln):
            paras[-1] = _append(paras[-1], ln)
        else:
            paras.append(ln)
        prev_len = len(ln)
    return "\n".join(paras)


def split_bullets(lines: list[str]) -> list[str]:
    """불릿 기호로 나눈다 (줄 단위가 아님).

    머리말 줄은 이미 제거됐으므로 페이지 경계에서 끊긴 불릿도
    다음 불릿 기호가 나올 때까지 한 불릿으로 이어진다.
    """
    text = "\n".join(lines)
    parts = BULLET_RE.split(text)
    if parts and parts[0].strip():
        raise IngestError(f"불릿 기호 앞에 텍스트가 있음: {parts[0].strip()[:60]!r}")
    return [join_all(p.split("\n")) for p in parts[1:] if p.strip()]


# ---------------------------------------------------------------- 1. 추출

def extract_lines(pdf_path) -> list[tuple[int, str]]:
    """페이지별 텍스트 → (page, line) 목록. 머리말·빈 줄 제거, NFKC로 합자(ﬁ, ﬂ) 정규화."""
    if not pdf_path.is_file():
        raise IngestError(f"가이드 PDF가 없음: {pdf_path}")
    out = []
    for page_no, page in enumerate(PdfReader(pdf_path).pages, start=1):
        text = unicodedata.normalize("NFKC", page.extract_text() or "")
        for ln in text.splitlines():
            ln = ln.strip()
            if ln and ln not in HEADER_LINES:
                out.append((page_no, ln))
    return out


# ---------------------------------------------------------------- 2. 구조 분할

def split_sections(lines: list[tuple[int, str]]) -> list[Section]:
    sections: list[Section] = []
    for page, ln in lines:
        for kind, pat in ANCHORS:
            m = pat.match(ln)
            if m:
                sections.append(Section(kind, m, page, [ln]))
                break
        else:
            if sections:              # 첫 앵커 이전(표지 제목)은 버림
                sections[-1].lines.append(ln)
    return sections


def parse_ts(sec: Section, domain: int) -> tuple[GuideChunk, list[GuideBullet], str]:
    major, minor = sec.match.group(1), sec.match.group(2)
    task_id = f"TS-{major}.{minor}"
    try:
        k_idx = sec.lines.index("Knowledge of:")
        s_idx = sec.lines.index("Skills in:")
    except ValueError:
        # 'Knowledge of: • ...' 처럼 같은 줄에 불릿이 붙은 추출기 대응
        k_idx = next((i for i, l in enumerate(sec.lines) if l.startswith("Knowledge of:")), -1)
        s_idx = next((i for i, l in enumerate(sec.lines) if l.startswith("Skills in:")), -1)
        if k_idx < 0 or s_idx < 0:
            raise IngestError(f"{task_id}: 'Knowledge of:' 또는 'Skills in:' 없음")
    if not 0 < k_idx < s_idx:
        raise IngestError(f"{task_id}: Knowledge/Skills 순서 이상 (k={k_idx}, s={s_idx})")

    title = join_all(sec.lines[:k_idx]).split(": ", 1)[1]
    k_lines = [sec.lines[k_idx].removeprefix("Knowledge of:")] + sec.lines[k_idx + 1:s_idx]
    s_lines = [sec.lines[s_idx].removeprefix("Skills in:")] + sec.lines[s_idx + 1:]
    knowledge, skills = split_bullets(k_lines), split_bullets(s_lines)

    bullets = [
        GuideBullet(bullet_id=f"{task_id}-K{i}", task_id=task_id, domain=domain, kind="knowledge", text=t)
        for i, t in enumerate(knowledge, 1)
    ] + [
        GuideBullet(bullet_id=f"{task_id}-S{i}", task_id=task_id, domain=domain, kind="skill", text=t)
        for i, t in enumerate(skills, 1)
    ]
    text = "\n".join(
        [f"Task Statement {major}.{minor}: {title}", "Knowledge of:"]
        + [f"- {t}" for t in knowledge] + ["Skills in:"] + [f"- {t}" for t in skills]
    )
    chunk = GuideChunk(doc_id=task_id, doc_type="task_statement", domain=domain,
                       title=title, page=sec.page, text=text)
    return chunk, bullets, title


def build_corpus(lines: list[tuple[int, str]]) -> tuple[list[GuideChunk], list[GuideBullet], dict]:
    sections = split_sections(lines)
    full_text = "\n".join(l for _, l in lines)
    info: dict = {"dropped": [], "version": None}

    weights = {
        int(m.group(1)): (m.group(2).strip(), int(m.group(3)))
        for m in re.finditer(r"Domain (\d): (.+?) \((\d+)% of scored content\)", full_text)
    }

    chunks: list[GuideChunk] = []
    bullets: list[GuideBullet] = []
    ov_details: list[Section] = []
    domain_pages: dict[int, int] = {}
    ts_titles: dict[int, list[tuple[str, str]]] = {}
    cur_domain: int | None = None
    sq_label: str | None = None

    def add(doc_id, doc_type, title, sec, text, domain=None):
        chunks.append(GuideChunk(doc_id=doc_id, doc_type=doc_type, domain=domain,
                                 title=title, page=sec.page, text=text))

    for sec in sections:
        m = sec.match
        if sec.kind in ("ov_intro", "ov_details", "ov_content", "scenarios_intro"):
            ov_details.append(sec)
        elif sec.kind == "ov_candidate":
            add("OV-candidate", "overview", "Target Candidate Description", sec, reflow(sec.lines))
        elif sec.kind == "scenario":
            n = int(m.group(1))
            add(f"SC-{n}", "scenario", f"Scenario {n}: {m.group(2)}", sec, reflow(sec.lines))
        elif sec.kind == "domain":
            cur_domain = int(m.group(1))
            domain_pages[cur_domain] = sec.page
            if len(sec.lines) > 1:
                raise IngestError(f"Domain {cur_domain} 헤더 뒤에 TS가 아닌 텍스트: {sec.lines[1][:60]!r}")
        elif sec.kind == "ts":
            if cur_domain is None or int(m.group(1)) != cur_domain:
                raise IngestError(f"TS {m.group(1)}.{m.group(2)}가 Domain {cur_domain} 아래에 있음")
            chunk, bs, title = parse_ts(sec, cur_domain)
            chunks.append(chunk)
            bullets.extend(bs)
            ts_titles.setdefault(cur_domain, []).append((chunk.doc_id, title))
        elif sec.kind == "sq_scenario":
            sq_label = m.group(1)
        elif sec.kind == "sq":
            n = int(m.group(1))
            body = reflow(sec.lines)
            add(f"SQ-{n:02d}", "sample_question", f"Sample Question {n} ({sq_label})", sec,
                f"Scenario: {sq_label}\n{body}")
        elif sec.kind == "ex":
            n = int(m.group(1))
            add(f"EX-{n}", "exercise", f"Exercise {n}: {m.group(2)}", sec, reflow(sec.lines))
        elif sec.kind in ("scope_tech", "scope_in", "scope_out"):
            doc_id = {"scope_tech": "SCOPE-tech", "scope_in": "SCOPE-in", "scope_out": "SCOPE-out"}[sec.kind]
            add(doc_id, "scope", sec.lines[0], sec, reflow(sec.lines))
        elif sec.kind == "version":
            info["version"] = m.group(1)
        else:  # sq_intro, ex_intro, appendix, prep: 62개 분류 밖 → 버림
            info["dropped"].append(f"{sec.lines[0]} (p.{sec.page}, {len(sec.lines)}줄)")

    if ov_details:
        add("OV-details", "overview", "Exam Details and Content Outline", ov_details[0],
            # 'Exam Details at a Glance'는 표(행마다 한 줄)라서 줄을 합치지 않는다
            "\n".join("\n".join(s.lines) if s.kind == "ov_details" else reflow(s.lines)
                      for s in ov_details))

    for d in sorted(ts_titles):
        name, pct = weights.get(d, ("?", 0))
        text = "\n".join(
            [f"Domain {d}: {name} ({pct}% of scored content)",
             f"Task statements ({len(ts_titles[d])}):"]
            + [f"- {tid}: {title}" for tid, title in ts_titles[d]]
        )
        chunks.append(GuideChunk(doc_id=f"D{d}", doc_type="domain", domain=d,
                                 title=f"Domain {d}: {name}", page=domain_pages.get(d), text=text))
    info["weights"] = weights
    return chunks, bullets, info


# ---------------------------------------------------------------- 3. 검증

def validate(chunks: list[GuideChunk], bullets: list[GuideBullet], info: dict) -> list[str]:
    errors: list[str] = []

    type_counts = Counter(c.doc_type for c in chunks)
    for t, exp in EXPECTED_DOC_TYPES.items():
        if type_counts.get(t, 0) != exp:
            errors.append(f"doc_type {t}: 기대 {exp}, 실제 {type_counts.get(t, 0)}")

    expected_ids = (
        {"OV-details", "OV-candidate", "SCOPE-tech", "SCOPE-in", "SCOPE-out"}
        | {f"D{d}" for d in range(1, 6)}
        | {f"SC-{n}" for n in range(1, 7)}
        | {f"TS-{d}.{i}" for d, k in TS_PER_DOMAIN.items() for i in range(1, k + 1)}
        | {f"SQ-{n:02d}" for n in range(1, 13)}
        | {f"EX-{n}" for n in range(1, 5)}
    )
    ids = [c.doc_id for c in chunks]
    dup = [i for i, n in Counter(ids).items() if n > 1]
    if dup:
        errors.append(f"doc_id 중복: {dup}")
    if missing := sorted(expected_ids - set(ids)):
        errors.append(f"doc_id 누락: {missing}")
    if extra := sorted(set(ids) - expected_ids):
        errors.append(f"doc_id 초과: {extra}")

    for d, (exp_k, exp_s) in EXPECTED_BULLETS.items():
        k = sum(1 for b in bullets if b.domain == d and b.kind == "knowledge")
        s = sum(1 for b in bullets if b.domain == d and b.kind == "skill")
        if (k, s) != (exp_k, exp_s):
            errors.append(f"Domain {d} 불릿 K/S: 기대 {exp_k}/{exp_s}, 실제 {k}/{s}")

    per_ts = Counter(b.task_id for b in bullets)
    for tid, n in sorted(per_ts.items()):
        kinds = {b.kind for b in bullets if b.task_id == tid}
        if not BULLETS_PER_TS[0] <= n <= BULLETS_PER_TS[1] or kinds != {"knowledge", "skill"}:
            errors.append(f"{tid}: 불릿 {n}개 (허용 {BULLETS_PER_TS}), kinds={sorted(kinds)}")

    bdup = [i for i, n in Counter(b.bullet_id for b in bullets).items() if n > 1]
    if bdup:
        errors.append(f"bullet_id 중복: {bdup}")

    for b in bullets:
        if len(b.text) < 20:
            errors.append(f"{b.bullet_id}: 너무 짧음 {b.text!r}")
        bad = [f for f in FORBIDDEN_IN_BULLET if f in b.text]
        if bad:
            errors.append(f"{b.bullet_id}: 경계 처리 실패 의심 {bad} — {b.text[:60]!r}")

    for c in chunks:
        if c.doc_type == "sample_question" and "Correct Answer:" not in c.text:
            errors.append(f"{c.doc_id}: 'Correct Answer:' 없음 (문제 경계 오분할 의심)")
        if any(h in c.text for h in ("Claude Certification Program Exam guide",)):
            errors.append(f"{c.doc_id}: 머리말 잔존")

    if len(info.get("weights", {})) != 5:
        errors.append(f"Content Outline 도메인 비중 파싱 {len(info.get('weights', {}))}개 (기대 5)")
    return errors


def report(chunks: list[GuideChunk], bullets: list[GuideBullet]) -> None:
    type_counts = Counter(c.doc_type for c in chunks)
    print("\n[doc_type]   기대  실제")
    for t, exp in EXPECTED_DOC_TYPES.items():
        mark = "" if type_counts.get(t, 0) == exp else "  <-- 불일치"
        print(f"  {t:<16}{exp:>4}{type_counts.get(t, 0):>6}{mark}")
    print(f"  {'합계':<15}{sum(EXPECTED_DOC_TYPES.values()):>4}{len(chunks):>6}")

    print("\n[불릿]  기대 K/S  실제 K/S")
    for d, (ek, es) in EXPECTED_BULLETS.items():
        k = sum(1 for b in bullets if b.domain == d and b.kind == "knowledge")
        s = sum(1 for b in bullets if b.domain == d and b.kind == "skill")
        mark = "" if (k, s) == (ek, es) else "  <-- 불일치"
        print(f"  D{d}      {ek:>3}/{es:<3}    {k:>3}/{s:<3}{mark}")
    print(f"  합계    {sum(sum(v) for v in EXPECTED_BULLETS.values()):>7}    {len(bullets):>7}")


# ---------------------------------------------------------------- 4. 저장·적재

def write_jsonl(path, items) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(it.model_dump_json() + "\n")


def load_chroma(chunks: list[GuideChunk]) -> None:
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    emb = config.get_embeddings()
    # 재실행해도 62개가 유지되도록 기존 컬렉션을 지우고 새로 만든다
    Chroma(collection_name=config.COLLECTION, embedding_function=emb,
           persist_directory=str(config.CHROMA_DIR)).delete_collection()

    docs = [
        Document(
            page_content=c.text,
            metadata={k: v for k, v in c.model_dump(exclude={"text"}).items() if v is not None},
        )
        for c in chunks
    ]
    db = Chroma.from_documents(
        documents=docs,
        embedding=emb,
        ids=[c.doc_id for c in chunks],
        collection_name=config.COLLECTION,
        persist_directory=str(config.CHROMA_DIR),
    )
    count = db._collection.count()
    print(f"\n[chroma] {config.COLLECTION} 적재 {count}개 ({config.CHROMA_DIR})")
    if count != len(chunks):
        raise IngestError(f"Chroma 적재 개수 {count} != {len(chunks)}")

    for q in ["stop_reason tool_use end_turn agentic loop", "MCP isError structured error response"]:
        hits = db.similarity_search_with_score(q, k=3)
        print(f"  q={q!r}: " + ", ".join(f"{d.metadata['doc_id']}({s:.3f})" for d, s in hits))


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="파싱·검증만 하고 파일·Chroma는 건드리지 않음")
    args = ap.parse_args()

    try:
        lines = extract_lines(config.GUIDE_PDF)
        chunks, bullets, info = build_corpus(lines)
    except IngestError as e:
        print(f"[ingest] 파싱 오류: {e}", file=sys.stderr)
        return 1

    order = {t: i for i, t in enumerate(EXPECTED_DOC_TYPES)}
    chunks.sort(key=lambda c: (order[c.doc_type], c.doc_id))

    print(f"[ingest] {config.GUIDE_PDF.name}: {len(lines)}줄 → 청크 {len(chunks)}, 불릿 {len(bullets)}")
    if info["version"] != config.GUIDE_VERSION:
        print(f"[ingest] 경고: 가이드 버전 {info['version']} != 설정 {config.GUIDE_VERSION}")
    for d in info["dropped"]:
        print(f"[ingest] 제외 섹션: {d}")
    report(chunks, bullets)

    errors = validate(chunks, bullets, info)
    if errors:
        print(f"\n[ingest] 검증 실패 {len(errors)}건 — jsonl·Chroma를 변경하지 않고 중단:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\n[ingest] 검증 통과")

    if args.dry_run:
        print("[ingest] --dry-run: 저장·적재 생략")
        return 0

    write_jsonl(config.CHUNKS_PATH, chunks)
    write_jsonl(config.BULLETS_PATH, bullets)
    print(f"[ingest] 저장: {config.CHUNKS_PATH.name}({len(chunks)}), {config.BULLETS_PATH.name}({len(bullets)})")
    try:
        load_chroma(chunks)
    except IngestError as e:
        print(f"[ingest] 적재 오류: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
