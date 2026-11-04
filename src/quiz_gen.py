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
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from string import Template
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src import config
from src.prompts import FIX_HINTS, load, load_pair
from src.schemas import (
    LABELS, GuideBullet, GuideChunk, ManifestSlot, QuizDraft, QuizItem, QuizOption, QuizOptionsRepair,
    ReviewVerdict,
)

MAX_ATTEMPTS = 3            # 최초 1회 + 재시도 2회 (TS 4.4)
DUP_THRESHOLD = 0.92        # 같은 TS 안 stem 임베딩 코사인
EXPECTED_BULLETS = 240

# DESIGN §5-2 표: 도메인별 배정 시나리오 (해당 도메인이 primary domain인 시나리오만)
DOMAIN_SCENARIOS: dict[int, list[int]] = {
    1: [1, 3, 4], 2: [1, 3, 4], 3: [2, 4, 5], 4: [5, 6], 5: [1, 2, 3, 6],
}
ALL_SCENARIOS = [1, 2, 3, 4, 5, 6]
# 어떤 경우에도 만들지 않는 TS × 시나리오 조합. 보충 슬롯의 primary 제한 완화에서도 유지한다.
# TS-1.7 × SC-5: evaluation/test_queries.csv 13번이 이 조합을 풀에 없는 조합(no_match → alternatives)으로 전제
FORBIDDEN_SCENARIOS: dict[str, set[int]] = {"TS-1.7": {5}}


def allowed_scenarios(task_id: str) -> list[int]:
    return [n for n in ALL_SCENARIOS if n not in FORBIDDEN_SCENARIOS.get(task_id, set())]

# 일시 오류: 지수 백오프 대상이며 재시도 횟수(attempts)에 넣지 않는다
TRANSIENT_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelNotReadyException", "InternalServerException", "ModelTimeoutException",
}
# 모델 전환 (ModelPool): 쓰로틀된 모델은 min(COOLDOWN_CAP, COOLDOWN_BASE·2^k)초 쉬고 다음 모델로 바로 넘어감.
# 체인 전체가 막혀 한 호출에서 기다린 시간이 WAIT_BUDGET을 넘으면 TransientError (슬롯은 pending).
# 일일 토큰 할당량 소진("Too many tokens per day")은 분 단위로 풀리지 않으므로 DAILY_COOLDOWN 동안 쉬게 한다.
DAILY_COOLDOWN = 3600
POOL_BOTOCORE_RETRIES = 1      # botocore 내부 재시도 (기본 4회 → 1회). 전환은 ModelPool이 맡는다
COOLDOWN_BASE = 4
COOLDOWN_CAP = 60
WAIT_BUDGET = 180


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


# 코드 검증 세부 패턴. 앞쪽이 더 심각 — 시도 하나의 대표 패턴은 이 순서로 고른다 (DESIGN §5-2)
CODE_PATTERNS = (
    "format", "unknown_ref", "excerpt_not_verbatim", "language",
    "label_reference", "meta_reference", "forbidden_phrase", "answer_too_long",
)
CodeIssue = tuple[str, str]       # (pattern, message)


def primary_pattern(issues: list[CodeIssue]) -> str:
    return min((p for p, _ in issues), key=CODE_PATTERNS.index)


def length_stats(draft: QuizDraft) -> Optional[dict]:
    """정답·오답 길이(문자 수). 보기 구조가 깨졌으면 None."""
    labels = [o.label for o in draft.options]
    if len(draft.options) != 4 or draft.answer not in labels:
        return None
    correct = next(len(o.text) for o in draft.options if o.label == draft.answer)
    wrong = [len(o.text) for o in draft.options if o.label != draft.answer]
    avg = sum(wrong) / len(wrong)
    return {"correct": correct, "avg_wrong": avg, "max_wrong": max(wrong),
            "ratio_avg": correct / avg if avg else 99.0,
            "ratio_max": correct / max(wrong) if max(wrong) else 99.0}


def merge_repair(draft: QuizDraft, repaired: "QuizOptionsRepair") -> tuple[Optional[QuizDraft], str]:
    """repair 결과에서 options만 받아 원본 draft에 끼운다. 라벨 집합이 바뀌면 거부."""
    if sorted(o.label for o in repaired.options) != sorted(o.label for o in draft.options):
        return None, f"repair changed option labels to {[o.label for o in repaired.options]}"
    options = sorted(repaired.options, key=lambda o: o.label)
    return draft.model_copy(update={"options": options}), ""


