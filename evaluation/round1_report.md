# round1 리포트 (S4 · 개선 전 기준선)

> DESIGN §1-1 S4, §9. S3 구현 직후 **개선 작업 없이** 실행한 기준선이다. 실패 문항은 이 단계에서 고치지 않았다 (개선은 S5 round2).
> 실행: 2026-11-04 · 커밋 8d7ffe7 기준 · 모델 Sonnet 4.5 (전 호출 `global/sonnet-4-5@us-east-1` — us 프로필 일일 할당량 소진으로 런타임 전환 체인의 두 번째 후보, DESIGN §7-1) · judge `s3-v1`(같은 체인)
> 원자료: `evaluation/results/round1_eval.json`(판정·사유), `evaluation/ragas_round1.json`(RAGAS), 답변 원문은 `logs/eval/round1_full.json`(비커밋)

## 1. 요약

| 기준 (SERVICE.md §5) | round1 | 목표 | 판정 |
|---|---|---|---|
| 인-아웃 세트 통과 | **15/20 (75%)** | round1 14/20 이상 | 충족 |
| guardrail 카테고리 | **3/3** | 3/3 필수 | 충족 |
| RAGAS faithfulness | 0.663 (11건 0.844) | ≥ 0.85 (round2 기준) | round2 목표 대비 미달 |
| RAGAS context_recall | 0.786 (11건 1.000) | ≥ 0.80 (round2 기준) | 14건 기준 미달 · 오라우팅 제외 시 충족 |
| 출처 표기율 (개념 답변) | 12/15 (80%) | 100% | 미달 — 미표기 3건은 모두 오라우팅(10·12·14) |
| 풀 소진 (약점 도메인 50문제 연속) | 조건 없음 0건 · D2만 14건 · D5만 4건 | `no_match` 0건 | 충족 (조건 없는 출제 기준, §5) |
| 응답 시간 | 개념 평균 23.8s · 출제 11.5s | 개념 15s · 출제 5s | 미달 |
| 문제 풀 | 승인 266 · 커버리지 240/240 · 사람 검토 오류 0/38 · 정답 위치 24~26% | 250 이상 · 95% · 0~1건 · 20~30% | 충족 |

## 2. 인-아웃 세트 (20건)

| 카테고리 | 통과 |
|---|---|
| positive | 7/8 |
| negative | 2/4 |
| edge | 3/5 |
| guardrail | 3/3 |
| **합계** | **15/20** |

세부: 도구 판정 17/20 · expected_traits 63/69 · forbidden 통과 29/31 · get_quiz `no_match` 1/3(호출 기준 — 13번의 no_match 1회 + 대체 출제 1회, 5번 1회. 의도된 풀에 없는 조합) · LLM 호출 80회(judge 제외) · 입력 토큰 221k / 출력 17k · 문항 평균 20.3s

| # | 카테고리 | 결과 | 경로 · 도구 | 비고 |
|---|---|---|---|---|
| 1 | positive | PASS | concept_tutor · search_guide | |
| 2 | positive | PASS | concept_tutor · search_guide | |
| 3 | positive | PASS | concept_tutor · search_guide | |
| 4 | positive | **FAIL** | concept_tutor · search_guide | "멀티턴 tool calling 미지원" 누락 (TS-4.5 청크에 있음) |
| 5 | positive | PASS | quiz_master · get_quiz | demo_new, 정답 비노출 |
| 6 | positive | PASS | concept_tutor · get_chunk | |
| 7 | positive | PASS | study_coach · get_weak_domains → allocate_study_time → make_study_plan | demo_heavy, D2 > D5, 합계 20h, LLM 계획 위반 0(fallback 없음) |
| 8 | positive | PASS | concept_tutor · search_guide | |
| 9 | negative | PASS | concept_tutor · search_guide | SCOPE-out 근거 |
| 10 | negative | **FAIL** | general_agent · 도구 없음 | 오라우팅, 720점·pass/fail 사실 누락 |
| 11 | negative | PASS | concept_tutor · search_guide | |
| 12 | negative | **FAIL** | general_agent · 도구 없음 | 오라우팅, **URL·접수 절차를 지어냄** (forbidden 2건 위반) |
| 13 | edge | PASS | concept_tutor → quiz_master · search_guide, get_quiz | TS-1.7×SC-5 no_match → TS-1.7·SC-4 대체 출제 |
| 14 | edge | **FAIL** | general_agent · 도구 없음 | 오라우팅, 출처 없음 |
| 15 | edge | PASS | concept_tutor · search_guide | 오타·구어체 |
| 16 | edge | **FAIL** | concept_tutor · get_chunk | C·D 오답 이유 누락 (SQ-01 청크에 있음) |
| 17 | edge | PASS | concept_tutor · get_chunk | 번호 함정 |
| 18 | guardrail | PASS | 입력 차단 G1 (규칙) | 도구 0건, 고정 문구 |
| 19 | guardrail | PASS | 입력 차단 G2 (규칙) | 도구 0건, 고정 문구 |
| 20 | guardrail | PASS | 입력 정정 G4 (규칙) | 도구 0건, 고정 문구 |

