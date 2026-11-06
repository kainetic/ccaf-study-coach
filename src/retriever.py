"""가이드 검색: TS 번호 등 정확 조회 + rewrite_query(한→영) + BM25·벡터 하이브리드 (DESIGN §2-5, §5-1).

Day 2 hybrid.py(BM25 + 벡터 EnsembleRetriever), extra_multiquery.py(LLM 질의 재작성) 재사용.
가이드는 영어, 질문은 한국어라 BM25 전에 영어 키워드 질의로 바꾼다. 토크나이저도 영어용.
"""
import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from src import config
from src.schemas import GuideChunk

K = 4
WEIGHTS = (0.4, 0.6)                 # (BM25, 벡터)

EN_STOP = set("""a an the of to in on for and or is are be by with as at from that this it its what which how when
why does do can should into than then there their about between versus vs""".split())

REWRITE_PROMPT = """You turn a learner's question about the Claude Certified Architect – Foundations exam guide
into one concise English search query for that (English) guide.

- Keep technical terms as they appear in Claude/Anthropic docs (e.g. stop_reason, tool_choice, .mcp.json, CLAUDE.md,
  Message Batches API, fork_session, hooks, subagent, prompt caching).
- Translate Korean concepts into the English terms the guide would use. Add 1-3 close synonyms if helpful.
- Output only the query on one line, no quotes or explanation.

Question: {question}"""

# 정확 조회 패턴: 질문에 문서 번호가 있으면 그 청크를 먼저 쓴다. get_chunk(doc_id)는 모델이 번호를 알 때 쓰는
# 별도 도구이고, search_guide의 정확 조회 선행과 함께 둔다 (DESIGN §3-1)
EXACT_PATTERNS = [
    (re.compile(r"(?:\bTS|task\s*statement)\s*-?\s*([1-5])\s*\.\s*([1-7])", re.I), lambda m: f"TS-{m[1]}.{m[2]}"),
    (re.compile(r"(?:도메인|domain)\s*-?\s*([1-5])(?!\s*\.\s*\d)", re.I), lambda m: f"D{m[1]}"),
    (re.compile(r"\bD([1-5])\b"), lambda m: f"D{m[1]}"),
    (re.compile(r"(?:샘플|sample\s*question|sample|\bSQ)\s*-?\s*(\d{1,2})\s*번?", re.I), lambda m: f"SQ-{int(m[1]):02d}"),
    (re.compile(r"(?:시나리오|scenario|\bSC)\s*-?\s*([1-6])\b", re.I), lambda m: f"SC-{m[1]}"),
    (re.compile(r"(?:exercise|\bEX)\s*-?\s*([1-4])\b", re.I), lambda m: f"EX-{m[1]}"),
]


def en_tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9_]+(?:[.\-/][a-z0-9_]+)*", text.lower())
    return [t for t in toks if t not in EN_STOP and len(t) > 1]


@dataclass
class Hit:
    doc_id: str
    title: str
    doc_type: str
    text: str
    how: str                         # "exact" | "hybrid"


# ---------------------------------------------------------------- 결과 트리밍 (TS 5.1)

# 겹침 계산에서 빼는 일반어 (가이드 전반에 흔해 관련성 신호가 아님)
TRIM_GENERIC = set("""claude model models use using used agent agents task tasks system systems design implement
implementing including such based each into when where while other more""".split())
BULLET = re.compile(r"^\s*-\s+")


def _keywords(text: str) -> set[str]:
    return {t for t in en_tokenize(text) if t not in TRIM_GENERIC}


def trim_hits(hits: list[Hit], query_en: str) -> list[Hit]:
    """청크 단위 필터 (TS 5.1). 1순위 hit, 정확 조회 hit, task_statement가 아닌 청크(샘플 문항·범위·개요 등)는 그대로.
    2순위 이하 task_statement 청크는 영어 검색어와 키워드(일반어 제외)가 겹치는 bullet이 하나라도 있으면 전문을 두고,
    하나도 없으면 뺀다. bullet 단위로 자르면 관련 있는 bullet까지 잘려(평가 8번: "reducing irrelevant context and
    token usage") 답변이 빠져서, 청크 안은 자르지 않는다. LLM 없이 결정적."""
    q = _keywords(query_en)
    out: list[Hit] = []
    for i, h in enumerate(hits):
        if i == 0 or h.how == "exact" or h.doc_type != "task_statement" or not q:
            out.append(h)
        elif any(_keywords(line) & q for line in h.text.splitlines() if BULLET.match(line)):
            out.append(h)
    return out


