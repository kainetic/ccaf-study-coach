"""concept_tutor 도구 (DESIGN §3-1): search_guide, get_chunk."""
import asyncio
from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.tools import tool

from src import guards, retriever

DocTypeArg = Literal["overview", "domain", "scenario", "task_statement", "sample_question", "exercise", "scope"]


@tool
async def search_guide(query: str, domain: Optional[int] = None, doc_type: Optional[DocTypeArg] = None) -> str:
    """CCA-F 공식 시험 가이드(영문, v0.2)에서 개념 질문의 근거 청크를 찾는다. 결과마다 [doc_id] 제목과 원문이 붙는다.

    언제 쓰나: 시험 개념·용어 설명, 샘플 문항(SQ-01~12) 해설, 시험 형식·도메인 비중, 시험 범위(in/out of scope) 확인.
    언제 쓰지 않나: 연습 문제 출제·채점(quiz_master 담당), 인사·잡담.

    입력:
      query: 사용자 질문 원문(한국어 그대로). 내부에서 영어 검색 질의로 바꾸고, "TS 2.2", "도메인 3", "샘플 1번"처럼
             번호가 있으면 그 청크를 먼저 정확 조회한다.
      domain: 1~5 (질문이 특정 도메인으로 한정될 때만)
      doc_type: overview | domain | scenario | task_statement | sample_question | exercise | scope
                (예: 샘플 문항 해설은 sample_question, 범위 질문은 scope)

    예: search_guide("agentic loop에서 stop_reason으로 종료 판단") / search_guide("샘플 1번 정답 이유", doc_type="sample_question")
    """
    await guards.wait_input_ok()
    query_en, hits = await asyncio.to_thread(retriever.search, query, domain, doc_type)
    hits = retriever.trim_hits(hits, query_en)     # 2순위 이하 TS 청크 중 검색어와 무관한 것은 뺀다 (TS 5.1)
    await adispatch_custom_event("rewrite", {"query": query, "query_en": query_en})
    await adispatch_custom_event("retrieve", {"hits": [{"doc_id": h.doc_id, "how": h.how, "text": h.text} for h in hits]})
    if not hits:
        return "검색 결과 없음 — 가이드에서 근거를 찾지 못했다고 답하라 (지어내지 말 것)."
    return "\n\n---\n\n".join(f"[{h.doc_id}] {h.title}\n{h.text}" for h in hits)


@tool
async def get_chunk(doc_id: str) -> str:
    """가이드 청크 하나를 doc_id로 정확히 가져온다 (전문). 결과 형식은 search_guide와 같다: [doc_id] 제목 + 원문.

    언제 쓰나: 질문이 특정 문서 번호를 가리킬 때 — TS 번호, 샘플 문항 번호, 도메인, 시나리오, Exercise, 시험 개요·범위.
      search_guide 결과에 나온 doc_id의 전문을 다시 확인할 때도 쓴다.
    언제 쓰지 않나: 번호 없이 개념으로 묻는 질문(search_guide 사용), 연습 문제 출제·채점.

    입력 doc_id 형식: TS-1.4 | SQ-01 (샘플 문항 1~12) | D3 (도메인 개요) | SC-5 (시나리오) | EX-2 (Exercise)
      | OV-details (시험 형식·점수·도메인 비중) | OV-candidate (대상 응시자) | SCOPE-in | SCOPE-out | SCOPE-tech
      ("TS 1.4", "샘플 1번"처럼 써도 정규화한다)

    예: get_chunk("SQ-01") / get_chunk("OV-details") / get_chunk("TS-1.7")
    """
    await guards.wait_input_ok()
    did = retriever.normalize_doc_id(doc_id)
    if did is None:
        return (f"알 수 없는 doc_id: {doc_id!r}. 형식: TS-1.4, SQ-01, D3, SC-5, EX-2, OV-details, OV-candidate, "
                "SCOPE-in, SCOPE-out, SCOPE-tech. 번호를 모르면 search_guide를 쓴다.")
    c = retriever.load_chunks()[did]
    await adispatch_custom_event("retrieve", {"hits": [{"doc_id": c.doc_id, "how": "get_chunk", "text": c.text}]})
    return f"[{c.doc_id}] {c.title}\n{c.text}"
