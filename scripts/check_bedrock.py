"""Bedrock 연결 확인: LLM 1회 + 임베딩 1회 호출.

실행: python scripts/check_bedrock.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402  (.env 로드)


def main() -> None:
    print(f"[env] {config.ENV_PATH}")
    print(f"[env] AWS_ACCESS_KEY_ID {'loaded' if os.getenv('AWS_ACCESS_KEY_ID') else 'MISSING'}")

    t = time.perf_counter()
    reply = config.get_text(config.get_llm().invoke("Reply with exactly: OK"))
    print(f"[llm] {config.MODEL_ID} -> {reply.strip()!r} ({time.perf_counter() - t:.1f}s)")

    t = time.perf_counter()
    vec = config.get_embeddings().embed_query("ping")
    print(f"[embed] {config.EMBED_MODEL_ID} -> dim {len(vec)} ({time.perf_counter() - t:.1f}s)")


if __name__ == "__main__":
    main()