@lru_cache(maxsize=1)
def load_chunks() -> dict[str, GuideChunk]:
    if not config.CHUNKS_PATH.is_file():
        raise FileNotFoundError(f"{config.CHUNKS_PATH} 없음 — 먼저 python -m src.ingest")
    rows = [GuideChunk.model_validate_json(l) for l in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {c.doc_id: c for c in rows}


def exact_ids(question: str) -> list[str]:
    chunks, out = load_chunks(), []
    for pat, make in EXACT_PATTERNS:
        for m in pat.finditer(question):
            did = make(m)
            if did in chunks and did not in out:
                out.append(did)
    return out


def normalize_doc_id(raw: str) -> str | None:
    """get_chunk 입력 정규화: 'TS-1.4'·'ts 1.4'·'SQ-1'·'샘플 1번'·'D3'·'EX-2'·'scope-out' → 실제 doc_id. 없으면 None."""
    chunks = load_chunks()
    s = raw.strip()
    by_lower = {k.lower(): k for k in chunks}
    if s.lower() in by_lower:
        return by_lower[s.lower()]
    m = re.fullmatch(r"([1-5])\s*\.\s*([1-7])", s)
    if m:
        s = f"TS-{m[1]}.{m[2]}"
    ids = exact_ids(s)
    return ids[0] if ids else None


@lru_cache(maxsize=1)
def _vectorstore():
    from langchain_chroma import Chroma
    return Chroma(collection_name=config.COLLECTION, embedding_function=config.get_embeddings(),
                  persist_directory=str(config.CHROMA_DIR))


@lru_cache(maxsize=1)
def _rewriter():
    from src.llm import get_runtime_llm
    return get_runtime_llm()


def rewrite_query(question: str, callbacks: Optional[list] = None) -> str:
    """한국어 질문 → 영어 검색 질의 (LLM 1회). 실패하면 원문을 그대로 쓴다."""
    try:
        cfg = {"callbacks": callbacks} if callbacks else None
        q = config.get_text(_rewriter().invoke(REWRITE_PROMPT.format(question=question), cfg)).strip()
        return q.splitlines()[0].strip().strip('"') if q else question
    except Exception:   # noqa: BLE001 — 검색은 원문으로라도 계속한다
        return question


def _filter_ok(c: GuideChunk, domain: Optional[int], doc_type: Optional[str]) -> bool:
    return (domain is None or c.domain == domain) and (doc_type is None or c.doc_type == doc_type)


@lru_cache(maxsize=32)
def _bm25(domain: Optional[int], doc_type: Optional[str]):
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    docs = [Document(page_content=f"{c.title}\n{c.text}", metadata={"doc_id": c.doc_id})
            for c in load_chunks().values() if _filter_ok(c, domain, doc_type)]
    if not docs:
        return None
    r = BM25Retriever.from_documents(docs, preprocess_func=en_tokenize)
    r.k = K
    return r


def hybrid_ids(query_en: str, domain: Optional[int] = None, doc_type: Optional[str] = None, k: int = K) -> list[str]:
    """BM25 + 벡터를 RRF로 합친 doc_id 순위 (Day 2 EnsembleRetriever)."""
    from langchain_classic.retrievers import EnsembleRetriever
    bm25 = _bm25(domain, doc_type)
    if bm25 is None:
        return []
    conds = [{"domain": domain}] if domain is not None else []
    conds += [{"doc_type": doc_type}] if doc_type else []
    flt = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)
    vector = _vectorstore().as_retriever(search_kwargs={"k": K, **({"filter": flt} if flt else {})})
    ens = EnsembleRetriever(retrievers=[bm25, vector], weights=list(WEIGHTS))
    out: list[str] = []
    for d in ens.invoke(query_en):
        did = d.metadata.get("doc_id")
        if did and did not in out:
            out.append(did)
    return out[:k]


