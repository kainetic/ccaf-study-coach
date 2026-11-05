# 시연 05 · 가이드 안/밖 사실 · 시험 범위

> 가이드에 있는 사실은 근거와 함께 답하고(턴 1), 없는 정보는 지어내지 않고 coverage_gap으로 밝히며(턴 2), 범위 질문은 SCOPE 청크를 근거로 답한다(턴 3).
>
> 생성: 2026-11-05 09:03 · 커밋 73f600c · user_id `demo_new` · `python scripts/demo_capture.py --only 05`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: CCA-F 자격증 유효기간은 몇 년이야?

**답변**

```text
CCA-F 자격증의 유효기간(validity period)은 **12개월**입니다. 자격증이 수여된 날짜(the date the credential is awarded)로부터 12개월간 유효합니다.

[근거: OV-details]
```

**trace 요약** (응답 22.0초, thread `517b313a`)

- guard: action=pass rule=None layer=llm
- route → concept_tutor
- retrieve (hybrid): OV-details, OV-candidate, SCOPE-out, SCOPE-tech
- output_check: rule=G3 claims=1 valid=1 fallback=False
- output_check: rule=G4
- 호출 도구: search_guide
- LLM 호출 5회 · 입력 16712 / 출력 318 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 5}

contexts doc_id: OV-details, OV-candidate, SCOPE-out, SCOPE-tech

## 턴 2

**질문**: CCA-F 떨어지면 며칠 뒤에 다시 응시할 수 있어?

**답변**

```text
가이드에 근거 없음: 공식 시험 가이드(v0.2)에서 이 질문에 답할 근거를 찾지 못해 추측으로 답하지 않습니다. 시험 개념·범위·형식에 관한 질문으로 다시 물어봐 주세요.
(가이드에 없는 부분: 재응시 정책 및 대기 기간)
```

**trace 요약** (응답 31.6초, thread `63cd6ae0`)

- guard: action=pass rule=None layer=llm
- route → concept_tutor
- retrieve (hybrid): SQ-08, TS-2.2, SCOPE-in, SCOPE-out
- retrieve (hybrid): SCOPE-out, SQ-08, TS-2.2, SCOPE-in
- retrieve (hybrid): OV-details, D4, SCOPE-in, SCOPE-out
- retrieve (get_chunk): OV-candidate
- output_check: rule=G3 claims=0 valid=0 fallback=True
- output_check: rule=G4
- 호출 도구: search_guide, search_guide, search_guide, get_chunk
- LLM 호출 10회 · 입력 44859 / 출력 585 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 10}

contexts doc_id: SQ-08, TS-2.2, SCOPE-in, SCOPE-out, OV-details, D4, OV-candidate

## 턴 3

**질문**: OAuth 인증 설정 방법도 시험에 나와?

**답변**

```text
**아니요, OAuth 인증 설정은 시험 범위에서 명시적으로 제외됩니다.**

CCA-F 시험은 OAuth, API 키 로테이션, 인증 프로토콜 세부사항을 다루지 않습니다. 시험 범위 밖(out-of-scope)으로 명시된 주제에는 Claude API 인증(authentication), 빌링, 계정 관리도 포함됩니다.

시험은 에이전트 구현, 도구 설계, MCP 서버 구성, 프롬프트 엔지니어링 등 **아키텍처 및 구현 패턴**에 집중합니다.

[근거: SCOPE-out]
```

**trace 요약** (응답 20.3초, thread `71f2387f`)

- guard: action=pass rule=None layer=llm
- route → concept_tutor
- retrieve (hybrid): SCOPE-in, SCOPE-tech, SCOPE-out
- output_check: rule=G3 claims=2 valid=2 fallback=False
- output_check: rule=G4
- 호출 도구: search_guide
- LLM 호출 5회 · 입력 16348 / 출력 552 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 5}

contexts doc_id: SCOPE-in, SCOPE-tech, SCOPE-out
