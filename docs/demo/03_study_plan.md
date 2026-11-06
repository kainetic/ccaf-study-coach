# 시연 03 · 진도·약점 → 학습 계획

> study_coach가 MCP 풀이 기록으로 약점을 계산하고, 시간 배분은 코드(allocate_study_time), 일자별 계획은 Plan-Execute로 만든다.
>
> 생성: 2026-11-06 09:01 · 커밋 a1345cd · user_id `demo_heavy`(풀이 기록은 시드 상태에서 시작) · 모든 턴이 같은 대화(thread)에서 이어짐
> `python scripts/demo_capture.py --only 03`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: 내 진도랑 약점 도메인 알려줘

**답변**

```text
## 📊 진도 현황

- **전체**: 61문제 시도 / 39문제 정답 → **정답률 63.9%**
- **세부 항목 커버리지**: 68/240 (28.3%)
- **TS 커버리지**: 30/30 (모든 TS 시도 완료)

### 도메인별 성적
1. **Agentic Architecture & Orchestration** (비중 27%) - 16시도 / 12정답 / 정답률 75.0%
2. **Tool Design & MCP Integration** (비중 18%) - 11시도 / 5정답 / 정답률 45.5%
3. **Claude Code Configuration & Workflows** (비중 20%) - 12시도 / 10정답 / 정답률 83.3%
4. **Prompt Engineering & Structured Output** (비중 20%) - 12시도 / 8정답 / 정답률 66.7%
5. **Context Management & Reliability** (비중 15%) - 10시도 / 4정답 / 정답률 40.0%

---

## ⚠️ 약점 도메인 (상위 2개)

1. **Tool Design & MCP Integration** (도메인 2)
   - 정답률: 45.5% / 시도: 11회 / 약점 점수: 0.098

2. **Context Management & Reliability** (도메인 5)
   - 정답률: 40.0% / 시도: 10회 / 약점 점수: 0.090

이 두 영역을 집중적으로 보강하시면 좋겠습니다!
```

**trace 요약** (응답 12.3초)

- 입력 가드: 통과 (LLM 판별)
- 배분: study_coach
- get_progress: 풀이 61회
- get_weak_domains: 약점 D2, D5
- G4 출력 검사: 위반 없음
- 호출 도구: get_progress, get_weak_domains
- LLM 호출 4회 (입력 가드 판별 제외) · 토큰 입력 11810 / 출력 591 · global/sonnet-4-5@ap-northeast-2 ×4

## 턴 2

**질문**: 시험까지 7일 남았고 하루 2시간 공부할 수 있어. 학습 계획 짜줘

**답변**

