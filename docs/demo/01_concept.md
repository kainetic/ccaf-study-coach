# 시연 01 · 개념 질문

> concept_tutor가 가이드를 검색해 답하고, 코드(G3)가 검증한 claim으로 [근거] 표기를 붙인다.
>
> 생성: 2026-11-06 09:01 · 커밋 a1345cd · user_id `demo_new`(풀이 기록은 시드 상태에서 시작) · 모든 턴이 같은 대화(thread)에서 이어짐
> `python scripts/demo_capture.py --only 01`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: MCP에서 resource와 tool은 각각 언제 쓰는 거야?

**답변**

```text
**Resource**는 콘텐츠 카탈로그를 노출할 때, **Tool**은 액션을 수행할 때 사용합니다.

- **MCP Resources**: 이슈 요약, 문서 계층 구조, 데이터베이스 스키마 같은 콘텐츠 카탈로그(content catalogs)를 노출하여, 에이전트가 탐색용 tool call 없이도 사용 가능한 데이터를 미리 파악할 수 있게 합니다.

- **MCP Tools**: 실제 작업을 수행하는 액션(actions)을 위해 사용합니다. 모든 설정된 MCP 서버의 tool들은 연결 시점에 발견되어 에이전트가 동시에 사용할 수 있습니다.

즉, resource는 "무엇이 있는지 보여주는" 읽기 전용 카탈로그, tool은 "무언가를 실행하는" 기능으로 구분됩니다.

[근거: TS-2.4, SCOPE-in]
```

**trace 요약** (응답 13.6초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): TS-2.4, EX-1, D2, SCOPE-in
- G3 근거 검증: claim 4개 중 유효 4개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide
- LLM 호출 3회 (입력 가드 판별 제외) · 토큰 입력 8945 / 출력 895 · global/sonnet-4-5@ap-northeast-2 ×3

contexts doc_id: TS-2.4, EX-1, D2, SCOPE-in