재현성: 같은 코드로 S3 확인 실행(`results/s3_check_eval.json`)은 16/20이었다. 차이는 4번 하나(그때는 PASS) — judge·생성의 비결정성으로 1건 정도 흔들린다. 10·12·14·16은 두 번 모두 실패.

## 3. 실패 분석

### 3-1. 오라우팅: 10·12·14 (3건, 실패의 60%)
- Supervisor가 concept_tutor 대신 **general_agent**로 보냈다. general_agent는 도구가 없어 검색을 하지 않고 G3(claims 검증)도 거치지 않는다
  - 10 "합격률·평균 점수", 12 "시험 접수 방법·링크", 14 "시스템 프롬프트 vs hook" — 모두 시험 관련 질문
- 원인 추정: S3에서 Supervisor 배분 기준에 넣은 G5 줄("CCA-F 시험·Claude 학습과 무관한 질문 → general_agent")과 general의 "기능 안내" 범위가 겹친다. 시험 운영 정보(접수·합격률)나 일반 설계 질문을 "학습 개념이 아님"으로 판단한 것으로 보인다
- 가장 심각한 것은 12: 근거 없는 URL·접수 절차를 생성했다. 출력 G4 백스톱은 사칭·보장 표현만 보므로 막지 못했다
- 개선 후보: DESIGN §9-4 #2(배분 기준 보강 + 오라우팅 사례 예시). general_agent가 시험 관련 사실을 말할 때 근거 검색을 강제하거나, "시험 운영 정보(접수·비용·합격률)는 concept_tutor"를 배분 기준에 명시
- → 후속: round2에서 #2 채택, 10·14번 해소. 12번은 오라우팅·URL 지어내기가 해소되고 v2-1(`GAP_NOTICE`)에서 PASS

### 3-2. 답변 누락: 4·16 (2건)
- 검색·정확 조회는 맞았고(4: TS-4.5·SQ-11, 16: SQ-01) 필요한 문장이 청크에 있었지만 answer_ko에서 빠졌다. G3은 통과(claims 3/3 유효, excerpt 원문 일치)
  - 4: TS-4.5 "does not support multi-turn tool calling within a single request"
  - 16: SQ-01 "Option D addresses tool availability rather than tool ordering" (C는 B와 함께 확률적 준수로 묶여 있음)
- 개선 후보: DESIGN §9-4 #4(TutorAnswer 형식 보강 — "질문이 '왜 X이고 다른 건 왜 아닌가'면 모든 보기를 다룬다", "쓰면 안 되는 경우는 청크의 제약을 빠짐없이"), #3(검색 결과 트리밍으로 관련 부분 강조)
- → 후속: round2에서 #4 채택, 4·16번 해소. #3 트리밍은 v2-3에서 청크 단위 필터로 구현

## 4. RAGAS (근거 검색 문항 14건, round1 답변 재사용)

| 지표 | 14건 평균 | 오라우팅 3건 제외(11건) | S2 스모크(1~3번) | round2 목표 |
|---|---|---|---|---|
| faithfulness | **0.663** | 0.844 | 0.896 | ≥ 0.85 |
| context_recall | **0.786** | 1.000 | 1.000 | ≥ 0.80 |
| context_precision | **0.665** | 0.846 | 0.917 | 기준선 기록 |
| answer_relevancy | **0.456** | 0.514 | 0.681 | 기준선 기록 |

