"""Bedrock 연결 확인: LLM 1회 + 임베딩 1회 호출.

실행:
    python scripts/check_bedrock.py               # 기본 모델(config.MODEL_ID) + 임베딩
    python scripts/check_bedrock.py --quiz-chain  # 문제 풀 생성 체인(config.QUIZ_GEN_MODEL_CHAIN) 모델마다 1회씩
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402  (.env 로드)


def ping(model_id: str) -> bool:
    t = time.perf_counter()
    try:
        reply = config.get_text(config.get_llm(model_id).invoke("Reply with exactly: OK"))
        print(f"[llm] {model_id} -> {reply.strip()!r} ({time.perf_counter() - t:.1f}s)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[llm] {model_id} -> 실패 {type(e).__name__}: {str(e)[:160]}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiz-chain", action="store_true", help="문제 풀 생성 모델 체인 4개를 각각 확인")
    args = ap.parse_args()

    print(f"[env] {config.ENV_PATH}")
    print(f"[env] AWS_ACCESS_KEY_ID {'loaded' if os.getenv('AWS_ACCESS_KEY_ID') else 'MISSING'}")

    if args.quiz_chain:
        results = [ping(m) for m in config.QUIZ_GEN_MODEL_CHAIN]
        print(f"[chain] 사용 가능 {sum(results)}/{len(results)}")
        return 0 if all(results) else 1

    ok = ping(config.MODEL_ID)
    t = time.perf_counter()
    vec = config.get_embeddings().embed_query("ping")
    print(f"[embed] {config.EMBED_MODEL_ID} -> dim {len(vec)} ({time.perf_counter() - t:.1f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
