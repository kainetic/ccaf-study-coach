"""FastAPI /query (DESIGN §7). Day 6 api_server.py 구조, 경로만 /query.

실행: uvicorn src.api:api --port 8000
요청: {"question": "...", "user_id": "demo_user"(선택), "thread_id": "..."(선택)}
응답: {"answer", "contexts": [{"doc_id", "text"}], "trace": [...], "thread_id"}
  - thread_id를 주지 않으면 새로 만들어 돌려준다. 같은 thread_id로 이어서 부르면 출제 → 채점이 이어진다.
GET /           웹 UI (src/web/index.html, v2-5)
GET /progress   ?user_id=... → get_progress 결과 그대로 (코드 계산, LLM 없음. 웹 UI 진도 패널용)
"""
import re
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src import config
from src.agent import build_app
from src.tracer import QueryTracer

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")     # Store namespace 규칙: 점 금지
WEB_INDEX = config.ROOT / "src" / "web" / "index.html"
UTF8_JSON = {"Content-Type": "application/json; charset=utf-8"}

coach = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global coach
    coach = await build_app()
    try:
        yield
    finally:
        await coach.close()


api = FastAPI(title="CCA-F 학습 코치", lifespan=lifespan)


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    user_id: str = "demo_user"
    thread_id: Optional[str] = None


async def run_query(question: str, user_id: str, thread_id: str) -> dict:
    tracer = QueryTracer()
    answer = await coach.run(question, user_id, thread_id, tracer)
    out = {"answer": answer, "contexts": tracer.contexts, "trace": tracer.summary(), "thread_id": thread_id}
    tracer.write({"question": question, "user_id": user_id, **out})
    return out


def check_user_id(user_id: str) -> None:
    if not USER_ID_RE.match(user_id):
        raise HTTPException(422, "user_id는 영문·숫자·_·-만 사용 (점 금지)")


@api.post("/query")
async def query(q: Query):
    check_user_id(q.user_id)
    out = await run_query(q.question, q.user_id, q.thread_id or str(uuid.uuid4()))
    return JSONResponse(content=out, headers=UTF8_JSON)


@api.get("/progress")
async def progress(user_id: str):
    check_user_id(user_id)
    return JSONResponse(content=await coach.bank.call("get_progress", {"user_id": user_id}), headers=UTF8_JSON)


@api.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_INDEX, media_type="text/html; charset=utf-8")