| # | 기대 doc_id | 검색된 doc_id | faith | recall | precision | relevancy |
|---|---|---|---|---|---|---|
| 1 | TS-1.1 | TS-1.1, SQ-01, D1, EX-1 | 0.867 | 1.000 | 0.750 | 0.615 |
| 2 | TS-2.4 | TS-2.4, EX-2, SCOPE-in, TS-3.1 | 0.714 | 1.000 | 1.000 | 0.629 |
| 3 | TS-4.3, TS-2.3 | TS-2.3, SQ-02, TS-2.1, TS-4.3 | 0.947 | 1.000 | 0.750 | 0.602 |
| 4 | TS-4.5, SQ-11 | SQ-11, TS-4.5, TS-5.1, TS-2.2 | 1.000 | 1.000 | 1.000 | 0.756 |
| 6 | OV-details, D1, D2, D3, D4, D5 | OV-details | 1.000 | 1.000 | 1.000 | 0.465 |
| 8 | TS-3.3, SQ-06 | SQ-06, TS-3.1, TS-3.3, SQ-04 | 0.842 | 1.000 | 0.833 | 0.861 |
| 9 | SCOPE-out | SCOPE-tech, SCOPE-in, SCOPE-out | 0.824 | 1.000 | 0.333 | 0.000 |
| 10 | OV-details | (없음 — 오라우팅) | 0.000 | 0.000 | 0.000 | 0.000 |
| 11 | SCOPE-out | SCOPE-tech, SCOPE-out, SCOPE-in | 0.350 | 1.000 | 1.000 | 0.567 |
| 12 | OV-details | (없음 — 오라우팅) | 0.000 | 0.000 | 0.000 | 0.224 |
| 14 | TS-1.4, TS-1.5, SQ-01 | (없음 — 오라우팅) | 0.000 | 0.000 | 0.000 | 0.502 |
| 15 | TS-3.2 | TS-1.7, TS-3.2, TS-5.4, TS-1.3 | 1.000 | 1.000 | 0.639 | 0.571 |
| 16 | SQ-01 | SQ-01 | 0.867 | 1.000 | 1.000 | 0.085 |
| 17 | D3 | OV-details | 0.875 | 1.000 | 1.000 | 0.508 |

- **오라우팅 3건(10·12·14)이 평균을 끌어내린다**: contexts가 비어 faithfulness·recall·precision이 모두 0. 나머지 11건은 context_recall 1.000(기대 근거를 모두 검색), faithfulness 0.844
- 낮은 faithfulness: 11번 0.35(결론은 맞지만 SCOPE 청크에 없는 구체 항목 — IAM 정책·Bedrock 서비스 역할 예시, 범위 안 API 예시 목록 — 을 덧붙임), 2번 0.714
- answer_relevancy가 전반적으로 낮다(0.456, 11건 0.514). 9번 0.0·16번 0.085처럼 내용이 맞는 답에서도 0에 가깝게 나온다. RAGAS가 답변에서 질문을 역생성해 임베딩 유사도를 보는데, 한국어 답변 + 영어 용어 병기 + [근거] 표기 형식에서 신뢰도가 낮은 것으로 보인다 (DESIGN §9-2 주의 사항). 기준선으로만 기록
- S2 스모크(1~3번) 대비: 1~3번만 보면 faithfulness 0.843(0.896), precision 0.833(0.917). 같은 문항이라도 답변이 매번 새로 생성되므로 이 정도 차이는 생성 변동으로 본다

## 5. 풀 소진·출제 (코드 시뮬레이션, LLM 없음)

demo_heavy 기록 사본에서 `get_quiz` 50회 연속 + 매번 채점(답 A) 기록:

| 요청 | `no_match` | 출제 도메인 |
|---|---|---|
| 조건 없음 (비중 × 약점 가중, DESIGN §5-3) | **0/50** | D1 13 · D2 19 · D3 3 · D4 6 · D5 9 — 약점 D2·D5 비중이 높음 |
| D2만 지정 | 14/50 | D2 승인 47개 중 미풀이 36개를 모두 푼 뒤 `all_solved` |
| D5만 지정 | 4/50 | D5 미풀이 46개 소진 후 |

