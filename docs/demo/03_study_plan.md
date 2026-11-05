# 시연 03 · 진도·약점 → 학습 계획

> study_coach가 MCP 풀이 기록으로 약점을 계산하고, 시간 배분은 코드(allocate_study_time), 일자별 계획은 Plan-Execute로 만든다.
>
> 생성: 2026-11-05 09:00 · 커밋 73f600c · user_id `demo_heavy` · `python scripts/demo_capture.py --only 03`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: 내 진도랑 약점 도메인 알려줘

**답변**

```text
## 📊 진도 현황

- **전체 시도**: 62문제 / 정답 40개 / **정답률 64.5%**
- **세부 항목 커버리지**: 69/240 (28.8%)
- **TS 커버리지**: 30/30 (모든 TS 시도 완료)

### 도메인별 정답률
1. **Agentic Architecture & Orchestration** (27%) - 16시도 / 12정답 / 75.0%
2. **Tool Design & MCP Integration** (18%) - 12시도 / 6정답 / 50.0%
3. **Claude Code Configuration & Workflows** (20%) - 12시도 / 10정답 / 83.3%
4. **Prompt Engineering & Structured Output** (20%) - 12시도 / 8정답 / 66.7%
5. **Context Management & Reliability** (15%) - 10시도 / 4정답 / 40.0%

---

## ⚠️ 약점 도메인 (상위 2개)

1. **Tool Design & MCP Integration** (18% 비중)
   - 정답률: 50.0% / 시도: 12회

2. **Context Management & Reliability** (15% 비중)
   - 정답률: 40.0% / 시도: 10회

이 두 도메인이 비중 대비 정답률이 가장 낮아 집중 학습이 필요합니다.
```

**trace 요약** (응답 20.8초, thread `18a9e46b`)

- guard: action=pass rule=None layer=llm
- route → study_coach
- progress: {'tool': 'get_progress', 'status': 'ok', 'attempts': 62}
- progress: {'tool': 'get_weak_domains', 'status': 'ok', 'top': ['D2', 'D5']}
- output_check: rule=G4
- 호출 도구: get_progress, get_weak_domains
- LLM 호출 4회 · 입력 13664 / 출력 537 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 4}

## 턴 2

**질문**: 시험까지 7일 남았고 하루 2시간 공부할 수 있어. 학습 계획 짜줘

**답변**

