"""문제 생성 → 코드 검증 → 독립 검토 → 재시도 (DESIGN §5-2). **오프라인 전용**.

scripts/build_quiz_pool.py에서만 호출한다. 에이전트 도구로 노출하거나 런타임(API·MCP 서버)에서
import하지 않는다 — 런타임에는 문제를 만들지 않는다 (CLAUDE.md, DESIGN §1-2).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src import config
from src.prompts import FIX_HINTS, load, load_pair
from src.schemas import (
    LABELS, GuideBullet, GuideChunk, ManifestSlot, QuizDraft, QuizItem, QuizOption, ReviewVerdict,
)

MAX_ATTEMPTS = 3            # 최초 1회 + 재시도 2회 (TS 4.4)
DUP_THRESHOLD = 0.92        # 같은 TS 안 stem 임베딩 코사인
EXPECTED_BULLETS = 240

# DESIGN §5-2 표: 도메인별 배정 시나리오 (해당 도메인이 primary domain인 시나리오만)
DOMAIN_SCENARIOS: dict[int, list[int]] = {
    1: [1, 3, 4], 2: [1, 3, 4], 3: [2, 4, 5], 4: [5, 6], 5: [1, 2, 3, 6],
}

# 일시 오류: 지수 백오프 대상이며 재시도 횟수(attempts)에 넣지 않는다
TRANSIENT_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelNotReadyException", "InternalServerException", "ModelTimeoutException",
}
BACKOFF_TRIES = 6


class CorpusError(Exception):
    pass


class TransientError(Exception):
    """백오프 후에도 Bedrock 일시 오류가 계속됨 → 슬롯을 pending으로 남긴다."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seeded_rng(*parts: str) -> random.Random:
    """PYTHONHASHSEED와 무관한 결정적 난수."""
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# ---------------------------------------------------------------- 코퍼스

@dataclass
class Corpus:
    chunks: dict[str, GuideChunk]
    bullets: list[GuideBullet]
    bullet_by_id: dict[str, GuideBullet]
    bullets_by_ts: dict[str, list[GuideBullet]]
    sq_by_scenario: dict[int, list[str]]          # SC 번호 → SQ doc_id 목록


