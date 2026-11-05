# 시연 05 · 가이드 안/밖 사실 · 시험 범위

> 가이드에 있는 사실은 근거와 함께 답하고(턴 1), 없는 정보는 지어내지 않고 coverage_gap으로 밝히며(턴 2), 범위 질문은 SCOPE 청크를 근거로 답한다(턴 3).
>
> 생성: 2026-11-05 16:34 · 커밋 954c75e · user_id `demo_new`(풀이 기록은 시드 상태에서 시작) · 턴마다 새 대화(thread)
> `python scripts/demo_capture.py --only 05`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: CCA-F 자격증 유효기간은 몇 년이야?

**답변**

```text
CCA-F 자격증의 유효기간(validity period)은 **12개월**입니다. 자격증이 수여된 날짜(the date the credential is awarded)로부터 12개월간 유효합니다.

[근거: OV-details]
```

**trace 요약** (응답 10.8초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): OV-details, OV-candidate, SCOPE-out, SCOPE-tech
- G3 근거 검증: claim 1개 중 유효 1개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide
- LLM 호출 5회 (입력 가드 판별 제외) · 토큰 입력 16944 / 출력 324 · global/sonnet-4-5@ap-northeast-2 ×5

contexts doc_id: OV-details, OV-candidate, SCOPE-out, SCOPE-tech

## 턴 2

**질문**: CCA-F 떨어지면 며칠 뒤에 다시 응시할 수 있어?

**답변**

```text
가이드에 근거 없음: 공식 시험 가이드(v0.2)에서 이 질문에 답할 근거를 찾지 못해 추측으로 답하지 않습니다. 시험 개념·범위·형식에 관한 질문으로 다시 물어봐 주세요.
(가이드에 없는 부분: 재응시 대기 기간)
가이드에 없는 정보는 Anthropic 공식 안내 채널에서 확인하세요.
```

**trace 요약** (응답 16.9초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): TS-4.4, SCOPE-out, SCOPE-in, SQ-08
- 검색 (hybrid): OV-details, OV-candidate
- G3 근거 검증: claim 0개 중 유효 0개 → 근거 없음 고정 문구
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide, search_guide
- LLM 호출 7회 (입력 가드 판별 제외) · 토큰 입력 24877 / 출력 552 · global/sonnet-4-5@ap-northeast-2 ×7

contexts doc_id: TS-4.4, SCOPE-out, SCOPE-in, SQ-08, OV-details, OV-candidate

## 턴 3

**질문**: OAuth 인증 설정 방법도 시험에 나와?

**답변**

```text
**아니요, OAuth 인증 설정은 시험 범위에서 명시적으로 제외됩니다.**

CCA-F 시험은 OAuth, API 키 로테이션(rotation), 인증 프로토콜 세부사항을 다루지 않습니다. 시험 범위 밖(out-of-scope) 주제에 "OAuth, API key rotation, or authentication protocol details"가 명시되어 있습니다.

시험은 Claude를 활용한 에이전트 시스템 설계와 구현에 집중하며, 인증·과금·계정 관리 같은 인프라 운영 주제는 포함하지 않습니다.

[근거: SCOPE-out]
```

**trace 요약** (응답 15.8초)

- 입력 가드: 통과 (LLM 판별)
- 배분: concept_tutor
- 검색 (hybrid): SCOPE-in, SCOPE-tech, SCOPE-out
- G3 근거 검증: claim 2개 중 유효 2개
- G4 출력 검사: 위반 없음
- 호출 도구: search_guide
- LLM 호출 5회 (입력 가드 판별 제외) · 토큰 입력 16559 / 출력 544 · global/sonnet-4-5@ap-northeast-2 ×5

contexts doc_id: SCOPE-in, SCOPE-tech, SCOPE-out