```text
**학습 계획** (7일 × 2시간 = 총 14시간)

**약점 근거** (풀이 기록 기준, 약점 점수 = 시험 비중 × (1 − 정답률))
| 순위 | 도메인 | 비중 | 시도 | 정답률 | 약점 점수 |
|---|---|---|---|---|---|
| 1 | D2 Tool Design & MCP Integration | 18% | 11 | 46% | 0.098 |
| 2 | D5 Context Management & Reliability | 15% | 10 | 40% | 0.090 |
| 3 | D1 Agentic Architecture & Orchestration | 27% | 16 | 75% | 0.068 |
| 4 | D4 Prompt Engineering & Structured Output | 20% | 12 | 67% | 0.067 |
| 5 | D3 Claude Code Configuration & Workflows | 20% | 12 | 83% | 0.033 |

**시간 배분** (시간 몫 ∝ 시험 비중 × 약점 계수 (시도 3회 이상: max(0.2, 1 − 정답률), 미만: 1.0), 0.5시간 단위 최대 잔여법)
| 도메인 | 비중 | 약점 계수 | 시간 |
|---|---|---|---|
| D1 Agentic Architecture & Orchestration | 27% | 0.25 | 2.5h |
| D2 Tool Design & MCP Integration | 18% | 0.55 | 4.0h |
| D3 Claude Code Configuration & Workflows | 20% | 0.20 | 1.5h |
| D4 Prompt Engineering & Structured Output | 20% | 0.33 | 2.5h |
| D5 Context Management & Reliability | 15% | 0.60 | 3.5h |
| **합계** | | | **14.0h** |

**일자별 계획**
- **1일차** (2.0h): TS-2.3 1.0h — Distribute tools appropriately across agents and configure tool choice (남은 문제 7개) · TS-5.1 1.0h — Manage conversation context to preserve critical information across long interactions (남은 문제 9개) · 실습 EX-2
- **2일차** (2.0h): TS-1.2 0.5h — Orchestrate multi-agent systems with coordinator-subagent patterns (남은 문제 7개) · TS-1.5 0.5h — Apply Agent SDK hooks for tool call interception and data normalization (남은 문제 5개) · TS-4.1 0.5h — Design prompts with explicit criteria to improve precision and reduce false positives (남은 문제 5개) · TS-4.3 0.5h — Enforce structured output using tool use and JSON schemas (남은 문제 9개) · 실습 EX-1
- **3일차** (2.0h): TS-3.4 0.5h — Determine when to use plan mode vs direct execution (남은 문제 7개) · TS-3.5 0.5h — Apply iterative refinement techniques for progressive improvement (남은 문제 8개) · TS-2.4 1.0h — Integrate MCP servers into Claude Code and agent workflows (남은 문제 7개) · 실습 EX-1
- **4일차** (2.0h): TS-5.2 1.0h — Design effective escalation and ambiguity resolution patterns (남은 문제 7개) · TS-1.3 0.5h — Configure subagent invocation, context passing, and spawning (남은 문제 6개) · TS-1.6 0.5h — Design task decomposition strategies for complex workflows (남은 문제 4개) · 실습 EX-3
- **5일차** (2.0h): TS-4.4 0.5h — Implement validation, retry, and feedback loops for extraction quality (남은 문제 7개) · TS-4.5 0.5h — Design efficient batch processing strategies (남은 문제 7개) · TS-2.1 0.5h — Design effective tool interfaces with clear descriptions and boundaries (남은 문제 7개) · TS-2.5 0.5h — Select and apply built-in tools (Read, Write, Edit, Bash, Grep, Glob) effectively (남은 문제 8개) · 실습 EX-3
- **6일차** (2.0h): TS-5.3 0.5h — Implement error propagation strategies across multi-agent systems (남은 문제 7개) · TS-5.4 0.5h — Manage context effectively in large codebase exploration (남은 문제 8개) · TS-3.1 0.5h — Configure CLAUDE.md files with appropriate hierarchy, scoping, and modular organization (남은 문제 7개) · TS-1.1 0.5h — Design and implement agentic loops for autonomous task execution (남은 문제 5개) · 실습 EX-1
- **7일차** (2.0h): TS-4.2 0.5h — Apply few-shot prompting to improve output consistency and quality (남은 문제 8개) · TS-2.2 0.5h — Implement structured error responses for MCP tools (남은 문제 7개) · TS-2.4 0.5h — Integrate MCP servers into Claude Code and agent workflows (남은 문제 7개) · TS-5.5 0.5h — Design human review workflows and confidence calibration (남은 문제 6개) · 실습 EX-3

**연결 Preparation Exercise** (가이드의 Domains reinforced 기준)
- EX-1 Build a Multi-Tool Agent with Escalation Logic — D1, D2, D5
- EX-2 Configure Claude Code for a Team Development Workflow — D3, D2
- EX-3 Build a Structured Data Extraction Pipeline — D4, D5

각 TS 문제는 "TS-2.1 문제 내줘"처럼 요청하면 사전 검토된 풀에서 출제합니다.
```

**trace 요약** (응답 12.7초)

- 입력 가드: 통과 (LLM 판별)
- 배분: study_coach
- get_weak_domains: 약점 D2, D5
- allocate_study_time (코드 계산): 합계 14.0h — D1 2.5h · D2 4.0h · D3 1.5h · D4 2.5h · D5 3.5h
- make_study_plan: plan → execute → finalize · 재계획 0회 · LLM 계획 검증 통과
- G4 출력 검사: 위반 없음
- 호출 도구: get_weak_domains, allocate_study_time, make_study_plan
- LLM 호출 4회 (입력 가드 판별 제외) · 토큰 입력 10145 / 출력 989 · global/sonnet-4-5@ap-northeast-2 ×4
