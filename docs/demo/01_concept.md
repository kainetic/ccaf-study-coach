# 시연 01 · 개념 질문

> concept_tutor가 가이드를 검색해 답하고, 코드(G3)가 검증한 claim으로 [근거] 표기를 붙인다.
>
> 생성: 2026-11-05 08:59 · 커밋 73f600c · user_id `demo_new` · `python scripts/demo_capture.py --only 01`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: MCP에서 resource와 tool은 각각 언제 쓰는 거야?

**답변**

```text
MCP에서 resource와 tool은 사용 목적이 다릅니다.

**Resource(리소스)**: 콘텐츠 카탈로그(content catalogs)를 노출할 때 사용합니다. 예를 들어 이슈 요약, 문서 계층 구조, 데이터베이스 스키마 등 에이전트가 탐색적 tool 호출 없이도 어떤 데이터가 있는지 미리 볼 수 있게 합니다.

**Tool(도구)**: 실제 작업(actions)을 수행할 때 사용합니다. 데이터를 조회하거나 변경하는 등의 동작을 실행합니다.

핵심 차이는 resource는 "무엇이 있는지 보여주는 것"이고, tool은 "무언가를 실행하는 것"입니다. Resource를 제공하면 에이전트가 불필요한 탐색 호출을 줄이고 효율적으로 작업할 수 있습니다.

[근거: TS-2.4, SCOPE-in]
```

**trace 요약** (응답 28.2초, thread `0187d998`)

- guard: action=pass rule=None layer=llm
- route → concept_tutor
- retrieve (hybrid): TS-2.4, EX-1, SCOPE-in, D2
- output_check: rule=G3 claims=3 valid=3 fallback=False
- output_check: rule=G4
- 호출 도구: search_guide
- LLM 호출 5회 · 입력 16520 / 출력 813 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 5}

contexts doc_id: TS-2.4, EX-1, SCOPE-in, D2
