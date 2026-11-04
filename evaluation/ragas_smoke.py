"""RAGAS 3건 스모크 (DESIGN §1-1 S2, §9-2): ragas_reference.csv의 RAGAS 대상 앞 3건을 /query로 실행해 채점.

실행: python evaluation/ragas_smoke.py [--n 3]
- /query는 in-process(FastAPI TestClient)로 부른다. 문항마다 새 thread.
- 지표: faithfulness, context_recall, context_precision(with reference), answer_relevancy
- 평가 LLM은 런타임과 같은 전환 체인(src/llm.py), 임베딩은 Titan v2
- 결과는 evaluation/ragas_smoke.json에 점수·doc_id만 저장 (가이드 원문·답변 본문은 저장하지 않음, DESIGN §2-4)
- 목표치(faithfulness ≥ 0.85, context_recall ≥ 0.80)는 round2 기준. 여기서는 기준선 기록만.
"""
import argparse
import csv
import json
import math
import sys
import types
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402

OUT_PATH = config.ROOT / "evaluation" / "ragas_smoke.json"
USER = "ragas_smoke"


def _ragas_compat_shim() -> None:
    """ragas 0.4.3은 langchain-community 0.4에서 사라진 chat_models.vertexai를 import한다 (isinstance 목록용).
    venv를 건드리지 않고 이 스크립트 안에서만 빈 자리표시 클래스를 끼워 넣는다."""
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            mod = types.ModuleType(name)
            mod.ChatVertexAI = type("ChatVertexAI", (), {})
            sys.modules[name] = mod


def load_cases(n: int) -> list[dict]:
    ev = config.ROOT / "evaluation"
    queries = {r["id"]: r for r in csv.DictReader((ev / "test_queries.csv").open(encoding="utf-8-sig"))}
    refs = [r for r in csv.DictReader((ev / "ragas_reference.csv").open(encoding="utf-8-sig")) if r["ragas"].strip().upper() == "Y"]
    return [{"id": r["id"], "question": queries[r["id"]]["input"], "reference": r["reference"],
             "reference_doc_ids": r["reference_doc_ids"].split(";")} for r in refs[:n]]


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=3, help="평가할 문항 수 (기본 3)")
    args = ap.parse_args()

    cases = load_cases(args.n)
    from fastapi.testclient import TestClient
    import src.api as api_mod
    with TestClient(api_mod.api) as client:
        for c in cases:
            r = client.post("/query", json={"question": c["question"], "user_id": USER,
                                            "thread_id": f"ragas-{uuid.uuid4().hex[:8]}"}, timeout=300)
            r.raise_for_status()
            out = r.json()
            c["response"], c["contexts"] = out["answer"], out["contexts"]
            print(f"[query] #{c['id']} contexts {[x['doc_id'] for x in out['contexts']]} · 답변 {len(out['answer'])}자")

    _ragas_compat_shim()
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    from src.llm import get_runtime_llm

    ds = EvaluationDataset.from_list([{
        "user_input": c["question"], "response": c["response"],
        "retrieved_contexts": [x["text"] for x in c["contexts"]] or [""], "reference": c["reference"],
    } for c in cases])
    metrics = [Faithfulness(), LLMContextRecall(), LLMContextPrecisionWithReference(), ResponseRelevancy()]
    result = evaluate(ds, metrics=metrics, llm=LangchainLLMWrapper(get_runtime_llm()),
                      embeddings=LangchainEmbeddingsWrapper(config.get_embeddings()),
                      run_config=RunConfig(max_workers=2, timeout=240), show_progress=False)
    df = result.to_pandas()
    cols = [c for c in df.columns if c in ("faithfulness", "context_recall", "llm_context_precision_with_reference",
                                           "answer_relevancy")]
    rows = []
    print("\n#   " + "  ".join(f"{c[:22]:>22}" for c in cols))
    for c, (_, rec) in zip(cases, df.iterrows()):
        scores = {k: (None if rec[k] is None or (isinstance(rec[k], float) and math.isnan(rec[k])) else round(float(rec[k]), 3))
                  for k in cols}
        hit = sorted(set(c["reference_doc_ids"]) & {x["doc_id"] for x in c["contexts"]})
        rows.append({"id": c["id"], "reference_doc_ids": c["reference_doc_ids"],
                     "retrieved_doc_ids": [x["doc_id"] for x in c["contexts"]], "reference_hit": hit, **scores})
        print(f"{c['id']:<3} " + "  ".join(f"{'-' if scores[k] is None else scores[k]:>22}" for k in cols))
    means = {k: round(sum(r[k] for r in rows if r[k] is not None) / max(1, sum(r[k] is not None for r in rows)), 3) for k in cols}
    print("avg " + "  ".join(f"{means[k]:>22}" for k in cols))
    OUT_PATH.write_text(json.dumps({"n": len(rows), "means": means, "rows": rows,
                                    "note": "S2 RAGAS 스모크 기준선. 목표치는 round2 (SERVICE.md §5)"},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ragas] {OUT_PATH.relative_to(config.ROOT).as_posix()} 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
