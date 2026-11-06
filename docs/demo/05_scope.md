# 시연 05 · 가이드 안/밖 사실 · 시험 범위

> 가이드에 있는 사실은 근거와 함께 답하고(턴 1), 없는 정보는 지어내지 않고 coverage_gap으로 밝히며(턴 2), 범위 질문은 SCOPE 청크를 근거로 답한다(턴 3).
>
> 생성: 2026-11-06 09:02 · 커밋 a1345cd · user_id `demo_new`(풀이 기록은 시드 상태에서 시작) · 턴마다 새 대화(thread)
> `python scripts/demo_capture.py --only 05`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: CCA-F 자격증 유효기간은 몇 년이야?

**답변**

```text
CCA-F 자격증의 유효기간(Validity period)은 **12개월**입니다. 자격증이 수여된 날짜(the date the credential is awarded)로부터 12개월간 유효합니다.

[근거: OV-details]
```

**trace 요약** (응답 9.3초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): OV-details, OV-candidate, SCOPE-out, SCOPE-tech
- G3 근거 검증: claim 1개 중 유효 1개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide
- LLM 호출 3회 (입력 가드 판별 제외) · 토큰 입력 9388 / 출력 269 · global/sonnet-4-5@ap-northeast-2 ×3

contexts doc_id: OV-details, OV-candidate, SCOPE-out, SCOPE-tech

## 턴 2

**질문**: CCA-F 떨어지면 며칠 뒤에 다시 응시할 수 있어?

**답변**

```text
가이드에 근거 없음: 공식 시험 가이드(v0.2)에서 이 질문에 답할 근거를 찾지 못해 추측으로 답하지 않습니다. 시험 개념·범위·형식에 관한 질문으로 다시 물어봐 주세요.
(가이드에 없는 부분: 재응시 대기 기간 및 재응시 정책)
가이드에 없는 정보는 Anthropic 공식 안내 채널에서 확인하세요.
```

**trace 요약** (응답 18.9초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): OV-details, SCOPE-out, SCOPE-in, SQ-01
- 검색 (hybrid): TS-4.4, TS-2.2, SCOPE-in, SCOPE-out
- 검색 (hybrid): TS-2.2, TS-4.4, SQ-08
- 검색 (get_chunk): OV-details
- G3 근거 검증: claim 0개 중 유효 0개 → 근거 없음 고정 문구
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide, search_guide, search_guide, get_chunk
- LLM 호출 8회 (입력 가드 판별 제외) · 토큰 입력 38694 / 출력 533 · global/sonnet-4-5@ap-northeast-2 ×8

contexts doc_id: OV-details, SCOPE-out, SCOPE-in, SQ-01, TS-4.4, TS-2.2, SQ-08

## 턴 3

**질문**: OAuth 인증 설정 방법도 시험에 나와?

**답변**

```text
**아니요, OAuth 인증 설정 방법은 시험에 나오지 않습니다.**

CCA-F 시험은 OAuth, API 키 로테이션(rotation), 인증 프로토콜 세부 사항을 명시적으로 시험 범위에서 제외합니다. 시험은 Claude를 활용한 아키텍처 설계와 구현에 집중하며, 인증 메커니즘의 구체적인 설정 방법은 다루지 않습니다.

시험에서 다루는 MCP 서버 구성(MCP server configuration)에는 환경 변수 확장(environment variable expansion)을 통한 자격 증명(credentials) 처리가 포함되지만, 이는 OAuth 프로토콜 자체의 설정이 아니라 MCP 서버 설정 파일에서 환경 변수를 사용하는 방법에 관한 것입니다.

[근거: SCOPE-out, SCOPE-in, EX-2]
```

**trace 요약** (응답 17.9초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): SCOPE-in, OV-details, SCOPE-tech, EX-2
- 검색 (hybrid): SCOPE-tech, SCOPE-in, SCOPE-out
- G3 근거 검증: claim 4개 중 유효 4개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide, search_guide
- LLM 호출 5회 (입력 가드 판별 제외) · 토큰 입력 18915 / 출력 799 · global/sonnet-4-5@ap-northeast-2 ×5

contexts doc_id: SCOPE-in, OV-details, SCOPE-tech, EX-2, SCOPE-out
