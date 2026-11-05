"""런타임 LLM: 쓰로틀링 시 모델·리전 전환 (DESIGN §7-1, 문제 풀 생성의 모델 전환 §5-2와 같은 방식).

기본은 config.MODEL_ID(global Sonnet 4.5 @ ap-northeast-2). 일시 오류(쓰로틀링·일일 토큰 할당량 소진 등)가 나면 그 후보에
쿨다운을 걸고 config.RUNTIME_MODEL_CHAIN의 다음 후보로 바로 넘어간다. 쿨다운 상태는 프로세스 안의 모든 에이전트가
공유한다. 일시 오류가 아닌 오류는 전환하지 않고 올린다 (모델 문제를 다른 모델로 감추지 않음).

FallbackChatModel은 BaseChatModel이라 create_agent·create_supervisor·with_structured_output에 그대로 넣을 수 있다.
bind_tools는 첫 후보(ChatBedrockConverse)로 도구를 Bedrock 형식으로 바꾼 kwargs를 묶고, 호출 때 같은 kwargs로
실제 후보의 _generate를 부른다 (Converse API 도구 형식은 모델 간 동일).
"""
import threading
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult
from pydantic import ConfigDict, PrivateAttr

from src import config

# 일시 오류: 재시도·모델 전환 대상 (문제 풀 생성 quiz_gen.ModelPool도 같은 판정을 쓴다)
TRANSIENT_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelNotReadyException", "InternalServerException", "ModelTimeoutException",
}
DAILY_COOLDOWN = 3600          # 일일 토큰 할당량 소진은 분 단위로 풀리지 않음
COOLDOWN_BASE, COOLDOWN_CAP = 4, 60
BOTOCORE_RETRIES = 1


def is_transient(e: BaseException, depth: int = 0) -> bool:
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
    return is_transient(e.__cause__ or e.__context__, depth + 1)


def is_unavailable(e: BaseException) -> bool:
    """이 계정·리전에서 쓸 수 없는 모델 (권한 없음, 모델 id 없음)."""
    try:
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError):
            return e.response.get("Error", {}).get("Code") in ("AccessDeniedException", "ResourceNotFoundException")
    except ImportError:
        pass
    return "AccessDeniedException" in str(e) or "ResourceNotFoundException" in str(e)


def label(model_id: str, region: str) -> str:
    """('global.anthropic.claude-sonnet-4-5-…', 'us-west-2') → 'global/sonnet-4-5@us-west-2'."""
    prefix, _, rest = model_id.partition(".")
    name = rest.replace("anthropic.claude-", "").split("-2025")[0]
    return f"{prefix}/{name}@{region}"


class _ChainState:
    """프로세스 공용 쿨다운."""
    lock = threading.Lock()
    cool_until: dict[tuple, float] = {}
    streak: dict[tuple, int] = {}
    disabled: set[tuple] = set()


class FallbackChatModel(BaseChatModel):
    chain: list[tuple[str, str]]
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _models: dict = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "bedrock-converse-fallback"

    def _model(self, key: tuple[str, str]):
        if key not in self._models:
            self._models[key] = config.get_llm(key[0], max_retries=BOTOCORE_RETRIES, region=key[1])
        return self._models[key]

    def bind_tools(self, tools, **kwargs):
        formatted = self._model(self.chain[0]).bind_tools(tools, **kwargs)
        return self.bind(**formatted.kwargs)

    def _order(self) -> list[tuple[str, str]]:
        now = time.monotonic()
        with _ChainState.lock:
            live = [k for k in self.chain if k not in _ChainState.disabled]
            ready = [k for k in live if _ChainState.cool_until.get(k, 0) <= now]
            cooling = sorted((k for k in live if k not in ready), key=lambda k: _ChainState.cool_until[k])
        return ready + cooling          # 모두 쿨다운이면 가장 빨리 풀리는 후보부터 그래도 시도

    def _generate(self, messages, stop: Optional[list[str]] = None, run_manager=None, **kwargs: Any) -> ChatResult:
        last: Optional[BaseException] = None
        for key in self._order():
            try:
                result = self._model(key)._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:   # noqa: BLE001
                last = e
                with _ChainState.lock:
                    if is_transient(e):
                        n = _ChainState.streak[key] = _ChainState.streak.get(key, 0) + 1
                        cool = DAILY_COOLDOWN if "per day" in str(e).lower() else min(COOLDOWN_CAP, COOLDOWN_BASE * 2 ** (n - 1))
                        _ChainState.cool_until[key] = time.monotonic() + cool
                        continue
                    if is_unavailable(e):
                        _ChainState.disabled.add(key)
                        continue
                raise
            with _ChainState.lock:
                _ChainState.streak[key] = 0
            for g in result.generations:
                g.message.response_metadata["served_by"] = label(*key)
            return result
        raise last if last else RuntimeError("런타임 모델 체인이 비어 있음")


def get_runtime_llm() -> FallbackChatModel:
    """런타임 에이전트·Supervisor·rewrite_query 공용 LLM (temperature 0, 전환 포함)."""
    return FallbackChatModel(chain=list(config.RUNTIME_MODEL_CHAIN))
