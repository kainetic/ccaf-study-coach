"""문제 은행 MCP 서버 클라이언트 (stdio 자식 프로세스, 앱 수명 동안 세션 1개 유지).

MultiServerMCPClient.get_tools()는 도구 호출마다 서버 프로세스를 새로 띄우므로, 세션을 열어 두고
session.call_tool()로 직접 호출한다. quiz_master의 도구 래퍼(src/agents/quiz_master.py)가 이 클라이언트를 쓴다.
"""
import json
import os
import sys
from contextlib import AsyncExitStack

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import AnyUrl

from src import config

SERVER = "quiz_bank"


class QuizBankClient:
    def __init__(self):
        self._stack: AsyncExitStack | None = None
        self.session = None

    async def start(self) -> "QuizBankClient":
        client = MultiServerMCPClient({SERVER: {
            "command": sys.executable,
            "args": ["-m", "src.quiz_bank_server"],
            "transport": "stdio",
            "cwd": str(config.ROOT),
            # CCAF_STUDY_LOG 등 테스트용 경로 전달. 서버 로그·경고는 줄인다 (stdio라 stderr는 부모 터미널로 나감)
            "env": {**os.environ, "FASTMCP_LOG_LEVEL": "WARNING", "PYTHONWARNINGS": "ignore"},
        }})
        self._stack = AsyncExitStack()
        self.session = await self._stack.enter_async_context(client.session(SERVER))
        return self

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack, self.session = None, None

    async def call(self, tool: str, args: dict) -> dict:
        """도구 결과(JSON 문자열)를 dict로. MCP 수준 오류도 구조화 에러 형식으로 바꾼다."""
        res = await self.session.call_tool(tool, {k: v for k, v in args.items() if v is not None})
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.isError:   # 도구 인자 스키마 검증 실패 등 (도구 본문까지 가지 못한 경우)
            category = "validation" if "validation error" in text else "mcp"
            return {"isError": True, "errorCategory": category, "isRetryable": False, "message": text[:500]}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"isError": True, "errorCategory": "internal", "isRetryable": False,
                    "message": f"도구 결과가 JSON이 아님: {text[:200]}"}

    async def catalog(self) -> dict:
        res = await self.session.read_resource(AnyUrl("quiz://catalog"))
        return json.loads(res.contents[0].text)
