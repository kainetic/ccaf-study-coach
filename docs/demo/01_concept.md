# 시연 01 · 개념 질문

> concept_tutor가 가이드를 검색해 답하고, 코드(G3)가 검증한 claim으로 [근거] 표기를 붙인다.
>
> 생성: 2026-11-05 16:33 · 커밋 954c75e · user_id `demo_new`(풀이 기록은 시드 상태에서 시작) · 모든 턴이 같은 대화(thread)에서 이어짐
> `python scripts/demo_capture.py --only 01`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: MCP에서 resource와 tool은 각각 언제 쓰는 거야?

**답변**

```text
MCP에서 resource와 tool은 용도가 다릅니다.

**Resource(리소스)**: 콘텐츠 카탈로그(content catalog)를 노출할 때 사용합니다. 예를 들어 이슈 요약, 문서 계층 구조, 데이터베이스 스키마처럼 에이전트가 어떤 데이터를 사용할 수 있는지 미리 보여주는 용도입니다. 리소스를 제공하면 에이전트가 탐색용 도구 호출(exploratory tool calls)을 줄일 수 있습니다.

**Tool(도구)**: 실제 작업(action)을 수행할 때 사용합니다. 에이전트가 특정 기능을 실행하거나 조작을 수행해야 할 때 도구를 호출합니다.

간단히 말해, 리소스는 "무엇이 있는지 보여주는" 읽기 전용 카탈로그이고, 도구는 "실제로 무언가를 하는" 실행 가능한 기능입니다.

[근거: TS-2.4, SCOPE-in]
```

**trace 요약** (응답 21.5초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): TS-2.4, D2, EX-1, SCOPE-in
- G3 근거 검증: claim 3개 중 유효 3개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide
- LLM 호출 5회 (입력 가드 판별 제외) · 토큰 입력 16761 / 출력 836 · global/sonnet-4-5@ap-northeast-2 ×5

contexts doc_id: TS-2.4, D2, EX-1, SCOPE-in