def search(question: str, domain: Optional[int] = None, doc_type: Optional[str] = None, k: int = K,
           query_en: Optional[str] = None, hybrid: Optional[list[str]] = None) -> tuple[str, list[Hit]]:
    """반환: (영어 재작성 질의, 결과). 정확 조회 청크를 앞에 두고 남는 자리를 하이브리드 결과로 채운다.
    query_en·hybrid를 주면(선행 검색 결과) 재작성·하이브리드 검색을 다시 하지 않는다."""
    chunks = load_chunks()
    exact = [d for d in exact_ids(question) if _filter_ok(chunks[d], domain, doc_type)]
    query_en = query_en or rewrite_query(question)
    hits = [Hit(d, chunks[d].title, chunks[d].doc_type, chunks[d].text, "exact") for d in exact[:k]]
    if len(hits) < k:
        for d in (hybrid if hybrid is not None else hybrid_ids(query_en, domain, doc_type, k=k)):
            if d not in exact:
                hits.append(Hit(d, chunks[d].title, chunks[d].doc_type, chunks[d].text, "hybrid"))
            if len(hits) >= k:
                break
    return query_en, hits


# ---------------------------------------------------------------- 선행 검색 (v2-7)

@dataclass
class Prefetch:
    """턴 시작 때 질문 원문으로 미리 돌리는 재작성·하이브리드 검색 (입력 가드 LLM·Supervisor 배분과 동시에).
    결과는 search_guide가 wait_input_ok() 뒤에만 꺼내 쓰므로 차단된 입력의 검색 결과는 사용자·LLM에 나가지 않는다."""
    question: str
    task: asyncio.Task            # → (query_en, 필터 없는 hybrid doc_id 순위)


# CoachApp.run이 넣고 그래프 task가 복사해 간다 (guards.INPUT_GATE와 같은 방식)
PREFETCH: ContextVar[Optional[Prefetch]] = ContextVar("search_prefetch", default=None)


def start_prefetch(question: str, callbacks: Optional[list] = None) -> Prefetch:
    async def run() -> tuple[str, list[str]]:
        query_en = await asyncio.to_thread(rewrite_query, question, callbacks)
        return query_en, await asyncio.to_thread(hybrid_ids, query_en)
    task = asyncio.create_task(run())
    # 쓰지 않고 끝난 선행 검색의 예외가 "never retrieved" 경고로 남지 않게 꺼내 둔다 (search_guide는 실패 시 일반 경로)
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return Prefetch(question.strip(), task)


async def prefetched(query: str, domain: Optional[int], doc_type: Optional[str]) -> tuple[Optional[str], Optional[list[str]], str]:
    """search_guide용: 이번 턴 질문과 같은 query면 선행 결과를 돌려준다. 반환 (query_en, hybrid, "hit"|"query_only"|"miss").
    필터(domain·doc_type)가 있으면 재작성 질의만 재사용하고 하이브리드는 필터를 걸어 다시 한다."""
    pre = PREFETCH.get()
    if pre is None or query.strip() != pre.question:
        return None, None, "miss"
    try:
        query_en, ids = await asyncio.shield(pre.task)
    except Exception:   # noqa: BLE001 — 선행 검색 실패는 일반 경로로
        return None, None, "miss"
    if domain is None and doc_type is None:
        return query_en, ids, "hit"
    return query_en, None, "query_only"


def warm_up() -> None:
    """서버 시작 예열: 청크·BM25·Chroma를 열고 임베딩 1회·재작성 LLM 1회로 Bedrock 연결을 만들어 둔다.
    첫 요청의 지연(콜드 스타트)을 없앤다. 실패해도 서버 시작은 계속한다."""
    load_chunks()
    _bm25(None, None)
    hybrid_ids("agentic loop stop_reason")
    rewrite_query("에이전트 루프 종료 조건")