def load_corpus() -> Corpus:
    for p in (config.CHUNKS_PATH, config.BULLETS_PATH):
        if not p.is_file():
            raise CorpusError(f"{p} 없음 — 먼저 python -m src.ingest 실행")
    chunks = [GuideChunk.model_validate_json(l) for l in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    bullets = [GuideBullet.model_validate_json(l) for l in config.BULLETS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(bullets) != EXPECTED_BULLETS:
        raise CorpusError(f"세부 항목 {len(bullets)}개 (기대 {EXPECTED_BULLETS}) — ingest 파싱 오류로 보고 중단")

    by_id = {c.doc_id: c for c in chunks}
    by_ts: dict[str, list[GuideBullet]] = defaultdict(list)
    for b in bullets:
        by_ts[b.task_id].append(b)

    # SQ 제목 "Sample Question 1 (Customer Support Resolution Agent)" ↔ SC 제목 "Scenario 1: Customer Support ..."
    sc_by_name = {c.title.split(": ", 1)[1]: int(c.doc_id.split("-")[1])
                  for c in chunks if c.doc_type == "scenario"}
    sq_by_sc: dict[int, list[str]] = defaultdict(list)
    for c in sorted((c for c in chunks if c.doc_type == "sample_question"), key=lambda c: c.doc_id):
        m = re.search(r"\((.+)\)\s*$", c.title)
        if not m or m.group(1) not in sc_by_name:
            raise CorpusError(f"{c.doc_id}: 시나리오를 찾을 수 없음 ({c.title!r})")
        sq_by_sc[sc_by_name[m.group(1)]].append(c.doc_id)

    return Corpus(by_id, bullets, {b.bullet_id: b for b in bullets}, dict(by_ts), dict(sq_by_sc))


def check_domain_scenarios(corpus: Corpus) -> list[str]:
    """DOMAIN_SCENARIOS의 각 조합이 SC 청크의 'Primary domains'에 실제로 있는지 확인."""
    errors = []
    for d, scs in DOMAIN_SCENARIOS.items():
        name = corpus.chunks[f"D{d}"].title.split(": ", 1)[1]
        for sc in scs:
            text = re.sub(r"\s+", " ", corpus.chunks[f"SC-{sc}"].text)
            primary = text.split("Primary domains:", 1)[1] if "Primary domains:" in text else ""
            if name not in primary:
                errors.append(f"D{d} ({name}) is not a primary domain of SC-{sc}")
    return errors


# ---------------------------------------------------------------- 컨텍스트

def focus_block(slot: ManifestSlot, corpus: Corpus) -> str:
    lines = []
    for bid in slot.focus_bullet_ids:
        b = corpus.bullet_by_id[bid]
        lines.append(f"- {bid} ({'Knowledge of' if b.kind == 'knowledge' else 'Skills in'}): {b.text}")
    return "\n".join(lines)


def pick_fewshot(quiz_id: str, domain: int, scenario_id: int, corpus: Corpus) -> list[str]:
    """슬롯 시나리오의 SQ를 우선 쓰고, 부족하면(SC-4·SC-6) 도메인 시나리오 목록 순서대로 채운다."""
    primary = list(corpus.sq_by_scenario.get(scenario_id, []))
    rng = seeded_rng("fewshot", quiz_id, str(scenario_id))
    if len(primary) >= 2:
        return sorted(rng.sample(primary, 2))
    pool = [sq for sc in DOMAIN_SCENARIOS[domain] if sc != scenario_id
            for sq in corpus.sq_by_scenario.get(sc, [])]
    if len(primary) + len(pool) < 2:
        pool = [sq for sc, sqs in sorted(corpus.sq_by_scenario.items()) for sq in sqs if sq not in primary]
    return primary + sorted(rng.sample(pool, 2 - len(primary)))


def build_vars(slot: ManifestSlot, scenario_id: int, corpus: Corpus) -> dict[str, str]:
    """생성·검토 프롬프트 공통 변수."""
    return {
        "task_id": slot.task_id,
        "domain": str(slot.domain),
        "ts_text": corpus.chunks[slot.task_id].text,
        "focus_block": focus_block(slot, corpus),
        "scenario_id": str(scenario_id),
        "scenario_text": corpus.chunks[f"SC-{scenario_id}"].text,
    }


# ---------------------------------------------------------------- 코드 검증

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", " ": " "})
HANGUL_RE = re.compile(r"[가-힣]")
# 보기를 섞으면 라벨이 바뀌므로 다른 보기를 라벨로 가리키면 안 된다
LABEL_REF_RES = [
    re.compile(r"(?i:보기|선택지|옵션|options?|choices?|answers?)\s*\(?\s*[A-D](?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_./-])[A-D]\)"),
    re.compile(r"\(\s*[A-D]\s*\)"),
    re.compile(r"(?<![A-Za-z0-9_./-])[A-D](?=\s?(?:와|과|는|은|도|가|이|를|을|의|보다|처럼|번|만|에서|로|으로)(?![가-힣]{2}))"),
]
MAX_CORRECT_LEN_RATIO = 1.25   # 정답 보기 길이 / 오답 평균 길이 (길이로 정답을 맞히는 표면 단서 방지)
MAX_CORRECT_VS_LONGEST = 1.15  # 정답 보기 길이 / 가장 긴 오답 길이
META_REF_RE = re.compile(r"(?i)focus item|초점\s*항목|세부\s*항목|task statement|source_excerpt|근거\s*발췌")
ABOVE_RE = re.compile(r"(?i)\b(?:all|none|both) of (?:the )?(?:above|these|options)\b")
GUIDE_REF_RE = re.compile(r"(?i)\b(?:exam guide|the guide|task statement)\b|\bTS-?\d\.\d")


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_QUOTES)
    s = re.sub(r"\s+", " ", s).strip().strip("\"'").strip(" .;:,")
    return s.casefold()


def find_label_refs(text: str) -> list[str]:
    return [m.group(0) for r in LABEL_REF_RES for m in r.finditer(text)]


def validate_draft(draft: QuizDraft, slot: ManifestSlot, scenario_id: int, corpus: Corpus) -> list[str]:
    """DESIGN §5-2 5단계 + 보기 섞기·언어 규칙. 오류는 재시도 피드백에 그대로 들어가므로 영어로 구체적으로 쓴다."""
    errs: list[str] = []

    # 슬롯 참조 실재
    if slot.task_id not in corpus.chunks:
        errs.append(f"unknown task_id {slot.task_id}")
    for bid in slot.focus_bullet_ids:
        b = corpus.bullet_by_id.get(bid)
        if b is None or b.task_id != slot.task_id:
            errs.append(f"focus bullet {bid} does not belong to {slot.task_id}")
    if f"SC-{scenario_id}" not in corpus.chunks:
        errs.append(f"unknown scenario SC-{scenario_id}")

    # 보기 구조
    labels = [o.label for o in draft.options]
    if len(draft.options) != 4:
        errs.append(f"options: expected exactly 4, got {len(draft.options)}")
    if sorted(labels) != list(LABELS):
        errs.append(f"option labels must be A, B, C, D once each, got {labels}")
    if draft.answer not in labels:
        errs.append(f"answer {draft.answer} is not one of the option labels {labels}")
    texts = [norm_text(o.text) for o in draft.options]
    if len(set(texts)) != len(texts):
        errs.append("two options have the same text")

    # 빈 값
    if len(draft.stem.strip()) < 40:
        errs.append("stem is missing or too short (describe a concrete situation in 2-5 sentences)")
    for o in draft.options:
        if not o.text.strip():
            errs.append(f"option {o.label} text is empty")
        if not o.rationale.strip():
            errs.append(f"option {o.label} rationale is empty")

    # 언어: stem·options 영어, rationale 한국어
    if HANGUL_RE.search(draft.stem) or any(HANGUL_RE.search(o.text) for o in draft.options):
        errs.append("stem and option texts must be in English (Korean characters found)")
    for o in draft.options:
        if o.rationale.strip() and not HANGUL_RE.search(o.rationale):
            errs.append(f"option {o.label} rationale must be written in Korean")

    # 라벨 참조·금지 표현
    for where, text in [("stem", draft.stem)] + [(f"option {o.label} text", o.text) for o in draft.options] \
            + [(f"option {o.label} rationale", o.rationale) for o in draft.options]:
        refs = find_label_refs(text)
        if refs:
            errs.append(f"{where} refers to options by letter {refs}; options are shuffled later, "
                        "so describe the other approach by its content instead")
    for o in draft.options:
        if META_REF_RE.search(o.rationale):
            errs.append(f"option {o.label} rationale mentions internal terms ('focus item', 'task statement', "
                        "source_excerpt); learners read rationales, so explain the idea itself")

    # 정답이 길이로 드러나지 않게
    if len(draft.options) == 4 and draft.answer in labels:
        correct_len = next(len(o.text) for o in draft.options if o.label == draft.answer)
        wrong = [len(o.text) for o in draft.options if o.label != draft.answer]
        ratio = correct_len / (sum(wrong) / len(wrong)) if sum(wrong) else 99.0
        if ratio > MAX_CORRECT_LEN_RATIO:
            errs.append(f"correct option is {ratio:.2f}x the average distractor length (max {MAX_CORRECT_LEN_RATIO}); "
                        "shorten the correct option or make the distractors equally specific so length is not a cue")
        elif max(wrong) and correct_len > MAX_CORRECT_VS_LONGEST * max(wrong):
            errs.append(f"correct option is {correct_len / max(wrong):.2f}x the longest distractor "
                        f"(max {MAX_CORRECT_VS_LONGEST}); make at least one distractor as long and detailed as the correct option")

    if ABOVE_RE.search(draft.stem + " " + " ".join(o.text for o in draft.options)):
        errs.append("'all/none/both of the above' style options are not allowed")
    if GUIDE_REF_RE.search(draft.stem + " " + " ".join(o.text for o in draft.options)):
        errs.append("stem/options must not mention the exam guide, task statements, or TS ids")

    # 근거 발췌: TS 원문의 연속 구간 (초점 항목에 맞는지는 검토자가 off_focus_bullet으로 판정)
    ex = norm_text(draft.source_excerpt)
    ts_chunk = corpus.chunks.get(slot.task_id)
    ts_lines = [norm_text(l.removeprefix("- ")) for l in ts_chunk.text.splitlines()] if ts_chunk else []
    if len(ex) < 15:
        errs.append("source_excerpt is missing or too short")
    elif not any(ex in t for t in ts_lines):
        errs.append(f"source_excerpt is not a verbatim span of a single line of the {slot.task_id} text; "
                    "copy it character for character from one item without paraphrasing, joining items, or ellipses")
    return errs


# ---------------------------------------------------------------- 보기 섞기

def shuffle_options(draft: QuizDraft, quiz_id: str, target: str) -> QuizDraft:
    """정답을 target 위치에 놓고 오답 3개는 quiz_id 시드로 섞는다. rationale은 보기와 함께 이동.
    입력 보기 순서와 무관하게 같은 결과(오답을 텍스트로 정렬한 뒤 섞음) → 결정적·멱등."""
    correct = next(o for o in draft.options if o.label == draft.answer)
    wrong = sorted((o for o in draft.options if o.label != draft.answer), key=lambda o: o.text)
    seeded_rng("shuffle", quiz_id).shuffle(wrong)
    ordered = list(wrong)
    ordered.insert(LABELS.index(target), correct)
    options = [QuizOption(label=lab, text=o.text, rationale=o.rationale) for lab, o in zip(LABELS, ordered)]
    return draft.model_copy(update={"options": options, "answer": target})


# ---------------------------------------------------------------- 중복 제거

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def dedupe(items: list[QuizItem], order: dict[str, int],
           embed: Callable[[list[str]], list[list[float]]]) -> list[tuple[str, str, float]]:
    """approved 문항을 task_id별·order 순으로 훑어, 앞에서 남긴 문항과 stem 코사인 ≥ 0.92면 뒤 문항을 중복으로 판정.
    반환: [(중복 quiz_id, 원본 quiz_id, 유사도)]. 상태 변경은 호출부가 한다."""
    approved = sorted((i for i in items if i.status == "approved"), key=lambda i: order.get(i.quiz_id, 10**9))
    if not approved:
        return []
    vecs = embed([i.stem for i in approved])
    kept: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    dups = []
    for it, v in zip(approved, vecs):
        best_sim, best_id = max(((_cosine(v, kv), kid) for kid, kv in kept[it.task_id]), default=(0.0, ""))
        if best_sim >= DUP_THRESHOLD:
            dups.append((it.quiz_id, best_id, best_sim))
        else:
            kept[it.task_id].append((it.quiz_id, v))
    return dups


# ---------------------------------------------------------------- LLM 호출

def _is_transient(e: BaseException, depth: int = 0) -> bool:
    if e is None or depth > 5:
        return False
    try:
        from botocore.exceptions import (ClientError, ConnectTimeoutError, EndpointConnectionError,
                                         ReadTimeoutError)
        if isinstance(e, ClientError):
            return e.response.get("Error", {}).get("Code") in TRANSIENT_CODES
        if isinstance(e, (ReadTimeoutError, ConnectTimeoutError, EndpointConnectionError)):
            return True
    except ImportError:
        pass
    if any(code in str(e) for code in TRANSIENT_CODES):
        return True
    return _is_transient(e.__cause__ or e.__context__, depth + 1)


def invoke_with_backoff(runnable, messages):
    for n in range(BACKOFF_TRIES):
        try:
            return runnable.invoke(messages)
        except Exception as e:  # noqa: BLE001
            if not _is_transient(e):
                raise
            if n == BACKOFF_TRIES - 1:
                raise TransientError(f"Bedrock transient error after {BACKOFF_TRIES} tries: {e}") from e
            time.sleep(min(60, 2 ** n) + random.uniform(0, 1))


def _raw_output(raw) -> str:
    """파싱 실패 시 피드백에 넣을 원문 (tool call 인자 또는 텍스트)."""
    if raw is None:
        return "(no output)"
    calls = getattr(raw, "tool_calls", None) or []
    if calls:
        return json.dumps(calls[0].get("args", {}), ensure_ascii=False, indent=2)[:3000]
    return (config.get_text(raw) or "(empty)")[:3000]


# ---------------------------------------------------------------- 슬롯 실행

@dataclass
class Attempt:
    n: int
    scenario_id: int
    pattern: str                     # parse | code | review 패턴
    problems: list[str]
    item_json: str


@dataclass
class SlotResult:
    quiz_id: str
    status: str                      # approved | rejected
    attempts: int
    scenario_id: int
    detected_patterns: list[str]
    last_errors: list[str]
    item: Optional[QuizItem]         # 코드 검증을 통과한 마지막 문항 (없으면 None)
    log: list[str] = field(default_factory=list)


class QuizGenerator:
    def __init__(self, corpus: Corpus, llm=None):
        llm = llm or config.get_llm()
        self.corpus = corpus
        self.gen = llm.with_structured_output(QuizDraft, include_raw=True)
        self.rev = llm.with_structured_output(ReviewVerdict, include_raw=True)
        self.gen_system, self.gen_user = load_pair("quiz_generate")
        self.rev_system, self.rev_user = load_pair("quiz_review")
        self.retry_tpl = load("quiz_retry")

    # -- 프롬프트 조립
    def generation_messages(self, slot: ManifestSlot, scenario_id: int, avoid_stems: list[str],
                            history: list[Attempt], scenario_note: str = "") -> list:
        v = build_vars(slot, scenario_id, self.corpus)
        sqs = pick_fewshot(slot.quiz_id, slot.domain, scenario_id, self.corpus)
        v["fewshot_block"] = "\n\n".join(f"## {sq}\n{self.corpus.chunks[sq].text}" for sq in sqs)
        v["avoid_block"] = (
            "\n# EXISTING QUESTIONS ON THIS TASK STATEMENT (do not repeat their situation or decision)\n"
            + "\n".join(f"- {s}" for s in avoid_stems) + "\n"
        ) if avoid_stems else ""
        v["retry_block"] = ("\n" + self.retry_block(history, scenario_note) + "\n") if history else ""
        return [SystemMessage(self.gen_system), HumanMessage(self.gen_user.substitute(v))]

    def retry_block(self, history: list[Attempt], scenario_note: str = "") -> str:
        """재시도 피드백: 직전 실패 문항 전체 + 구체적 오류 + 패턴별 지시 + 이전 시도 요약 (DESIGN §5-2 7단계)."""
        last = history[-1]
        kinds = []
        for p in last.problems:
            k = "code" if p.startswith("[code]") else "parse" if p.startswith("[parse]") else last.pattern
            if k not in kinds:
                kinds.append(k)
        hints = "\n".join(f"- {FIX_HINTS.get(k, FIX_HINTS['other'])}" for k in kinds)
        earlier = "\n".join(
            f"- attempt {a.n}: {a.pattern} — {a.problems[0][:200] if a.problems else ''}" for a in history[:-1]
        ) or "- (none)"
        return self.retry_tpl.substitute(
            attempt=last.n, max_attempts=MAX_ATTEMPTS,
            scenario_note=f"\n**{scenario_note}**\n" if scenario_note else "",
            item_json=last.item_json,
            problems="\n".join(f"- {p}" for p in last.problems),
            hints=hints, history=earlier,
        )

    def review_messages(self, slot: ManifestSlot, scenario_id: int, draft: QuizDraft) -> list:
        v = build_vars(slot, scenario_id, self.corpus)
        v["item_json"] = draft.model_dump_json(indent=2)
        return [SystemMessage(self.rev_system), HumanMessage(self.rev_user.substitute(v))]

    # -- 호출
    def generate(self, messages) -> tuple[Optional[QuizDraft], Optional[str], str]:
        out = invoke_with_backoff(self.gen, messages)
        parsed, err = out.get("parsed"), out.get("parsing_error")
        if parsed is None:
            return None, str(err or "no structured output returned"), _raw_output(out.get("raw"))
        return parsed, None, parsed.model_dump_json(indent=2)

    def review(self, messages) -> ReviewVerdict:
        last_err = None
        for _ in range(2):          # 검토자 출력 형식 오류는 생성자 탓이 아니므로 검토만 다시 부른다
            out = invoke_with_backoff(self.rev, messages)
            if out.get("parsed") is not None:
                return out["parsed"]
            last_err = out.get("parsing_error")
        raise RuntimeError(f"reviewer output unparsable: {last_err}")

    # -- 한 슬롯
    def run_slot(self, slot: ManifestSlot, avoid_stems: list[str] | None = None) -> SlotResult:
        avoid_stems = avoid_stems or []
        scenario_id = slot.scenario_id
        history: list[Attempt] = []
        patterns: list[str] = []
        last_item: Optional[QuizItem] = None
        scenario_note = ""
        log: list[str] = []

        for n in range(1, MAX_ATTEMPTS + 1):
            msgs = self.generation_messages(slot, scenario_id, avoid_stems, history, scenario_note)
            draft, parse_err, item_json = self.generate(msgs)

            if parse_err:
                att = Attempt(n, scenario_id, "parse", [f"[parse] {parse_err}"], item_json)
            elif errs := validate_draft(draft, slot, scenario_id, self.corpus):
                att = Attempt(n, scenario_id, "code", [f"[code] {e}" for e in errs], item_json)
            else:
                verdict = self.review(self.review_messages(slot, scenario_id, draft))
                ok = verdict.valid and verdict.detected_pattern is None and not verdict.issues
                if ok:
                    final = shuffle_options(draft, slot.quiz_id, slot.target_answer)
                    item = self._item(slot, scenario_id, final, "approved", n, patterns)
                    log.append(f"attempt {n}: approved (SC-{scenario_id})")
                    return SlotResult(slot.quiz_id, "approved", n, scenario_id, patterns, [], item, log)
                pat = verdict.detected_pattern or "other"
                issues = verdict.issues or ["(reviewer gave no detail)"]
                att = Attempt(n, scenario_id, pat, [f"[review:{pat}] {i}" for i in issues], item_json)
                last_item = self._item(slot, scenario_id, draft, "rejected", n, patterns + [pat])

            history.append(att)
            patterns.append(att.pattern)
            log.append(f"attempt {n}: {att.pattern} — {att.problems[0][:160]}")

            scenario_note = ""
            # 마지막 시도 뒤에는 바꾸지 않는다 (매니페스트에는 실제로 마지막에 시도한 시나리오가 남아야 함)
            if att.pattern == "scenario_mismatch" and len(slot.scenario_candidates) > 1 and n < MAX_ATTEMPTS:
                cands = slot.scenario_candidates
                nxt = cands[(cands.index(scenario_id) + 1) % len(cands)] if scenario_id in cands else cands[0]
                scenario_note = f"The scenario has changed from SC-{scenario_id} to SC-{nxt}; rewrite the situation for SC-{nxt}."
                scenario_id = nxt

        if last_item is not None:
            last_item = last_item.model_copy(update={"detected_patterns": patterns})
        return SlotResult(slot.quiz_id, "rejected", MAX_ATTEMPTS, scenario_id, patterns,
                          history[-1].problems, last_item, log)

    def _item(self, slot: ManifestSlot, scenario_id: int, draft: QuizDraft, status: str,
              attempts: int, patterns: list[str]) -> QuizItem:
        return QuizItem(
            quiz_id=slot.quiz_id, task_id=slot.task_id, domain=slot.domain,
            focus_bullet_ids=slot.focus_bullet_ids, scenario_id=scenario_id,
            stem=draft.stem, options=draft.options, answer=draft.answer,
            source_excerpt=draft.source_excerpt, status=status,
            review_retries=attempts - 1, detected_patterns=list(patterns),
            generator_model=config.MODEL_ID, guide_version=config.GUIDE_VERSION, created_at=now_iso(),
        )