def validate_draft(draft: QuizDraft, slot: ManifestSlot, scenario_id: int, corpus: Corpus) -> list[CodeIssue]:
    """DESIGN §5-2 코드 검증. 메시지는 재시도·repair 피드백에 그대로 들어가므로 영어로 구체적으로 쓴다."""
    errs: list[CodeIssue] = []

    # 슬롯 참조 실재
    if slot.task_id not in corpus.chunks:
        errs.append(("unknown_ref", f"unknown task_id {slot.task_id}"))
    for bid in slot.focus_bullet_ids:
        b = corpus.bullet_by_id.get(bid)
        if b is None or b.task_id != slot.task_id:
            errs.append(("unknown_ref", f"focus bullet {bid} does not belong to {slot.task_id}"))
    if f"SC-{scenario_id}" not in corpus.chunks:
        errs.append(("unknown_ref", f"unknown scenario SC-{scenario_id}"))

    # 보기 구조·빈 값
    labels = [o.label for o in draft.options]
    if len(draft.options) != 4:
        errs.append(("format", f"options: expected exactly 4, got {len(draft.options)}"))
    if sorted(labels) != list(LABELS):
        errs.append(("format", f"option labels must be A, B, C, D once each, got {labels}"))
    if draft.answer not in labels:
        errs.append(("format", f"answer {draft.answer} is not one of the option labels {labels}"))
    texts = [norm_text(o.text) for o in draft.options]
    if len(set(texts)) != len(texts):
        errs.append(("format", "two options have the same text"))
    if len(draft.stem.strip()) < 40:
        errs.append(("format", "stem is missing or too short (describe a concrete situation in 2-5 sentences)"))
    for o in draft.options:
        if not o.text.strip():
            errs.append(("format", f"option {o.label} text is empty"))
        if not o.rationale.strip():
            errs.append(("format", f"option {o.label} rationale is empty"))

    # 언어: stem·options 영어, rationale 한국어
    if HANGUL_RE.search(draft.stem) or any(HANGUL_RE.search(o.text) for o in draft.options):
        errs.append(("language", "stem and option texts must be in English (Korean characters found)"))
    for o in draft.options:
        if o.rationale.strip() and not HANGUL_RE.search(o.rationale):
            errs.append(("language", f"option {o.label} rationale must be written in Korean"))

    # 라벨 참조·내부 용어·금지 표현
    for where, text in [("stem", draft.stem)] + [(f"option {o.label} text", o.text) for o in draft.options] \
            + [(f"option {o.label} rationale", o.rationale) for o in draft.options]:
        refs = find_label_refs(text)
        if refs:
            errs.append(("label_reference", f"{where} refers to options by letter {refs}; options are shuffled "
                         "later, so describe the other approach by its content instead"))
    for o in draft.options:
        if META_REF_RE.search(o.rationale):
            errs.append(("meta_reference", f"option {o.label} rationale mentions internal terms ('focus item', "
                         "'task statement', source_excerpt); learners read rationales, so explain the idea itself"))
    visible = draft.stem + " " + " ".join(o.text for o in draft.options)
    if ABOVE_RE.search(visible):
        errs.append(("forbidden_phrase", "'all/none/both of the above' style options are not allowed"))
    if GUIDE_REF_RE.search(visible):
        errs.append(("forbidden_phrase", "stem/options must not mention the exam guide, task statements, or TS ids"))

    # 정답이 길이로 드러나지 않게
    st = length_stats(draft)
    if st is not None:
        if st["ratio_avg"] > MAX_CORRECT_LEN_RATIO:
            errs.append(("answer_too_long", f"correct option is {st['ratio_avg']:.2f}x the average distractor length "
                         f"(max {MAX_CORRECT_LEN_RATIO}); shorten the correct option or make the distractors "
                         "equally specific so length is not a cue"))
        elif st["ratio_max"] > MAX_CORRECT_VS_LONGEST:
            errs.append(("answer_too_long", f"correct option is {st['ratio_max']:.2f}x the longest distractor "
                         f"(max {MAX_CORRECT_VS_LONGEST}); make at least one distractor as long and detailed "
                         "as the correct option"))

    # 근거 발췌: TS 원문 한 줄의 연속 구간 (초점 항목에 맞는지는 검토자가 off_focus_bullet으로 판정)
    ex = norm_text(draft.source_excerpt)
    ts_chunk = corpus.chunks.get(slot.task_id)
    ts_lines = [norm_text(l.removeprefix("- ")) for l in ts_chunk.text.splitlines()] if ts_chunk else []
    if len(ex) < 15:
        errs.append(("excerpt_not_verbatim", "source_excerpt is missing or too short"))
    elif not any(ex in t for t in ts_lines):
        errs.append(("excerpt_not_verbatim", f"source_excerpt is not a verbatim span of a single line of the "
                     f"{slot.task_id} text; copy it character for character from one item without paraphrasing, "
                     "joining items, or ellipses"))
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


