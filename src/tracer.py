"""/query 한 번의 trace·contexts 수집 (DESIGN §7). Day 7 local_tracer.py + run_eval.py ToolRecorder 방식.

Supervisor 구조에서는 하위 에이전트의 도구 결과가 최상위 messages에 올라오지 않으므로 콜백으로 모은다.
- route: transfer_to_<agent> 핸드오프 도구
- rewrite·retrieve: search_guide가 보내는 custom event
- get_quiz·submit: quiz_master 도구 래퍼가 보내는 custom event (정답은 담지 않음)
- guard·output_check: CoachApp.run(입력 가드, G4) + concept_tutor G3 custom event
- progress·allocate·plan: study_coach 도구 래퍼·planner custom event
- tools: 이번 턴에 실행된 앱 도구 이름 (핸드오프 transfer_*와 구조화 출력 도구 제외, 평가 판정용)
- llm: 호출 수·토큰 합계
"""
import json
import time
from pathlib import Path
from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackHandler

from src import config

TRACE_LOG = config.ROOT / "logs" / "trace.jsonl"
APP_TOOLS = {"search_guide", "get_chunk", "get_quiz", "submit_answer",
             "get_progress", "get_weak_domains", "allocate_study_time", "make_study_plan"}
PASSTHROUGH_EVENTS = ("progress", "allocate", "plan")


class QueryTracer(AsyncCallbackHandler):
    def __init__(self):
        self.t0 = time.perf_counter()
        self.trace: list[dict] = []
        self.contexts: list[dict] = []
        self.tools: list[str] = []
        self.llm_calls = 0
        self.tokens = {"input": 0, "output": 0}
        self.models: dict[str, int] = {}          # 실제로 응답한 모델 (런타임 전환 확인용)
        self._starts: dict[Any, float] = {}

    def _t(self) -> float:
        return round(time.perf_counter() - self.t0, 2)

    def _add_context(self, doc_id: str, text: str) -> None:
        if not any(c["doc_id"] == doc_id for c in self.contexts):
            self.contexts.append({"doc_id": doc_id, "text": text})

    async def on_tool_start(self, serialized, input_str, *, run_id, **kw):
        name = (serialized or {}).get("name", "")
        self._starts[run_id] = time.perf_counter()
        if name.startswith("transfer_to_"):
            self.trace.append({"step": "route", "agent": name[len("transfer_to_"):], "t": self._t()})
        elif name in APP_TOOLS:
            self.tools.append(name)

    def add_step(self, step: dict) -> None:
        self.trace.append({**step, "t": self._t()})

    async def on_tool_end(self, output, *, run_id, **kw):
        self._starts.pop(run_id, None)

    async def on_custom_event(self, name: str, data: Any, *, run_id, **kw):
        if name == "rewrite":
            self.trace.append({"step": "rewrite", "query_en": data["query_en"], "t": self._t()})
        elif name == "retrieve":
            for h in data["hits"]:
                self._add_context(h["doc_id"], h["text"])
            self.trace.append({"step": "retrieve", "doc_ids": [h["doc_id"] for h in data["hits"]],
                               "how": [h["how"] for h in data["hits"]], "t": self._t()})
        elif name == "get_quiz":
            ctx = data.pop("context", None)
            if ctx:
                self._add_context(ctx["doc_id"], ctx["text"])
            self.trace.append({"step": "get_quiz", **data, "t": self._t()})
        elif name == "submit":
            self.trace.append({"step": "submit", **data, "t": self._t()})
        elif name == "output_check":
            self.trace.append({"step": "output_check", **data, "t": self._t()})
        elif name in PASSTHROUGH_EVENTS:
            self.trace.append({"step": name, **data, "t": self._t()})

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self.llm_calls += 1

    async def on_llm_end(self, response, *, run_id, **kw):
        try:
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
            served = msg.response_metadata.get("served_by")
        except (IndexError, AttributeError):
            usage, served = {}, None
        if served:
            self.models[served] = self.models.get(served, 0) + 1
        self.tokens["input"] += usage.get("input_tokens", 0)
        self.tokens["output"] += usage.get("output_tokens", 0)

    def summary(self) -> list[dict]:
        return self.trace + [{"step": "tools", "names": self.tools},
                             {"step": "llm", "calls": self.llm_calls, **self.tokens, "models": self.models, "t": self._t()}]

    def write(self, record: dict, path: Optional[Path] = TRACE_LOG) -> None:
        """로컬 확인용 JSONL (logs/는 커밋하지 않음)."""
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **record}, ensure_ascii=False) + "\n")
