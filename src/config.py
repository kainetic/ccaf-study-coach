"""프로젝트 설정의 유일한 진입점.

- 자격 증명은 저장소 폴더(프로젝트 루트)의 .env 하나만 사용한다 (.gitignore에 포함, 커밋 금지).
  다른 모듈은 load_dotenv()를 직접 부르지 말고 이 모듈을 import한다.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]                 # mini_pjt/
ENV_PATH = ROOT / ".env"                                   # 저장소 폴더/.env

if not ENV_PATH.is_file():
    raise SystemExit(f"[config] .env not found: {ENV_PATH}")
load_dotenv(ENV_PATH)

# ---- 모델 (CLAUDE.md 고정값) ----
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-east-1"

# 문제 풀 생성(오프라인 quiz_gen) 전용: 쓰로틀링 시 이 순서로 전환 (DESIGN §5-2). 런타임은 MODEL_ID 고정.
# Haiku는 문항·검토 품질 때문에 쓰지 않는다.
QUIZ_GEN_MODEL_CHAIN = [
    MODEL_ID,                                              # us  Sonnet 4.5 (기본)
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",    # global Sonnet 4.5
    "us.anthropic.claude-sonnet-4-6",                      # us  Sonnet 4.6
    "global.anthropic.claude-sonnet-4-6",                  # global Sonnet 4.6
]

# 런타임 에이전트: 기본은 MODEL_ID(us-east-1). 쓰로틀링·일일 할당량 소진 시에만 이 순서로 전환 (src/llm.py, DESIGN §5-2·§7).
# 같은 Sonnet 4.5를 다른 추론 프로필·리전으로 먼저 쓰고, 그다음 Sonnet 4.6. Haiku는 쓰지 않는다. (model_id, region)
_S45, _S46 = "anthropic.claude-sonnet-4-5-20250929-v1:0", "anthropic.claude-sonnet-4-6"
RUNTIME_MODEL_CHAIN = [
    (MODEL_ID, REGION),                         # 기본: us Sonnet 4.5 @ us-east-1
    (f"global.{_S45}", "us-east-1"),
    (f"us.{_S45}", "us-west-2"),
    (f"global.{_S45}", "us-west-2"),
    (f"global.{_S45}", "ap-northeast-2"),
    (f"us.{_S46}", "us-east-1"),
    (f"global.{_S46}", "us-east-1"),
    (f"us.{_S46}", "us-west-2"),
    (f"global.{_S46}", "us-west-2"),
    (f"global.{_S46}", "ap-northeast-2"),
]
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"

# ---- 경로 ----
DATA_DIR = ROOT / "data"
GUIDE_PDF = DATA_DIR / "raw" / "ccaf_exam_guide.pdf"
CHUNKS_PATH = DATA_DIR / "guide_chunks.jsonl"
BULLETS_PATH = DATA_DIR / "guide_bullets.jsonl"
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION = "ccaf_guide"
QUIZ_POOL_PATH = DATA_DIR / "quiz_pool.json"
# 풀이 기록. 테스트에서만 CCAF_STUDY_LOG로 다른 파일을 가리킬 수 있다.
STUDY_LOG_PATH = Path(os.environ.get("CCAF_STUDY_LOG", DATA_DIR / "study_log.json"))

# 장기 대화 요약 (src/memory.py, TS 5.1). 턴 시작 때 thread 메시지가 이 수를 넘으면 최근 턴만 남기고 앞부분을 요약.
# 스모크에서만 CCAF_SUMMARY_TRIGGER로 낮춘다.
SUMMARY_TRIGGER_MESSAGES = int(os.environ.get("CCAF_SUMMARY_TRIGGER", 40))
SUMMARY_KEEP_TURNS = 3

# 시험 도메인 비중 (가이드 v0.2). 출제 도메인 가중에 쓴다 (DESIGN §5-3).
DOMAIN_WEIGHTS = {1: 0.27, 2: 0.18, 3: 0.20, 4: 0.20, 5: 0.15}
DOMAIN_NAMES = {
    1: "Agentic Architecture & Orchestration",
    2: "Tool Design & MCP Integration",
    3: "Claude Code Configuration & Workflows",
    4: "Prompt Engineering & Structured Output",
    5: "Context Management & Reliability",
}
BULLET_TOTAL = 240          # 세부 항목 수 (ingest가 검증, 진도 커버리지 분모)

GUIDE_VERSION = "0.2"


def get_llm(model_id: str = MODEL_ID, max_retries: int | None = None, region: str = REGION):
    """Bedrock Claude (temperature 0) 단일 모델. 런타임 에이전트는 이 함수 대신 src.llm.get_runtime_llm()(전환 포함)을 쓴다.
    max_retries: botocore 내부 재시도 횟수. 모델 전환을 쓰는 곳은 1로 줄여 쓰로틀 시 전환이 바로 일어나게 한다."""
    from langchain_aws import ChatBedrockConverse

    kwargs = {}
    if max_retries is not None:
        from botocore.config import Config
        kwargs["config"] = Config(retries={"max_attempts": max_retries, "mode": "standard"})
    return ChatBedrockConverse(model=model_id, region_name=region, temperature=0, **kwargs)


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
