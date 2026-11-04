"""프로젝트 설정의 유일한 진입점.

- 자격 증명은 상위 폴더(sds-ax-practice)의 .env 하나만 사용한다.
  다른 모듈은 load_dotenv()를 직접 부르지 말고 이 모듈을 import한다.
- 제출용 독립 저장소로 분리할 때는 ENV_PATH 한 줄만 바꾼다.
"""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]                 # mini_pjt/
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"    # sds-ax-practice/.env

if not ENV_PATH.is_file():
    raise SystemExit(f"[config] .env not found: {ENV_PATH}")
load_dotenv(ENV_PATH)

# ---- 모델 (CLAUDE.md 고정값) ----
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-east-1"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"

# ---- 경로 ----
DATA_DIR = ROOT / "data"
GUIDE_PDF = DATA_DIR / "raw" / "ccaf_exam_guide.pdf"
CHUNKS_PATH = DATA_DIR / "guide_chunks.jsonl"
BULLETS_PATH = DATA_DIR / "guide_bullets.jsonl"
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION = "ccaf_guide"

GUIDE_VERSION = "0.2"


def get_llm():
    """Bedrock Claude (temperature 0). 호출부에서 필요할 때만 생성."""
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(model=MODEL_ID, region_name=REGION, temperature=0)


def get_embeddings():
    """Titan v2 임베딩 (1024차원)."""
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=EMBED_MODEL_ID, region_name=REGION)


def get_text(message) -> str:
    """ChatBedrockConverse는 content를 블록 리스트로 주기도 하므로 텍스트만 모아 반환한다."""
    content = message.content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content
