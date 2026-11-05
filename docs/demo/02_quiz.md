# 시연 02 · 출제 → 채점

> quiz_master가 사전 생성 풀에서 출제(정답은 LLM 컨텍스트에 없음)하고, 같은 thread에서 답을 받아 코드로 채점·기록한다.
>
> 생성: 2026-11-05 08:59 · 커밋 73f600c · user_id `demo_heavy` · `python scripts/demo_capture.py --only 02`로 재생성 (LLM 답변이라 실행마다 문장은 달라질 수 있음)

## 턴 1

**질문**: Domain 2에서 연습 문제 하나 내줘

**답변**

```text
**연습 문제** (Q-2.4-K3 · TS-2.4 · 시나리오 4: Developer Productivity with Claude)

Your development agent is configured with three MCP servers in .mcp.json: a GitHub server, a Jira server, and a custom documentation server. During agent startup, the logs show successful connections to all three servers and list 8 tools from GitHub, 5 tools from Jira, and 3 tools from the documentation server. A developer then asks the agent to "create a PR, link it to JIRA-456, and update the wiki," and the agent successfully completes all three tasks using tools from all three servers. What does this scenario demonstrate about MCP tool availability?

A. Each MCP server activates its tools independently when the agent makes the first call to any tool from that server.
B. Tools from all configured MCP servers are discovered at connection time and available simultaneously to the agent throughout the session.
C. The agent dynamically loads MCP server tools on demand when it encounters relevant keywords in user requests during the session.
D. The agent queries each configured MCP server sequentially during request processing to discover which tools are currently available for that request.

답을 A~D 중 하나로 알려 주세요. (문제는 실제 시험과 같은 영어, 해설은 한국어)
```

**trace 요약** (응답 11.8초, thread `9b04e5e6`)

- guard: action=pass rule=None layer=llm
- route → quiz_master
- get_quiz: status=ok quiz_id=Q-2.4-K3 task_id=TS-2.4
- output_check: rule=G4
- 호출 도구: get_quiz
- LLM 호출 3회 · 입력 6647 / 출력 98 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 3}

contexts doc_id: TS-2.4

## 턴 2

**질문**: B

**답변**

```text
정답입니다! (정답: B)

**보기별 해설**
- **A**: MCP 서버는 첫 도구 호출 시점에 도구를 활성화하지 않습니다. 시작 로그가 에이전트가 어떤 도구도 호출하기 전에 이미 세 서버의 모든 도구(16개)를 나열한 것은, 도구 발견이 연결 시점에 완료되었음을 명확히 보여줍니다.
- **B (정답)**: 연결 시점(connection time)에 구성된 모든 MCP 서버의 도구가 발견되며, 에이전트는 세션 동안 이들을 동시에 사용할 수 있습니다. 시나리오에서 시작 로그가 세 서버 모두에서 도구를 나열하고, 에이전트가 단일 요청에서 세 서버의 도구를 모두 성공적으로 사용한 것은 이 메커니즘을 정확히 보여줍니다.
- **C**: MCP 도구는 요청 중 키워드에 반응하여 동적으로 로드되지 않습니다. 모든 구성된 서버의 도구는 연결 시점에 미리 발견되어 에이전트에게 제공됩니다. 시작 로그가 이미 모든 도구를 나열한 것은 사전 발견(pre-discovery)을 보여주며, 온디맨드 로딩 모델과 모순됩니다.
- **D**: 에이전트는 요청 처리 중에 MCP 서버를 순차적으로 쿼리하여 도구를 발견하지 않습니다. 시작 로그가 사용자 요청 전에 이미 모든 도구를 나열했으므로, 도구 발견은 연결 시점에 한 번 수행되며 각 요청마다 반복되지 않습니다.

[근거: TS-2.4]
다음 문제를 원하면 말씀해 주세요.
```

**trace 요약** (응답 10.6초, thread `9b04e5e6`)

- guard: action=pass rule=None layer=llm
- route → quiz_master
- submit_answer: status=graded quiz_id=Q-2.4-K3 choice=B correct=True
- output_check: rule=G4
- 호출 도구: submit_answer
- LLM 호출 3회 · 입력 8512 / 출력 98 토큰 · 모델 {'us/sonnet-4-5@us-east-1': 3}