def _is_unavailable(e: BaseException) -> bool:
    """이 계정·리전에서 쓸 수 없는 모델 (권한 없음, 모델 id 없음). 체인에서 빼고 다음 모델로 간다."""
    try:
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError):
            return e.response.get("Error", {}).get("Code") in ("AccessDeniedException", "ResourceNotFoundException")
    except ImportError:
        pass
    return "AccessDeniedException" in str(e) or "ResourceNotFoundException" in str(e)


class ModelPool:
    """문제 풀 생성 전용 LLM 호출기 (DESIGN §5-2 모델 전환). 스레드 안전.

    체인(config.QUIZ_GEN_MODEL_CHAIN) 순서대로 쿨다운이 끝난 첫 모델을 쓴다. 일시 오류(쓰로틀링 등)가 나면
    그 모델에 쿨다운(min(60, 4·2^k)초)을 걸고 기다리지 않고 다음 모델로 넘어간다. 모두 쿨다운 중이면 가장 빨리
    풀리는 시각까지 자고, 한 호출의 총 대기가 WAIT_BUDGET을 넘으면 TransientError (슬롯은 pending, attempts 미증가).
    일시 오류가 아닌 오류는 전환하지 않고 그대로 올린다. 권한 없음·모델 없음은 그 모델을 이번 실행에서 뺀다."""

    def __init__(self, model_ids: list[str] | None = None, factory=None, schemas: dict | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        from src.schemas import ScenarioFit
        self.model_ids = list(model_ids or config.QUIZ_GEN_MODEL_CHAIN)
        factory = factory or (lambda mid: config.get_llm(mid, max_retries=POOL_BOTOCORE_RETRIES))
        schemas = schemas or {"draft": QuizDraft, "review": ReviewVerdict,
                              "repair": QuizOptionsRepair, "scenario": ScenarioFit}
        self.runnables = {}
        for mid in self.model_ids:
            llm = factory(mid)
            self.runnables[mid] = {k: llm.with_structured_output(s, include_raw=True) for k, s in schemas.items()}
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._cool_until = {m: 0.0 for m in self.model_ids}
        self._streak = {m: 0 for m in self.model_ids}
        self._disabled: set[str] = set()
        self.stats = {m: Counter() for m in self.model_ids}     # ok, throttled, unavailable
        self.switches = 0                                       # 한 호출 안에서 다른 모델로 넘어간 횟수
        self.wait_budget = WAIT_BUDGET

    def _pick(self) -> tuple[Optional[str], float]:
        with self._lock:
            live = [m for m in self.model_ids if m not in self._disabled]
            if not live:
                return None, -1.0
            now = self._clock()
            for m in live:
                if self._cool_until[m] <= now:
                    return m, 0.0
            return None, min(self._cool_until[m] for m in live) - now

    def invoke(self, key: str, messages) -> tuple[dict, str]:
        waited, prev, last_err = 0.0, None, None
        while True:
            m, wait = self._pick()
            if m is None:
                if wait < 0:
                    raise TransientError(f"no usable model in chain (all unavailable): {last_err}")
                if waited + wait > self.wait_budget:
                    raise TransientError(f"all {len(self.model_ids)} models throttled; waited {waited:.0f}s: {last_err}")
                t = wait + random.uniform(0, 1)
                self._sleep(t)
                waited += t
                continue
            if prev is not None and m != prev:
                with self._lock:
                    self.switches += 1
            prev = m
            try:
                out = self.runnables[m][key].invoke(messages)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if _is_transient(e):
                    daily = "per day" in str(e).lower()
                    with self._lock:
                        self._streak[m] += 1
                        cool = DAILY_COOLDOWN if daily else min(COOLDOWN_CAP, COOLDOWN_BASE * 2 ** (self._streak[m] - 1))
                        first_daily = daily and self.stats[m]["daily_quota"] == 0
                        self._cool_until[m] = self._clock() + cool
                        self.stats[m]["throttled"] += 1
                        self.stats[m]["daily_quota"] += daily
                    if first_daily:
                        print(f"[model] {short_model(m)} 일일 토큰 할당량 소진 — {DAILY_COOLDOWN // 60}분간 다음 모델 사용",
                              flush=True)
                    continue
                if _is_unavailable(e):
                    with self._lock:
                        self._disabled.add(m)
                        self.stats[m]["unavailable"] += 1
                    print(f"[model] {m} 사용 불가 — 이번 실행에서 제외 ({type(e).__name__})", flush=True)
                    continue
                raise
            with self._lock:
                self._streak[m] = 0
                self.stats[m]["ok"] += 1
            return out, m

    def report(self) -> str:
        parts = [f"{short_model(m)} ok {c['ok']}/throttled {c['throttled']}"
                 + (" (일일 할당량 소진)" if c["daily_quota"] else "")
                 + (" (사용 불가)" if m in self._disabled else "") for m, c in self.stats.items()]
        return "; ".join(parts) + f" | 전환 {self.switches}회"


def short_model(model_id: Optional[str]) -> str:
    """'global.anthropic.claude-sonnet-4-6' → 'global/sonnet-4-6' (요약 출력용)."""
    if not model_id:
        return "-"
    prefix, _, rest = model_id.partition(".anthropic.claude-")
    rest = re.sub(r"-\d{8}-v\d+:\d+$", "", rest)
    return f"{prefix}/{rest}"


def _raw_output(raw) -> str:
    """파싱 실패 시 피드백에 넣을 원문 (tool call 인자 또는 텍스트)."""
    if raw is None:
        return "(no output)"
    calls = getattr(raw, "tool_calls", None) or []
    if calls:
        return json.dumps(calls[0].get("args", {}), ensure_ascii=False, indent=2)[:3000]
    return (config.get_text(raw) or "(empty)")[:3000]


# ---------------------------------------------------------------- 보충 슬롯 시나리오 (시나리오 불일치 탈락)

def failure_patterns(slot: ManifestSlot) -> list[str]:
    """시도별 실패 패턴만 (repair 성공 표시·중복 표시 제외)."""
    return [p for p in slot.detected_patterns if ":" not in p]


def mismatched_scenarios(slot: ManifestSlot) -> list[int]:
    """slot에서 scenario_mismatch가 난 시나리오들을 복원한다.
    run_slot은 마지막 시도가 아닌 불일치마다 후보 목록의 다음 시나리오로 한 칸 이동하므로,
    최종 시나리오에서 이동 횟수만큼 거꾸로 짚으면 불일치가 난 시나리오가 나온다."""
    pats = failure_patterns(slot)
    n = pats.count("scenario_mismatch")
    if not n:
        return []
    last_attempt_mismatch = slot.status == "rejected" and pats and pats[-1] == "scenario_mismatch" \
        and slot.attempts == MAX_ATTEMPTS
    moves = n - (1 if last_attempt_mismatch else 0)
    cands = slot.scenario_candidates
    if slot.scenario_id not in cands:
        return [slot.scenario_id] if last_attempt_mismatch else []
    i = cands.index(slot.scenario_id)
    out = [cands[(i - k) % len(cands)] for k in range(moves, 0, -1)]
    if last_attempt_mismatch:
        out.append(slot.scenario_id)
    return list(dict.fromkeys(out))


def rejected_for_scenario(slot: ManifestSlot) -> bool:
    """최종 탈락 사유가 scenario_mismatch인 슬롯."""
    pats = failure_patterns(slot)
    return slot.status == "rejected" and bool(pats) and pats[-1] == "scenario_mismatch"


def order_scenarios(ranking: list[int], task_id: str, failed: list[int]) -> list[int]:
    """LLM 순위를 정리: 허용 시나리오만, 중복 제거, 빠진 것은 번호순으로 뒤에, 이미 불일치한 시나리오는 맨 뒤."""
    allowed = allowed_scenarios(task_id)
    ranked = [n for n in dict.fromkeys(ranking) if n in allowed]
    ranked += [n for n in allowed if n not in ranked]
    return [n for n in ranked if n not in failed] + [n for n in ranked if n in failed]


class ScenarioRanker:
    """세부 항목 내용에 가장 맞는 시나리오 순위 (보충 슬롯 전용, 오프라인). 시나리오 설명의
    'Primary domains' 줄은 빼고 보여 준다 — 도메인 primary 제한을 푸는 것이 목적이므로."""

    def __init__(self, corpus: Corpus, pool: "ModelPool | None" = None):
        self.corpus = corpus
        self.pool = pool or ModelPool()
        self.system, self.user = load_pair("scenario_fit")

    def messages(self, bullet: GuideBullet) -> list:
        allowed = allowed_scenarios(bullet.task_id)
        scen = "\n\n".join(
            f"## SC-{n}\n" + self.corpus.chunks[f"SC-{n}"].text.split("Primary domains:")[0].strip()
            for n in allowed
        )
        v = {"task_id": bullet.task_id, "ts_title": self.corpus.chunks[bullet.task_id].title,
             "bullet_id": bullet.bullet_id, "bullet_text": bullet.text, "scenarios": scen}
        return [SystemMessage(self.system), HumanMessage(self.user.substitute(v))]

    def rank(self, bullet: GuideBullet) -> tuple[list[int], str]:
        out, _ = self.pool.invoke("scenario", self.messages(bullet))
        parsed = out.get("parsed")
        if parsed is None:
            raise RuntimeError(f"scenario ranking unparsable for {bullet.bullet_id}: {out.get('parsing_error')}")
        return list(parsed.ranking), parsed.reason


# ---------------------------------------------------------------- 추가 슬롯 쌍 선정

STOPWORDS = set("""
the a an and or of to in for with on by as at from into that this these those is are be been being when which
while than via using use used their its it not only each other such eg e g vs between before after through
including include across both all any more most how what where who whether can could should would may might must
rather additional multiple specific appropriate based within without per like also them they you your one two
about then well need
""".split())


def keywords(text: str) -> set[str]:
    """소문자 토큰 → 불용어 제거 → 간이 어간(-ing/-es/-s/-ed, 남는 길이 4자 이상)."""
    out = set()
    for w in re.findall(r"[a-z][a-z0-9_]+", text.lower()):
        if len(w) < 3 or w in STOPWORDS:
            continue
        for suf in ("ing", "es", "s", "ed"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def pair_extra_bullets(bullets: list[GuideBullet], n: int) -> list[tuple[str, str, list[str]]]:
    """같은 TS 안 K×S 쌍 중 키워드 겹침이 큰 순으로 n개 (이미 고른 쌍 제외). 반환: [(K id, S id, 겹친 키워드)].
    점수: 겹친 수 → Jaccard → 아직 안 쓴 불릿 수 → (K 번호, S 번호) 오름차순."""
    def num(bid: str) -> int:
        return int(bid.rsplit("-", 1)[1][1:])

    ks = sorted((b for b in bullets if b.kind == "knowledge"), key=lambda b: num(b.bullet_id))
    ss = sorted((b for b in bullets if b.kind == "skill"), key=lambda b: num(b.bullet_id))
    kw = {b.bullet_id: keywords(b.text) for b in ks + ss}
    chosen: list[tuple[str, str, list[str]]] = []
    used_bullets: set[str] = set()
    for _ in range(n):
        best = None
        for k in ks:
            for s in ss:
                if any(k.bullet_id == c[0] and s.bullet_id == c[1] for c in chosen):
                    continue
                shared = kw[k.bullet_id] & kw[s.bullet_id]
                union = kw[k.bullet_id] | kw[s.bullet_id]
                score = (len(shared), len(shared) / len(union) if union else 0.0,
                         (k.bullet_id not in used_bullets) + (s.bullet_id not in used_bullets),
                         -num(k.bullet_id), -num(s.bullet_id))
                if best is None or score > best[0]:
                    best = (score, k.bullet_id, s.bullet_id, sorted(shared))
        if best is None:
            break
        chosen.append((best[1], best[2], best[3]))
        used_bullets.update((best[1], best[2]))
    return chosen


# ---------------------------------------------------------------- 슬롯 실행

@dataclass
class Attempt:
    n: int
    scenario_id: int
    pattern: str                     # parse | 코드 세부 패턴 | 검토 패턴
    problems: list[str]              # "[parse] …", "[code:answer_too_long] …", "[review:multiple_correct] …"
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
    repairs: int = 0
    repairs_ok: int = 0
    log: list[str] = field(default_factory=list)
    generator_model: Optional[str] = None   # 최종 문항을 만든 생성 호출의 모델
    reviewer_model: Optional[str] = None    # 승인 또는 마지막 탈락 판정을 낸 검토 호출의 모델


def _problem_kind(problem: str, fallback: str) -> str:
    if problem.startswith("[parse]"):
        return "parse"
    m = re.match(r"\[code:([a-z_]+)\]", problem)
    return m.group(1) if m else fallback


class QuizGenerator:
    def __init__(self, corpus: Corpus, pool: "ModelPool | None" = None):
        self.corpus = corpus
        self.pool = pool or ModelPool()          # 쓰로틀링 시 Sonnet 체인 전환 (config.QUIZ_GEN_MODEL_CHAIN)
        self.gen_system, self.gen_user = load_pair("quiz_generate")
        self.rev_system, self.rev_user = load_pair("quiz_review")
        rep_system, self.rep_user = load_pair("quiz_repair")
        self.rep_system = Template(rep_system).substitute(
            max_avg_ratio=MAX_CORRECT_LEN_RATIO, max_longest_ratio=MAX_CORRECT_VS_LONGEST)
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
        kinds: list[str] = []
        for p in last.problems:
            k = _problem_kind(p, last.pattern)
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

    def repair_messages(self, slot: ManifestSlot, scenario_id: int, draft: QuizDraft,
                        issues: list[CodeIssue]) -> list:
        st = length_stats(draft)
        v = build_vars(slot, scenario_id, self.corpus)
        v.update(item_json=draft.model_dump_json(indent=2), answer=draft.answer,
                 correct_len=str(st["correct"]), avg_wrong_len=f"{st['avg_wrong']:.0f}",
                 max_wrong_len=str(st["max_wrong"]), problems="; ".join(m for _, m in issues))
        return [SystemMessage(self.rep_system), HumanMessage(self.rep_user.substitute(v))]

    # -- 호출
    def generate(self, messages) -> tuple[Optional[QuizDraft], Optional[str], str, str]:
        out, model = self.pool.invoke("draft", messages)
        parsed, err = out.get("parsed"), out.get("parsing_error")
        if parsed is None:
            return None, str(err or "no structured output returned"), _raw_output(out.get("raw")), model
        return parsed, None, parsed.model_dump_json(indent=2), model

    def review(self, messages) -> tuple[ReviewVerdict, str]:
        last_err = None
        for _ in range(2):          # 검토자 출력 형식 오류는 생성자 탓이 아니므로 검토만 다시 부른다
            out, model = self.pool.invoke("review", messages)
            if out.get("parsed") is not None:
                return out["parsed"], model
            last_err = out.get("parsing_error")
        raise RuntimeError(f"reviewer output unparsable: {last_err}")

    def repair(self, slot: ManifestSlot, scenario_id: int, draft: QuizDraft,
               issues: list[CodeIssue]) -> tuple[Optional[QuizDraft], str]:
        """길이 규칙만 실패한 문항의 보기만 고친다. stem·answer·source_excerpt는 원본 그대로 둔다."""
        out, _ = self.pool.invoke("repair", self.repair_messages(slot, scenario_id, draft, issues))
        parsed = out.get("parsed")
        if parsed is None:
            return None, f"repair output unparsable: {out.get('parsing_error')}"
        return merge_repair(draft, parsed)

    # -- 한 슬롯
    def run_slot(self, slot: ManifestSlot, avoid_stems: list[str] | None = None) -> SlotResult:
        avoid_stems = avoid_stems or []
        scenario_id = slot.scenario_id
        history: list[Attempt] = []
        patterns: list[str] = []
        last_item: Optional[QuizItem] = None
        scenario_note = ""
        log: list[str] = []
        repairs = repairs_ok = 0
        last_gen_model: Optional[str] = None     # last_item(검토 탈락 문항)을 만든 생성 모델
        last_rev_model: Optional[str] = None

        for n in range(1, MAX_ATTEMPTS + 1):
            msgs = self.generation_messages(slot, scenario_id, avoid_stems, history, scenario_note)
            draft, parse_err, item_json, gen_model = self.generate(msgs)

            if parse_err:
                att = Attempt(n, scenario_id, "parse", [f"[parse] {parse_err}"], item_json)
            else:
                issues = validate_draft(draft, slot, scenario_id, self.corpus)
                notes: list[str] = []
                # 길이 규칙만 실패 → 보기만 고치는 repair 1회 (attempts에 넣지 않음)
                if issues and all(p == "answer_too_long" for p, _ in issues):
                    repairs += 1
                    fixed, why = self.repair(slot, scenario_id, draft, issues)
                    if fixed is None:
                        notes.append(f"[code:answer_too_long] a length repair was attempted but failed ({why})")
                    else:
                        issues2 = validate_draft(fixed, slot, scenario_id, self.corpus)
                        if not issues2:
                            repairs_ok += 1
                            patterns.append("answer_too_long:repaired")
                            log.append(f"attempt {n}: answer_too_long → repaired")
                            draft, item_json, issues = fixed, fixed.model_dump_json(indent=2), []
                        else:
                            issues, item_json = issues2, fixed.model_dump_json(indent=2)
                            notes.append("[code:answer_too_long] this is the version after a length repair, "
                                         "which still failed the checks above")
                if issues:
                    probs = [f"[code:{p}] {m}" for p, m in issues] + notes
                    att = Attempt(n, scenario_id, primary_pattern(issues), probs, item_json)
                elif notes:
                    att = Attempt(n, scenario_id, "answer_too_long", notes, item_json)
                else:
                    verdict, rev_model = self.review(self.review_messages(slot, scenario_id, draft))
                    ok = verdict.valid and verdict.detected_pattern is None and not verdict.issues
                    if ok:
                        final = shuffle_options(draft, slot.quiz_id, slot.target_answer)
                        item = self._item(slot, scenario_id, final, "approved", n, patterns, gen_model)
                        log.append(f"attempt {n}: approved (SC-{scenario_id}, gen {short_model(gen_model)}, "
                                   f"review {short_model(rev_model)})")
                        return SlotResult(slot.quiz_id, "approved", n, scenario_id, patterns, [], item,
                                          repairs, repairs_ok, log, gen_model, rev_model)
                    pat = verdict.detected_pattern or "other"
                    issues_r = verdict.issues or ["(reviewer gave no detail)"]
                    att = Attempt(n, scenario_id, pat, [f"[review:{pat}] {i}" for i in issues_r], item_json)
                    last_item = self._item(slot, scenario_id, draft, "rejected", n, patterns + [pat], gen_model)
                    last_gen_model, last_rev_model = gen_model, rev_model

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
                          history[-1].problems, last_item, repairs, repairs_ok, log,
                          last_gen_model, last_rev_model)

    def _item(self, slot: ManifestSlot, scenario_id: int, draft: QuizDraft, status: str,
              attempts: int, patterns: list[str], generator_model: str) -> QuizItem:
        return QuizItem(
            quiz_id=slot.quiz_id, task_id=slot.task_id, domain=slot.domain,
            focus_bullet_ids=slot.focus_bullet_ids, scenario_id=scenario_id,
            stem=draft.stem, options=draft.options, answer=draft.answer,
            source_excerpt=draft.source_excerpt, status=status,
            review_retries=attempts - 1, detected_patterns=list(patterns),
            generator_model=generator_model, guide_version=config.GUIDE_VERSION, created_at=now_iso(),
        )
