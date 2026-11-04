# CCA-F 학습 코치 (mini PJT)

CCA-F 시험 가이드 기반 멀티에이전트 학습 도우미. LangGraph Supervisor + Bedrock Claude.
기획: SERVICE.md · 설계: docs/DESIGN.md · 평가: evaluation/test_queries.csv
설계 세부는 필요할 때 docs/DESIGN.md의 해당 절(§)을 읽을 것. 전체를 미리 읽지 말 것.

## 현재 단계: S2 완료 → S3(=P1) 시작 가능 · S1 후반 병행 (DESIGN §1-1 단계표)
- 완료: S0(ingest 62/240) · S1 전반(문제 풀 승인 266/284 슬롯(93.7%), 세부 항목 커버리지 240/240,
  정답이 가장 긴 보기 54% → 33.8%(목표 35% 이하 달성, --rebalance-length 성공 55·원본 유지 20),
  5% 초과 비율 28% → 7.9%(참고). 수치·예외 기록은 evaluation/quiz_pool_report.md)
  · S1 후반 사람 표본 검토 38/38 OK(오류 0, 경미 0) — 재검토 시 review_sample.xlsx 기입 → review_sample.py --apply
- S1 후반 남은 작업 (S2와 병행 가능):
  - scripts/seed_study_log.py (더미 사용자 demo_heavy·demo_new)
- S2(=P0) 완료: search_guide, get_quiz(MCP), submit_answer(MCP) + Supervisor + /query
  - evaluation/smoke_p0.py 18/18 PASS (개념 질문 → 풀 출제 → 채점, study_log.json 기록, 정답 비노출)
  - RAGAS 3건 기준선 (evaluation/ragas_smoke.json): faithfulness 0.896 · recall 1.0 · precision 0.917 · relevancy 0.681
- 다음: S3(=P1) — 가드레일 G1~G7, get_chunk, get_progress·get_weak_domains, study_coach, run_eval.py
- 선택 항목(v2)은 요청 전까지 시작하지 않는다
- S4(round1)는 S3 완료 즉시, 개선 작업보다 먼저 실행한다

## 명령 (Windows PowerShell)
..\.venv\Scripts\activate; pip install -r requirements.txt   # 상위 venv 공용 (버전 동일 고정, 독립 저장소 분리 시 새 venv)
python scripts/check_bedrock.py                      # Bedrock LLM·임베딩 연결 확인
python scripts/check_bedrock.py --quiz-chain         # 문제 풀 생성 모델 체인(QUIZ_GEN_MODEL_CHAIN) 4개 각각 접근 확인
python -m src.ingest --dry-run                       # 파싱·62/240 검증만
python -m src.ingest                                 # 가이드 PDF → guide_chunks.jsonl(62) + guide_bullets.jsonl(240) → Chroma
python scripts/build_quiz_pool.py --pilot 10         # 파일럿 풀
python scripts/build_quiz_pool.py --resume           # 전체 풀 270문제 (매니페스트 기준 이어서, 기본 슬롯 240개 먼저)
python scripts/build_quiz_pool.py --fill-gaps        # 승인 문제가 없는 세부 항목만 보충
python scripts/build_quiz_pool.py --rebalance-length # 정답이 가장 긴 오답보다 5% 넘게 긴 승인 문항의 보기 재조정
python scripts/build_quiz_pool.py --exception-slot TS-5.2-K2:1   # 미커버 항목 시나리오 고정 예외 슬롯
python scripts/build_quiz_pool.py --reset-stale      # 이전 prompt_version으로 처리된 슬롯을 pending으로 (LLM 호출 없음)
python scripts/build_quiz_pool.py --resume --concurrency 1   # 동시 실행 수 (기본 2, 쓰로틀링 심하면 1)
python scripts/review_sample.py                      # 사람 표본 검토 xlsx + 결과 CSV (층화, repair 문항 층마다 1개 이상)
python scripts/review_sample.py --apply             # 검토 반영: human_checked, 오류 → needs_fix, 리포트 갱신 (--dry-run 가능)
python scripts/pool_report.py                        # evaluation/quiz_pool_report.md 자동 절 갱신
uvicorn src.api:api --port 8000                      # API 서버
python evaluation/smoke_p0.py                        # P0 한 바퀴 검증 (--url 실행 중 서버, --keep 기록 남김)
python evaluation/ragas_smoke.py                     # RAGAS 3건 스모크 → evaluation/ragas_smoke.json
python evaluation/run_eval.py                        # 20건 평가 (P1부터)
MCP 서버(src/quiz_bank_server.py)는 단독 실행하지 않음. 클라이언트가 sys.executable로 stdio 자식 프로세스로 띄움.

## 고정값
- LLM: ChatBedrockConverse, model "us.anthropic.claude-sonnet-4-5-20250929-v1:0", region "us-east-1", temperature 0
  - 예외: 쓰로틀링·일일 할당량 소진 등 일시 오류일 때만 모델 전환 (Haiku 미사용)
    - 문제 풀 생성(오프라인 quiz_gen): config.QUIZ_GEN_MODEL_CHAIN (Sonnet 4.5/4.6 × us/global, DESIGN §5-2)
    - 런타임 에이전트: src/llm.py FallbackChatModel, config.RUNTIME_MODEL_CHAIN (Sonnet 4.5 다른 프로필·리전
      → Sonnet 4.6, us-east-1·us-west-2·ap-northeast-2, DESIGN §7-1). LLM은 config.get_llm()이 아니라 get_runtime_llm()