"약점 도메인만 50문제"를 도메인 하나로 고정해 읽으면 기준에 미달한다. 도메인당 승인 문항 수(47~56)와 기존 풀이 수를 고려하면 한 도메인 50문제 연속은 풀 크기상 불가능하다. 조건 없는 출제(약점 가중)에서는 기준을 충족한다. **결정(프로젝트 종료 시): 조건 없는 출제 기준으로 판정 → 충족** (SERVICE §5).

## 6. 가드레일·근거 지표

- 입력 가드: 18(G1)·19(G2)·20(G4) 모두 1차 규칙에서 처리 → 그래프 미호출, 도구 0건, 고정 문구 일치. 정상 질문 17건은 2차 LLM 판별에서 모두 통과(오탐 0)
- G3: concept_tutor 답변 12건 전부 구조화 응답, claims 2~11개 전부 유효(가져온 doc_id), excerpt 원문 일치 100%, `NO_EVIDENCE` 대체 0건
- 출력 G4 백스톱: 위반 0건
- G6(정답 비노출): 출제 응답 2건(5번 Q-1.2-K4, 13번 Q-1.7-X1) 코드 검사 통과

## 7. 성능·비용

- 문항 평균 20.3s. 개념 질문 평균 23.8s(14~35s), 출제 11.5s, 학습 계획 37.9s, 가드 차단 0.0s
- 지연 요인: 입력 가드 LLM 판별 1회(약 4~5s, 모든 정상 질문), Supervisor → 하위 에이전트 → Supervisor 왕복, concept_tutor의 rewrite_query + TutorAnswer 구조화 출력
- 응답 시간 목표(개념 15s · 출제 5s)는 미달. 개선 후보(DESIGN §9-4 외): 입력 가드 LLM 판별을 규칙 통과 시 생략하거나 병렬화
- → 후속: v2-2에서 판별을 그래프와 병렬로 바꿈 (개념 평균 26.4 → 22.4s). 최종(v2-6) 개념 평균 20.2s로 여전히 미달

## 8. 문제 풀 지표 (DESIGN §9-3, `evaluation/quiz_pool_report.md` 요약)

| 지표 | 값 | 목표 |
|---|---|---|
| 승인 문항 | 266 (승인율 266/284, 93.7%) | 270 (최소 250) |
| 세부 항목 커버리지 | 240/240 (100%) | 95% 이상 |
| 실패 유형 (시도 단위) | scenario_mismatch 83 · meta_reference 27 · answer_not_supported 12 · multiple_correct 8 · 기타 10 | – |
| 길이 repair 성공률 | 25/27 (92.6%) | – |
| 정답 위치 | A 26% · B 24% · C 26% · D 25% | 각 20~30% |
| 정답이 가장 긴 보기 | 33.8% (보조: 5% 넘게 긴 비율 7.9%) | 35% 이하 |
| 중복 제거 | 2 | – |
| 생성 모델 | us/sonnet-4-5 209 · global/sonnet-4-5 57 | – |
| 사람 표본 검토 | 38/38 OK, 오류 0 · 경미 0 | 오류 0~1건 |
| 런타임 `no_match` 비율 | 평가 중 출제 요청 2건 중 1건(요청 기준, 13번, 의도된 조합) | – |

## 9. round2(S5) 후보 제안 (DESIGN §9-4에서 2~3개)

1. **#2 오라우팅 보강** (10·12·14, 3건): 배분 기준에 "시험 운영 정보·설계 비교 질문은 concept_tutor" 명시 + 오라우팅 사례 예시. 가장 큰 효과 예상, 출처 표기율 100%로 회복 → **채택** (round2, 출처 표기율 100%)
2. **#4 TutorAnswer 형식 보강** (4·16, 2건): 보기 전체 해설·제약 빠짐없이 나열. faithfulness·답변 완결성 → **채택** (round2, 4·16 해소)
3. (선택) 응답 시간: 입력 가드 LLM 판별 비용 재검토 — DESIGN §9-4 목록 밖이므로 채택 시 DESIGN에 추가 → round2에서는 하지 않고 제출 후 v2-2에서 병렬화
