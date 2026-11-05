# CCA-F 학습 코치 (mini PJT)

CCA-F 시험 가이드 기반 멀티에이전트 학습 도우미. LangGraph Supervisor + Bedrock Claude.
기획: SERVICE.md · 설계: docs/DESIGN.md · 평가: evaluation/test_queries.csv
설계 세부는 필요할 때 docs/DESIGN.md의 해당 절(§)을 읽을 것. 전체를 미리 읽지 말 것.

## 현재 단계: S1~S6 완료 · push 완료 (DESIGN §1-1 단계표)
- 최종: round2 19/20 → v2-1(GAP_NOTICE) 20/20 → v2-2(가드 병렬) 19/20·개념 평균 26.4→22.4s
  → v2-3(case_facts·요약·청크 필터) 19/20, RAGAS precision 0.744→0.825·faithfulness 0.858→0.789(변동 추정, 미확인).
  smoke_p0 24/24, smoke_p1 37/37, smoke_memory 9/9. 수치·실패 분석은 evaluation/round*_report.md, quiz_pool_report.md
- 데이터: 문제 풀 승인 266, study_log 시드 demo_heavy 61회·demo_new 없음 (seed_study_log.py)
- 원격: https://github.com/kainetic/ccaf-study-coach (private, master). 개선 후보는 round2_report §6

## 명령 (Windows PowerShell)
.venv\Scripts\activate; pip install -r requirements.txt     # 저장소 폴더 안 로컬 venv (.gitignore에 포함)
python scripts/check_bedrock.py                      # Bedrock LLM·임베딩 연결 확인
python scripts/check_bedrock.py --quiz-chain         # 문제 풀 생성 모델 체인(QUIZ_GEN_MODEL_CHAIN) 4개 각각 접근 확인
python -m src.ingest --dry-run                       # 파싱·62/240 검증만
python -m src.ingest                                 # 가이드 PDF → guide_chunks.jsonl(62) + guide_bullets.jsonl(240) → Chroma
python scripts/build_quiz_pool.py --resume           # 문제 풀 생성 (매니페스트 기준 이어서). 다른 모드(--pilot·--fill-gaps·
                                                     # --rebalance-length·--exception-slot·--reset-stale·--concurrency)는 --help, DESIGN §5-2
python scripts/review_sample.py                      # 사람 표본 검토 xlsx + 결과 CSV (층화, repair 문항 층마다 1개 이상)
python scripts/review_sample.py --apply             # 검토 반영: human_checked, 오류 → needs_fix, 리포트 갱신 (--dry-run 가능)
python scripts/pool_report.py                        # evaluation/quiz_pool_report.md 자동 절 갱신
python scripts/seed_study_log.py                     # 더미 사용자 demo_heavy·demo_new → data/study_log.json (--force 재생성, --dry-run)
uvicorn src.api:api --port 8000                      # API 서버
python evaluation/smoke_p0.py                        # P0 한 바퀴 검증 (--url 실행 중 서버, --keep 기록 남김)
python evaluation/ragas_smoke.py                     # RAGAS 3건 스모크 → evaluation/ragas_smoke.json
python evaluation/smoke_p1.py                        # P1 오프라인 검증 (가드 규칙·G3·진도·배분 합계·트리밍·case_facts, LLM 없음)
python evaluation/smoke_memory.py                    # 긴 대화 5턴: 요약 + case_facts 보존 (Bedrock, study_log 임시 사본)
python evaluation/run_eval.py --label round1         # 20건 평가 → evaluation/results/round1_eval.json + logs/eval/round1_full.json (--ids 7,13)
python evaluation/ragas_smoke.py --from logs/eval/round1_full.json --out evaluation/ragas_round1.json   # 같은 답변으로 RAGAS 14건
python scripts/demo_capture.py                       # 시연 캡처 → docs/demo/*.md (--only 02,04, 실제 Bedrock 호출, study_log 임시 사본)
MCP 서버(src/quiz_bank_server.py)는 단독 실행하지 않음. 클라이언트가 sys.executable로 stdio 자식 프로세스로 띄움.

## 고정값
- LLM: ChatBedrockConverse, model "us.anthropic.claude-sonnet-4-5-20250929-v1:0", region "us-east-1", temperature 0
  - 예외: 쓰로틀링·일일 할당량 소진 등 일시 오류일 때만 모델 전환 (Haiku 미사용)
    - 문제 풀 생성(오프라인 quiz_gen): config.QUIZ_GEN_MODEL_CHAIN (Sonnet 4.5/4.6 × us/global, DESIGN §5-2)
    - 런타임 에이전트: src/llm.py FallbackChatModel, config.RUNTIME_MODEL_CHAIN (Sonnet 4.5 다른 프로필·리전
      → Sonnet 4.6, us-east-1·us-west-2·ap-northeast-2, DESIGN §7-1). LLM은 config.get_llm()이 아니라 get_runtime_llm()
- 임베딩: BedrockEmbeddings "amazon.titan-embed-text-v2:0"
- Chroma: persist ./chroma_db, collection "ccaf_guide"
- 자격 증명: 저장소 폴더(프로젝트 루트)의 .env를 사용 (.gitignore에 포함, 커밋 금지).
  src/config.py 한 곳에서만 load_dotenv(ROOT / ".env")로 로드하고,
  다른 모듈·스크립트는 src.config를 import해서 씀 (모듈마다 load_dotenv() 호출 금지). 코드·문서에 키를 쓰지 않음.
  .env 파일이 없으면 시작 시 경로를 포함한 오류로 즉시 종료
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
- concept_tutor는 ToolStrategy(TutorAnswer) + after_agent(TutorAnswerGuard)가 마지막 AIMessage를 만든다 → 답변 텍스트를 바꾸려면 프롬프트가 아니라 guards.check_tutor_answer
- 입력 가드 LLM 판별은 그래프와 병렬(v2-2) → 새 앱 도구는 첫 줄에 `await guards.wait_input_ok()` 필수
  (없으면 LLM이 차단하기 전에 도구가 실행됨). 차단 시 CoachApp.run이 그래프 취소 + 이번 턴 메시지 되돌림
- Supervisor pre_model_hook에서 messages를 RemoveMessage로 지우면 내부 그래프에만 적용되고 바깥 thread에는 남음
  → 장기 대화 요약은 CoachApp.run이 그래프 시작 전에 memory.compact + aupdate_state로 처리
- 평가·스모크가 출제하면 study_log에 issued가 쌓임 → run_eval·demo_capture는 임시 사본(CCAF_STUDY_LOG)을 씀.
  smoke_p0는 data/study_log.json을 직접 다시 씀(종료 시 smoke 사용자 삭제, 형식 차이·중단 시 잔존 가능)
  → 실행 전 $env:CCAF_STUDY_LOG에 임시 사본 경로 지정 권장
- 가드 고정 문구(guards.REFUSALS·G4_CORRECTION·NO_EVIDENCE·GAP_NOTICE·OFF_TOPIC)는 run_eval이 문자열로 매칭 → 문구를 바꾸면 평가 기준도 바뀜

## 작업 방식
- 새 모듈이나 여러 파일에 걸친 작업은 plan mode로 계획을 먼저 보여준다
- 한 단계가 끝나면 해당 스모크 테스트를 돌리고 결과를 보고한다