- 임베딩: BedrockEmbeddings "amazon.titan-embed-text-v2:0"
- Chroma: persist ./chroma_db, collection "ccaf_guide"
- 자격 증명: 상위 폴더의 ..\.env (sds-ax-practice\.env)를 사용. mini_pjt 안에 .env를 만들거나 복사하지 않음.
  src/config.py 한 곳에서만 load_dotenv(Path(__file__).resolve().parents[2] / ".env")로 로드하고,
  다른 모듈·스크립트는 src.config를 import해서 씀 (모듈마다 load_dotenv() 호출 금지). 코드·문서에 키를 쓰지 않음.
  .env 파일이 없으면 시작 시 경로를 포함한 오류로 즉시 종료 (제출용 독립 저장소로 분리할 때 이 경로만 바꾸면 됨)
- 가이드 PDF 위치: data/raw/ccaf_exam_guide.pdf (커밋 금지)

## 반드시 지킬 규칙
- 정답은 LLM 컨텍스트와 그래프 State에 넣지 않는다. get_quiz는 QuizPublic만 반환하고,
  채점은 submit_answer가 코드로 한다. State에는 pending_quiz_id만 둔다
- get_quiz가 no_match면 문제를 지어내지 않는다. alternatives로 대체 제안만 한다
- 승인(approved) 상태가 아닌 문제는 출제하지 않는다
- 런타임에 문제를 생성하지 않는다. quiz_gen은 scripts/build_quiz_pool.py에서만 호출하고 에이전트 도구로 노출하지 않는다
- 세부 항목 수가 240개가 아니면 ingest 파싱 오류로 보고 멈춘다
- 선택 항목(v2)은 요청 전까지 만들지 않는다: reset_progress HITL, 요약 미들웨어, 웹 UI, Dockerfile
- 커밋 금지: data/raw/*.pdf, data/guide_chunks.jsonl, data/guide_bullets.jsonl, chroma_db/, .env, evaluation/review_sample.xlsx
  (data/quiz_pool.json, quiz_pool_manifest.json, study_log.json은 커밋)
- evaluation/test_queries.csv와 ragas_reference.csv의 기대값은 묻지 않고 바꾸지 않는다
- 설계와 다르게 구현해야 하면 먼저 말하고, 합의되면 docs/DESIGN.md를 같이 고친다

## 코드 규칙
- 데이터 구조는 src/schemas.py의 Pydantic 모델만 사용 (DESIGN §4)
- 도구 docstring에 "언제 쓰고 언제 쓰지 않는지, 입력 형식, 예시"를 적는다
- MCP 도구 에러는 {"isError", "errorCategory", "isRetryable", "message"} 형식.
  조건에 맞는 결과가 없는 것은 에러가 아니라 status "no_match"
- 계산(시간 배분, 정답률, 약점 점수)은 LLM이 아니라 코드로 한다
- 답변은 한국어, 핵심 용어는 영어 병기, 개념 답변 끝에 [근거: TS-x.y] 표기

## 실습 코드 재사용 (원본: 상위 폴더 ..\dayN_practice)
- day2_practice/hybrid.py, extra_multiquery.py → src/retriever.py
- day4_practice/mcp_server.py → src/quiz_bank_server.py 골격
- day5_practice guards_input.py, guards_refusal.py → src/guards.py (P1)
- day6_practice/final_scenario.py → src/agent.py (build_app 구조)
- day6_practice/api_server.py → src/api.py (경로 /query로 변경)
- day7_practice/local_tracer.py → src/tracer.py
- day7_practice/run_eval.py, llm_judge.py → evaluation/ (P1)
- day7_practice/plan_and_execute.py → src/planner.py (P1)

## 알려진 함정
- ChatBedrockConverse의 content가 블록 리스트로 올 수 있음 → get_text() 헬퍼로 텍스트 추출
- Supervisor 구조에서는 하위 에이전트 도구 결과가 최상위 messages에 안 올라옴
  → contexts·trace는 콜백(ToolRecorder 방식)으로 수집
- 가이드는 영어, 질문은 한국어 → BM25 전에 rewrite_query로 영어 키워드 질의 생성
- Store namespace 라벨에 점(.)을 쓸 수 없음 (user_id에 점 금지)
- MCP 도구는 비동기로 로드 → build_app은 async, 실행은 ainvoke
- MultiServerMCPClient.get_tools()는 호출마다 서버 프로세스를 새로 띄움 → src/quiz_client.py가 세션 1개를 열어 두고 call_tool
- Bedrock Converse는 parallel_tool_calls 끄기를 지원하지 않음 → Supervisor 동시 핸드오프는 post_model_hook(single_handoff)로 제거
- output_mode="last_message"는 하위 에이전트의 마지막 메시지만 올림 → 출제·채점은 return_direct 도구 결과를 답변으로 씀 (agent.turn_answer)
- ragas 0.4.3은 langchain_community.chat_models.vertexai를 import (0.4에서 삭제) → ragas_smoke.py 안의 자리표시 모듈로 우회

## 작업 방식
- 새 모듈이나 여러 파일에 걸친 작업은 plan mode로 계획을 먼저 보여준다
- 한 단계가 끝나면 해당 스모크 테스트를 돌리고 결과를 보고한다