```text
**학습 계획** (7일 × 2시간 = 총 14시간)

**약점 근거** (풀이 기록 기준, 약점 점수 = 시험 비중 × (1 − 정답률))
| 순위 | 도메인 | 비중 | 시도 | 정답률 | 약점 점수 |
|---|---|---|---|---|---|
| 1 | D2 Tool Design & MCP Integration | 18% | 12 | 50% | 0.090 |
| 2 | D5 Context Management & Reliability | 15% | 10 | 40% | 0.090 |
| 3 | D1 Agentic Architecture & Orchestration | 27% | 16 | 75% | 0.068 |
| 4 | D4 Prompt Engineering & Structured Output | 20% | 12 | 67% | 0.067 |
| 5 | D3 Claude Code Configuration & Workflows | 20% | 12 | 83% | 0.033 |

**시간 배분** (시간 몫 ∝ 시험 비중 × 약점 계수 (시도 3회 이상: max(0.2, 1 − 정답률), 미만: 1.0), 0.5시간 단위 최대 잔여법)
| 도메인 | 비중 | 약점 계수 | 시간 |
|---|---|---|---|
| D1 Agentic Architecture & Orchestration | 27% | 0.25 | 3.0h |
| D2 Tool Design & MCP Integration | 18% | 0.50 | 3.5h |
| D3 Claude Code Configuration & Workflows | 20% | 0.20 | 1.5h |
| D4 Prompt Engineering & Structured Output | 20% | 0.33 | 2.5h |
| D5 Context Management & Reliability | 15% | 0.60 | 3.5h |
| **합계** | | | **14.0h** |

**일자별 계획**
- **1일차** (2.0h): TS-2.3 1.0h — 도메인별 도구 분배 전략과 tool choice 설정 방법 (남은 문제 7개) · TS-5.1 1.0h — 긴 대화에서 중요 정보를 보존하는 컨텍스트 관리 기법 (남은 문제 9개) · 실습 EX-2
- **2일차** (2.0h): TS-1.2 1.0h — coordinator-subagent 패턴을 활용한 멀티 에이전트 오케스트레이션 (남은 문제 7개) · TS-4.1 1.0h — 명시적 기준을 통한 정밀도 향상과 false positive 감소 (남은 문제 5개) · 실습 EX-1
- **3일차** (2.0h): TS-3.4 0.5h — plan mode와 direct execution 선택 기준 (남은 문제 7개) · TS-3.5 0.5h — 점진적 개선을 위한 반복적 개선 기법 (남은 문제 8개) · TS-2.1 1.0h — 명확한 설명과 경계를 가진 효과적인 도구 인터페이스 설계 (남은 문제 7개) · 실습 EX-1
- **4일차** (2.0h): TS-5.2 1.0h — 에스컬레이션과 모호성 해결 패턴 설계 (남은 문제 7개) · TS-1.5 1.0h — Agent SDK 훅을 활용한 도구 호출 가로채기와 데이터 정규화 (남은 문제 5개) · 실습 EX-3
- **5일차** (2.0h): TS-4.3 0.5h — tool use와 JSON 스키마를 활용한 구조화된 출력 강제 (남은 문제 9개) · TS-4.4 0.5h — 추출 품질을 위한 검증, 재시도, 피드백 루프 구현 (남은 문제 7개) · TS-2.4 1.0h — Claude Code와 에이전트 워크플로우에 MCP 서버 통합 (남은 문제 6개) · 실습 EX-4
- **6일차** (2.0h): TS-5.3 0.5h — 멀티 에이전트 시스템 전반의 오류 전파 전략 (남은 문제 7개) · TS-5.4 0.5h — 대규모 코드베이스 탐색에서의 효과적인 컨텍스트 관리 (남은 문제 8개) · TS-3.1 0.5h — CLAUDE.md 파일의 계층 구조, 스코핑, 모듈화 구성 (남은 문제 7개) · TS-1.3 0.5h — 서브에이전트 호출, 컨텍스트 전달, 생성 설정 (남은 문제 6개) · 실습 EX-1
- **7일차** (2.0h): TS-1.6 0.5h — 복잡한 워크플로우를 위한 작업 분해 전략 설계 (남은 문제 4개) · TS-4.5 0.5h — 효율적인 배치 처리 전략 설계 (남은 문제 7개) · TS-2.5 0.5h — 내장 도구(Read, Write, Edit, Bash, Grep, Glob)의 효과적 선택과 적용 (남은 문제 8개) · TS-5.5 0.5h — 휴먼 리뷰 워크플로우와 신뢰도 보정 설계 (남은 문제 6개) · 실습 EX-4

**연결 Preparation Exercise** (가이드의 Domains reinforced 기준)
- EX-1 Build a Multi-Tool Agent with Escalation Logic — D1, D2, D5
- EX-2 Configure Claude Code for a Team Development Workflow — D3, D2
- EX-3 Build a Structured Data Extraction Pipeline — D4, D5
- EX-4 Design and Debug a Multi-Agent Research Pipeline — D1, D2, D5

각 TS 문제는 "TS-2.1 문제 내줘"처럼 요청하면 사전 검토된 풀에서 출제합니다.
```

**trace 요약** (응답 44.3초, thread `18a9e46b`)

- guard: action=pass rule=None layer=llm
- route → study_coach
- progress: {'tool': 'get_weak_domains', 'status': 'ok', 'top': ['D2', 'D5']}
- allocate: {'total_hours': 14.0, 'hours': {'D1': 3.0, 'D2': 3.5, 'D3': 1.5, 'D4': 2.5, 'D5': 3.5}}
- plan: {'events': ['plan', 'execute', 'finalize'], 'revisions': 0, 'fallback': False, 'allocation': {'D1': 3.0, 'D2': 3.5, 'D3': 1.5, 'D4': 2.5, 'D5': 3.5}, 'total_hours': 14.0, 'weak_top': ['D2', 'D5']}
- output_check: rule=G4
- 호출 도구: get_weak_domains, allocate_study_time, make_study_plan
- LLM 호출 5회 · 입력 21533 / 출력 3608 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 5}
