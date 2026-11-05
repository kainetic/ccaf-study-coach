// CCA-F 학습 코치 웹 UI 동작 (v2-5). src/api.py가 /static/app.js로 서빙, index.html 끝에서 불러온다(DOM이 먼저 있어야 함).
// 정답은 서버 코드(submit_answer)만 안다. 이 스크립트는 사용자가 고른 글자를 /query로 보낼 뿐이다.
const USER_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const EXAMPLES = [
  "Domain 2에서 연습 문제 하나 내줘",
  "TS 1.4 핵심 내용을 정리해줘",
  "시험까지 14일, 하루 2시간이야. 학습 계획 짜줘",
  "내 약점 도메인이 어디야?",
];
const $ = (id) => document.getElementById(id);
const log = $("log"), q = $("q"), send = $("send"), uid = $("uid");
let threadId = null, busy = false;
let history = [];               // 이번 대화 기록 (새로고침 복원용, sessionStorage)
const SESSION_KEY = "ccaf_session", HISTORY_MAX = 60;
const DEBUG = new URLSearchParams(location.search).get("debug") === "1";   // ?debug=1 → trace 표시 체크박스
const ACC_MIN = 10;             // 전체 정답률은 이만큼 풀어야 크게 보여 준다 (그 전에는 풀이 수 중심)
const sources = new Map();      // doc_id → 가이드 원문 (이번 대화에서 받은 contexts). 채점 턴은 출제 턴의 TS 청크를 쓴다

function store(key, value) {
  try { value === undefined ? (value = localStorage.getItem(key)) : localStorage.setItem(key, value); } catch (e) { return null; }
  return value;
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 코치 답변용 작은 마크다운: 이스케이프 후 코드블록·목록·제목·굵게·인라인 코드·근거 배지만 처리
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[근거:([^\]]+)\]/g, (_, body) => {
      // TS 번호마다 누르면 가이드 원문 발췌를 펼치는 버튼. TS 번호가 없으면 배지만
      const ids = body.match(/TS-\d+\.\d+/g);
      if (!ids) return `<span class="cite">근거:${body}</span>`;
      return '<span class="cite-row">근거: ' + ids.map((id) =>
        `<button type="button" class="cite" data-doc="${id}" aria-expanded="false" title="가이드 원문 보기">${id}</button>`).join(" ") + "</span>";
    });
}

function renderMarkdown(text) {
  const lines = esc(text).split(/\r?\n/);
  const out = [];
  let para = [], list = null, code = null;
  const flushPara = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>` + list.items.map((i) => `<li>${inline(i)}</li>`).join("") + `</${list.tag}>`); list = null; } };
  for (const line of lines) {
    if (code) {
      if (/^\s*```/.test(line)) { out.push("<pre><code>" + code.join("\n") + "</code></pre>"); code = null; }
      else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flushPara(); flushList(); code = []; continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/), h = line.match(/^#{1,4}\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push((ul || ol)[1]);
    } else if (h) {
      flushPara(); flushList(); out.push("<h3>" + inline(h[1]) + "</h3>");
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList(); para.push(line);
    }
  }
  if (code) out.push("<pre><code>" + code.join("\n") + "</code></pre>");
  flushPara(); flushList();
  return out.join("");
}

// 대화 기록을 탭 단위로 저장한다 (탭을 닫으면 사라짐). 용량이 넘치면 최근 기록만 남긴다
function saveSession() {
  for (const n of [HISTORY_MAX, 20, 6]) {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify({ user: uid.value.trim(), threadId, items: history.slice(-n) }));
      return;
    } catch (e) { /* 용량 초과·접근 불가 → 더 줄여서 재시도 */ }
  }
}

function record(item) {
  history.push(item);
  if (history.length > HISTORY_MAX) history = history.slice(-HISTORY_MAX);
  saveSession();
}

// 저장된 기록을 네트워크 호출 없이 다시 그린다 (문항 카드·채점 표시·근거 원문 포함)
function restoreSession() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); } catch (e) { saved = null; }
  if (!saved || saved.user !== uid.value.trim() || !Array.isArray(saved.items) || !saved.items.length) return;
  history = saved.items;
  threadId = saved.threadId || null;
  for (const it of history) {
    if (it.r === "u") { disableChoices(); addUser(it.t, it.pick); }
    else if (it.r === "b") { for (const c of it.d.contexts || []) sources.set(c.doc_id, c.text); addBot(it.d); }
    else if (it.r === "e") addError(it.t);
  }
  const note = document.createElement("div");
  note.className = "restored";
  note.textContent = "이전 대화를 복원했습니다. 서버가 다시 시작됐다면 풀던 문제는 이어지지 않으니 새로 요청해 주세요.";
  log.appendChild(note);
  scrollDown();
}

function newConversation() {
  threadId = null; activeQuiz = null; sources.clear(); history = [];
  try { sessionStorage.removeItem(SESSION_KEY); } catch (e) { /* 무시 */ }
  showIntro();
}

function scrollDown() { log.scrollTop = log.scrollHeight; }

// 보기를 눌러 답하면 "B. 보기 앞부분…" 한 줄로 보여 준다 (서버에는 글자만 보냄)
function addUser(text, isPick) {
  log.querySelector(".intro-hero")?.remove();     // 대화가 시작되면 웰컴 화면은 기록에 남기지 않는다
  const el = document.createElement("div");
  el.className = "msg user" + (isPick ? " pick" : "");
  el.textContent = text;
  if (isPick) el.title = text;
  log.appendChild(el);
  scrollDown();
}

function addError(text) {
  const el = document.createElement("div");
  el.className = "msg bot error";
  el.textContent = text;
  log.appendChild(el);
  scrollDown();
}

function traceSummary(data) {
  const t = data.trace || [];
  const items = [];
  const guard = t.find((s) => s.step === "guard");
  if (guard) items.push(`입력 가드: ${guard.action}${guard.rule ? ` (${guard.rule}, ${guard.layer})` : ""}`);
  const routes = t.filter((s) => s.step === "route").map((s) => s.agent);
  if (routes.length) items.push("경로: " + routes.join(" → "));
  const tools = (t.find((s) => s.step === "tools") || {}).names || [];
  if (tools.length) items.push("도구: " + tools.join(", "));
  const docs = (data.contexts || []).map((c) => c.doc_id);
  if (docs.length) items.push("근거 doc_id: " + docs.join(", "));
  const llm = t.find((s) => s.step === "llm");
  if (llm) items.push(`LLM ${llm.calls}회 · 입력 ${llm.input} / 출력 ${llm.output} 토큰`);
  const total = t.length ? Math.max(...t.map((s) => s.t || 0)) : null;
  const el = document.createElement("details");
  el.className = "trace";
  el.innerHTML = `<summary>trace${total != null ? ` · ${total.toFixed(1)}초` : ""}</summary><ul>` +
    items.map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
  return el;
}

// 출제 답변을 문항 앞부분 · 보기(A~F) · 뒷부분으로 나눈다. 보기가 2개 미만이면 null (일반 렌더링)
function splitQuiz(answer) {
  const lines = answer.split(/\r?\n/);
  const idx = [], options = [];
  lines.forEach((line, i) => {
    const m = line.match(/^\s*([A-F])[.)]\s+(.*)$/);
    if (m) { idx.push(i); options.push({ label: m[1], text: m[2] }); }
  });
  if (options.length < 2) return null;
  return { before: lines.slice(0, idx[0]).join("\n"), options,
           after: lines.slice(idx[idx.length - 1] + 1).join("\n") };
}

// 출제 머리줄 (quiz_master.format_quiz): **연습 문제** (Q-2.4-K3 · TS-2.4 · 시나리오 4: 제목)
const QUIZ_HEAD_RE = /^\*\*연습 문제\*\* \((Q-\S+) · (TS-(\d+)\.\d+) · 시나리오 (\d+): (.+)\)\s*$/;
const ABBREV_RE = /\b(e\.g|i\.e|etc|vs|Mr|Ms|Dr|U\.S)\.$/;

// 지문 → { context, question }. 마지막 문장이 "?"로 끝나고 그 앞에 문장 경계가 있을 때만 나눈다.
// 두 조각을 공백 하나로 이으면 원문과 정확히 같아야 하며, 아니면 null (나누지 않음)
function splitStem(stem) {
  if (!stem.endsWith("?")) return null;
  const re = /[.!?]["”')\]]?\s+(?=["“]?[A-Z])/g;
  let cut = -1, m;
  while ((m = re.exec(stem)) !== null) {
    if (ABBREV_RE.test(stem.slice(0, m.index + 1))) continue;
    cut = m.index + m[0].trimEnd().length;
  }
  if (cut <= 0) return null;
  const context = stem.slice(0, cut), question = stem.slice(cut).trimStart();
  if (question.length < 12 || question.length > stem.length * 0.7 || `${context} ${question}` !== stem) return null;
  return { context, question };
}

// 채점 텍스트 (quiz_master.format_grade): 머리줄 / **보기별 해설** / - **A (정답)**: … / [근거: …] / 안내
const GRADE_HEAD_RE = /^(정답입니다!|아쉽지만 오답입니다\.)/;
function parseGrade(answer) {
  const lines = answer.split(/\r?\n/);
  const head = lines.findIndex((l) => GRADE_HEAD_RE.test(l.trim()));
  const title = lines.findIndex((l) => l.trim() === "**보기별 해설**");
  if (head < 0 || title <= head) return null;
  const items = [];
  let i = title + 1;
  for (; i < lines.length; i++) {
    const m = lines[i].match(/^- \*\*([A-F])(?: \((정답|선택)\))?\*\*: (.*)$/);
    if (!m) break;
    items.push({ label: m[1], tag: m[2] || "", text: m[3] });
  }
  if (items.length < 2) return null;
  return { before: lines.slice(0, head).join("\n"), head: lines[head].trim(), items, after: lines.slice(i).join("\n") };
}

let activeQuiz = null;          // 답을 기다리는 문항 {el, cards, state}
const domainNames = {};         // 진도 데이터의 도메인 이름 (문항 배지 툴팁용)
const QUIZ_STATES = { open: "풀이 중", pending: "채점 중", ok: "정답", bad: "오답", closed: "종료" };
const OPT_STATES = { picked: "채점 중…", right: "✓ 정답", wrong: "✗ 내 선택" };

function setQuizState(quiz, state) {
  if (!quiz || !quiz.el) return;
  quiz.state = state;
  const pill = quiz.el.querySelector(".qh-status");
  if (pill) { pill.dataset.state = state; pill.textContent = QUIZ_STATES[state]; }
}

// 보기 카드 상태: 클래스 + 오른쪽 상태 표시(아이콘·텍스트, 색만으로 구분하지 않음)
function setOptState(card, state) {
  card.classList.remove("picked", "right", "wrong", "dim");
  if (state) card.classList.add(state);
  const tag = card.querySelector(".o-state");
  if (tag) tag.textContent = OPT_STATES[state] || "";
}

function disableChoices() {
  document.querySelectorAll(".choices .btn, .opt").forEach((b) => { b.disabled = true; });
}

function pick(label) {
  if (!activeQuiz || busy || activeQuiz.state !== "open") return;    // 채점 중 재선택·중복 제출 방지
  const card = activeQuiz.cards[label];
  if (!card) return;
  setOptState(card, "picked");
  setQuizState(activeQuiz, "pending");
  const text = (card.querySelector(".o-text") || {}).textContent || "";
  ask(label, text.trim() ? `${label}. ${text.trim().replace(/\s+/g, " ")}` : null);
}

// 답 제출이 오류로 끝나면(채점 응답 없음) 다시 고를 수 있게 되돌린다
function reopenQuiz() {
  if (!activeQuiz || activeQuiz.state !== "pending") return;
  Object.values(activeQuiz.cards).forEach((c) => { c.disabled = false; setOptState(c, null); });
  setQuizState(activeQuiz, "open");
}

function quizBlock(el, answer) {
  const q = splitQuiz(answer);
  if (!q) {      // 보기를 못 찾으면 예전처럼 A~D 버튼
    el.innerHTML = renderMarkdown(answer);
    const row = document.createElement("div");
    row.className = "choices";
    const cards = {};
    for (const label of ["A", "B", "C", "D"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.textContent = label;
      b.addEventListener("click", () => pick(label));
      row.appendChild(b);
      cards[label] = b;
    }
    el.appendChild(row);
    return cards;
  }
  // 머리줄을 찾으면 문제 카드(헤더·지문·Question), 못 찾으면 앞부분을 마크다운 그대로
  const lines = q.before.split(/\r?\n/);
  const hi = lines.findIndex((l) => QUIZ_HEAD_RE.test(l.trim()));
  if (hi >= 0) {
    const [, quizId, ts, dom, scen, scenTitle] = lines[hi].trim().match(QUIZ_HEAD_RE);
    const notice = lines.slice(0, hi).join("\n").trim();
    const stem = lines.slice(hi + 1).join("\n").trim();
    const parts = stem.includes("\n") ? null : splitStem(stem);
    el.classList.add("quiz");
    el.innerHTML = (notice ? `<div class="quiz-notice">${renderMarkdown(notice)}</div>` : "") + `
      <div class="quiz-head">
        <div class="qh-row"><span class="qh-kicker">연습 문제</span><span class="qh-id">${esc(quizId)}</span>
          <span class="qh-status" data-state="open">${QUIZ_STATES.open}</span></div>
        <div class="qh-row qh-meta">
          <span class="badge" title="${esc(domainNames[dom] || "")}">Domain ${esc(dom)}</span><span class="badge">${esc(ts)}</span>
          <span class="qh-scen">시나리오 ${esc(scen)} · ${esc(scenTitle)}</span></div>
      </div>
      <div class="quiz-body">` +
      (parts
        ? `<p class="stem">${inline(esc(parts.context))}</p>
           <div class="question"><span class="q-label">Question</span><p>${inline(esc(parts.question))}</p></div>`
        : `<div class="stem">${renderMarkdown(stem)}</div>`) + "</div>";
  } else {
    el.innerHTML = renderMarkdown(q.before);
  }
  const body = el.querySelector(".quiz-body") || el;
  const hint = document.createElement("p");
  hint.className = "opt-hint";
  hint.innerHTML = "보기를 눌러 답하세요 · 키보드 " + q.options.map((o) => `<kbd>${o.label}</kbd>`).join(" ");
  body.appendChild(hint);
  const box = document.createElement("div");
  box.className = "options";
  const cards = {};
  for (const o of q.options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "opt";
    b.innerHTML = `<span class="key">${o.label}</span><span class="o-text">${inline(esc(o.text))}</span><span class="o-state"></span>`;
    b.addEventListener("click", () => pick(o.label));
    box.appendChild(b);
    cards[o.label] = b;
  }
  body.appendChild(box);
  // "답을 A~D 중 하나로 알려 주세요" 안내는 카드 힌트와 겹치므로 뺀다
  const rest = q.after.split(/\r?\n/).filter((l) => !/^\s*답을\s*[A-F]~[A-F]/.test(l)).join("\n");
  if (rest.trim()) body.insertAdjacentHTML("beforeend", renderMarkdown(rest));
  return cards;
}

// 채점 결과: 직전 문항 카드에 내 선택(정답/오답)과 정답 표시. 정답 글자는 채점 텍스트("정답: X")에서만 읽는다
function markQuizGraded(submit, answer) {
  if (!activeQuiz) return;
  const correct = (answer.match(/정답\s*[:：]\s*([A-F])/) || [])[1];
  for (const [label, card] of Object.entries(activeQuiz.cards)) {
    card.disabled = true;
    if (label === submit.choice) setOptState(card, submit.correct ? "right" : "wrong");
    else if (label === correct) setOptState(card, "right");
    else setOptState(card, "dim");
  }
  setQuizState(activeQuiz, submit.correct ? "ok" : "bad");
}

// 예전 표시 (채점 텍스트 형식이 다를 때): 말풍선 머리줄 + 원문 마크다운
function markGraded(el, submit) {
  el.classList.add(submit.correct ? "graded-ok" : "graded-bad");
  const head = document.createElement("div");
  head.className = "verdict";
  head.textContent = submit.correct ? `✓ 정답 (선택 ${submit.choice})` : `✗ 오답 (선택 ${submit.choice})`;
  el.prepend(head);
}

// 채점 결과 배너 + 보기별 해설. 해설 문장은 서버 텍스트 그대로, 정답·내 선택 항목만 펼친다
function gradeBlock(el, g, submit) {
  const item = (it) => {
    const tags = [];
    if (it.tag === "정답") tags.push('<span class="x-tag ok">✓ 정답</span>');
    if (it.tag === "선택" || it.label === submit.choice) tags.push('<span class="x-tag mine">내 선택</span>');
    const cls = it.tag === "정답" ? " ok" : it.tag === "선택" ? " bad" : "";
    return `<div class="x-item${cls}"><span class="key">${it.label}</span>
      <div class="x-body"><div class="x-tags">${tags.join("")}</div><p>${inline(esc(it.text))}</p></div></div>`;
  };
  const main = g.items.filter((it) => it.tag || it.label === submit.choice);
  const others = g.items.filter((it) => !main.includes(it));
  el.classList.add("graded");
  el.innerHTML = (g.before.trim() ? renderMarkdown(g.before) : "") + `
    <div class="result ${submit.correct ? "ok" : "bad"}" role="status">
      <span class="r-ico" aria-hidden="true">${submit.correct ? "✓" : "✗"}</span><span class="r-text">${esc(g.head)}</span></div>
    <section class="explain"><h4>보기별 해설</h4>${main.map(item).join("")}
      ${others.length ? `<div class="more"><button type="button" class="more-toggle" aria-expanded="false">
        <svg class="ico chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>나머지 보기 해설 ${others.length}개</button>
        <div class="more-body"><div inert>${others.map(item).join("")}</div></div></div>` : ""}
    </section>` + (g.after.trim() ? `<div class="grade-foot">${renderMarkdown(g.after.trim())}</div>` : "");
}

// 채점 후 다음 행동 칩 (quiz_id "Q-2.4-K3" → TS-2.4, Domain 2)
function nextActions(el, submit) {
  const m = (submit.quiz_id || "").match(/^Q-(\d)\.(\d+)/);
  const items = [];
  if (m) {
    items.push([`Domain ${m[1]} 문제 하나 더`, `Domain ${m[1]}에서 연습 문제 하나 더 내줘`]);
    items.push(["다른 도메인 문제", `Domain ${m[1]} 말고 다른 도메인에서 연습 문제 하나 내줘`]);
    items.push([`TS ${m[1]}.${m[2]} 개념 설명`, `TS ${m[1]}.${m[2]} 핵심 개념을 설명해줘`]);
  } else {
    items.push(["다음 문제", "연습 문제 하나 더 내줘"]);
  }
  const row = document.createElement("div");
  row.className = "next";
  for (const [label, question] of items) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = label;
    b.addEventListener("click", () => ask(question));
    row.appendChild(b);
  }
  el.appendChild(row);
}

// 출제·채점 응답은 카드 머리(채점은 결과 헤더)가 화면 위에 오게 (긴 해설 끝으로 튀지 않게), 그 외는 맨 아래로
function scrollToMsg(el) { (el.querySelector(".result") || el).scrollIntoView({ block: "start", behavior: "smooth" }); }

function addBot(data) {
  const el = document.createElement("div");
  el.className = "msg bot";
  const answer = data.answer || "";
  const trace = data.trace || [];
  const submit = trace.find((s) => s.step === "submit" && s.status === "graded");
  const quizOk = trace.some((s) => s.step === "get_quiz" && s.status === "ok");
  if (submit) {
    const g = parseGrade(answer);
    if (g) gradeBlock(el, g, submit);
    else { el.innerHTML = renderMarkdown(answer); markGraded(el, submit); }
    markQuizGraded(submit, answer);
    nextActions(el, submit);
  } else {
    if (activeQuiz && activeQuiz.state !== "ok" && activeQuiz.state !== "bad") setQuizState(activeQuiz, "closed");
    if (!quizOk) el.innerHTML = renderMarkdown(answer);
  }
  activeQuiz = quizOk ? { el, cards: quizBlock(el, answer), state: "open" } : null;
  el.appendChild(traceSummary(data));
  log.appendChild(el);
  if (quizOk || submit) scrollToMsg(el); else scrollDown();
}

function setBusy(on) {
  busy = on;
  send.disabled = on;
  q.disabled = on;
  uid.disabled = on;
  document.querySelectorAll(".examples button, #progress [data-domain]").forEach((b) => { b.disabled = on; });
}

function currentUid() {
  const v = uid.value.trim();
  const ok = USER_ID_RE.test(v);
  uid.classList.toggle("invalid", !ok);
  return ok ? v : null;
}

async function ask(question, display) {
  question = question.trim();
  if (!question || busy) return;
  const user = currentUid();
  if (!user) { addError("user_id는 영문·숫자·_·-만 쓸 수 있습니다 (점 금지, 64자 이하)."); return; }
  disableChoices();
  addUser(display || question, !!display);
  record(display ? { r: "u", t: display, pick: true } : { r: "u", t: question });
  setBusy(true);
  // 스트리밍이 없어 실제 진행 단계는 모른다 → 경과 시간에 따른 일반 문구만 보여 준다
  const wait = document.createElement("div");
  wait.className = "msg bot skeleton";
  wait.setAttribute("role", "status");
  wait.innerHTML = '<div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="stage"></div>';
  const stage = wait.querySelector(".stage");
  const t0 = Date.now();
  const tick = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    const text = sec < 4 ? "질문을 확인하고 있어요"
      : sec < 30 ? "가이드를 찾아보고 답변을 정리하고 있어요 (보통 10~25초)"
      : "조금 더 걸리고 있어요. 잠시만 기다려 주세요";
    stage.textContent = `${text} · ${sec}초`;
  };
  tick();
  const timer = setInterval(tick, 1000);
  log.appendChild(wait);
  scrollDown();
  try {
    const res = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, user_id: user, thread_id: threadId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : `요청 실패 (HTTP ${res.status})`);
    }
    threadId = data.thread_id;
    for (const c of data.contexts || []) sources.set(c.doc_id, c.text);
    wait.remove();      // 답변 카드로 스크롤하기 전에 치워야 위치가 밀리지 않는다
    addBot(data);
    record({ r: "b", d: { answer: data.answer, trace: data.trace, contexts: data.contexts } });
    const graded = (data.trace || []).find((s) => s.step === "submit" && s.status === "graded");
    if (graded) loadProgress(+((graded.quiz_id || "").match(/^Q-(\d)\./) || [])[1] || null);
  } catch (e) {
    const msg = "오류: " + (e.message || "서버에 연결할 수 없습니다.");
    addError(msg);
    record({ r: "e", t: msg });
    reopenQuiz();
  } finally {
    clearInterval(timer);
    wait.remove();
    setBusy(false);
    q.focus();
  }
}

function pct(x) { return x == null ? "–" : Math.round(x * 100) + "%"; }

// 최근 학습일: 오늘 / 어제 / N일 전(일주일 안) / 11월 5일 (올해가 아니면 연도 포함)
function relDay(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) -
                           new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const md = `${d.getMonth() + 1}월 ${d.getDate()}일`;
  const label = days <= 0 ? "오늘" : days === 1 ? "어제" : days < 7 ? `${days}일 전`
    : d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}년 ${md}`;
  return { label, full: `${d.getFullYear()}년 ${md}` };
}

// 숫자가 바뀐 항목은 이전 값에서 짧게 카운트한다 (같은 프로필에서 다시 불러올 때만)
let lastCounts = { user: null, map: {} };
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
function num(key, value, suffix = "") {
  return `<span class="cnt" data-key="${key}" data-to="${value}" data-suffix="${suffix}">${value}${suffix}</span>`;
}
function countUp(el, from, to) {
  const t0 = performance.now(), dur = 700, suffix = el.dataset.suffix;
  el.classList.add("bump");
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), v = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
    el.textContent = v + suffix;
    if (k < 1) requestAnimationFrame(step); else setTimeout(() => el.classList.remove("bump"), 600);
  };
  requestAnimationFrame(step);
}
function animateCounts(box, user) {
  const prev = lastCounts.user === user ? lastCounts.map : null, map = {};
  box.querySelectorAll(".cnt").forEach((el) => {
    const key = el.dataset.key, to = +el.dataset.to;
    map[key] = to;
    if (prev && prev[key] != null && prev[key] !== to && !reduceMotion) countUp(el, prev[key], to);
  });
  lastCounts = { user, map };
}

const CHEVRON = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

// flashDomain: 방금 채점한 문제의 도메인 → 그 행을 잠깐 강조
async function loadProgress(flashDomain) {
  const box = $("progress");
  const user = currentUid();
  $("panelUser").textContent = user ? `· ${user}` : "";
  if (!user) { box.innerHTML = '<p class="note">user_id 형식이 올바르지 않습니다.</p>'; return; }
  try {
    const res = await fetch("/progress?user_id=" + encodeURIComponent(user));
    const p = await res.json();
    if (!res.ok || p.isError) throw new Error(p.message || p.detail || `HTTP ${res.status}`);
    // 약점 판정은 서버 get_weak_domains 결과(weak)를 그대로 쓴다 (UI에서 계산하지 않음)
    for (const d of p.domains) domainNames[d.domain] = d.name;
    const weak = p.weak && !p.weak.isError ? p.weak : null;
    const weakIds = new Set(weak ? weak.top.map((w) => w.domain) : []);
    const dis = busy ? " disabled" : "";
    // 막대 길이 = 시험 비중(가장 큰 도메인이 꽉 참), 채운 부분 = 그 도메인 정답률
    const maxW = Math.max(...p.domains.map((d) => d.weight)) || 1;
    const doms = p.domains.map((d) => {
      const tried = d.attempts > 0 && d.accuracy != null;
      return `
      <button type="button" class="dom${weakIds.has(d.domain) ? " weak" : ""}" data-domain="${d.domain}"${dis}
        title="Domain ${d.domain} 연습 문제 풀기 · 막대 길이 = 시험 비중 ${pct(d.weight)}, 채운 부분 = 정답률">
        <span class="row"><span class="d-name">D${d.domain} ${esc(d.name)}${weakIds.has(d.domain) ? '<span class="weak-tag">약점</span>' : ""}</span>
          ${tried ? `<span class="d-cnt">${d.correct}/${d.attempts}</span><span class="d-acc">${num("d" + d.domain, Math.round(d.accuracy * 100), "%")}</span>`
                  : '<span class="d-none">아직 안 풂</span>'}</span>
        <span class="row bar-row"><span class="lane"><span class="track" style="width:${(d.weight / maxW) * 100}%"><span class="fill" style="width:${tried ? d.accuracy * 100 : 0}%"></span></span></span>
          <span class="d-weight">비중 ${pct(d.weight)}</span><span class="go"><span class="go-text">문제 풀기</span>${CHEVRON}</span></span>
      </button>`;
    }).join("");
    let weakBox = "";
    if (weak && weak.top.length) {
      const names = weak.top.map((w) => `D${w.domain} (정답률 ${pct(w.accuracy)})`).join(", ");
      weakBox = `<div class="weak-box"><span class="w-title">약점 도메인</span>${esc(names)}
        <button type="button" class="btn primary" id="weakQuiz" data-domain="${weak.top[0].domain}"${dis}>D${weak.top[0].domain} 약점 문제 풀기</button></div>`;
    } else if (weak) {
      weakBox = `<div class="weak-box note">${esc(weak.message || "약점을 판단할 기록이 부족합니다.")}</div>`;
    }
    const kpi = (label, value, sub, cls = "", extra = "") =>
      `<div class="kpi${cls}"><div class="k-label">${label}</div><div class="k-value">${value}</div>${extra}<div class="k-sub">${sub}</div></div>`;
    const meter = (c) => `<div class="k-bar"><span style="width:${c.total ? (c.solved / c.total) * 100 : 0}%"></span></div>`;
    // 기록이 적을 때는 정답률 대신 풀이 수를 보여 준다. 강조색은 진척(풀이 수)·좋은 정답률(70% 이상)에만
    const first = p.attempts < ACC_MIN
      ? kpi("풀이 완료", `${num("attempts", p.attempts)}<small>문제</small>`,
            p.attempts ? `정답률은 ${ACC_MIN}문제부터` : "첫 문제를 풀어 보세요", p.attempts ? " primary" : "")
      : kpi("정답률", num("acc", Math.round(p.accuracy * 100), "%"), `${p.correct}/${p.attempts} 정답`,
            p.accuracy >= 0.7 ? " primary" : "");
    const last = p.last_answered_at ? relDay(p.last_answered_at) : null;
    box.innerHTML = `
      <div class="kpis">
        ${first}
        ${kpi("TS 커버리지", `${num("ts", p.task_coverage.solved)}<small>/${p.task_coverage.total}</small>`, "푼 Task Statement", "", meter(p.task_coverage))}
        ${kpi("세부 항목 커버리지", `${num("bullet", p.bullet_coverage.solved)}<small>/${p.bullet_coverage.total}</small>`, "다룬 세부 항목", "", meter(p.bullet_coverage))}
        ${last ? kpi("최근 학습일", esc(last.label), esc(last.full)) : kpi("최근 학습일", '<span class="k-none">기록 없음</span>', "&nbsp;")}
      </div>${weakBox}
      <div class="section-title">도메인별 진도
        <span class="info" tabindex="0" aria-label="도메인별 진도 설명" aria-describedby="domTip">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>
          <span class="tip" role="tooltip" id="domTip">막대 길이는 시험 비중, 채운 부분은 정답률이에요. 도메인을 누르면 그 도메인 문제를 냅니다.
            <span class="tip-sub">약점 점수 = 시험 비중 × (1 − 정답률), 시도 3회 이상 도메인만 (서버 계산)</span></span></span>
      </div>
      ${doms}`;
    box.querySelectorAll("[data-domain]").forEach((b) => b.addEventListener("click", () => {
      ask(`Domain ${b.dataset.domain}에서 연습 문제 하나 내줘`);
    }));
    animateCounts(box, user);
    const row = flashDomain && box.querySelector(`.dom[data-domain="${flashDomain}"]`);
    if (row) {
      // scrollIntoView를 또 쓰면 채팅 쪽 부드러운 스크롤이 끊기므로 패널만 직접 움직인다
      const r = row.getBoundingClientRect(), b = box.getBoundingClientRect();
      if (r.top < b.top || r.bottom > b.bottom) box.scrollTo({ top: box.scrollTop + r.top - b.top - 16, behavior: "smooth" });
      row.classList.add("flash");
      row.addEventListener("animationend", () => row.classList.remove("flash"), { once: true });
    }
  } catch (e) {
    box.innerHTML = `<p class="note">진도를 불러오지 못했습니다: ${esc(e.message || "")}</p>`;
  }
}

// 초기 화면 기능 카드: EXAMPLES와 같은 순서. 누르면 예시 질문을 그대로 보낸다 (추천 질문 동작)
const FEATURES = [
  { title: "연습 문제", desc: "검토를 마친 문제 풀에서 출제하고 코드로 채점해요.",
    icon: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>' },
  { title: "개념 Q&A", desc: "시험 가이드 원문을 근거로 개념을 설명해요.",
    icon: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>' },
  { title: "학습 계획", desc: "남은 기간과 하루 공부 시간으로 도메인별 계획을 짜요.",
    icon: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>' },
  { title: "약점 분석", desc: "풀이 기록으로 시험 비중 대비 약한 도메인을 찾아요.",
    icon: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>' },
];

function showIntro() {
  log.innerHTML = "";
  const el = document.createElement("div");
  el.className = "intro-hero";
  el.innerHTML = `<p class="eyebrow">CCA-F 시험 가이드 기반 학습 코치</p>
    <h2>무엇을 공부해 볼까요?</h2>
    <p>개념 질문, 연습 문제 출제·채점, 학습 계획, 약점 분석을 도와드려요. 아래 예시를 누르거나 직접 질문해 보세요.</p>`;
  const ex = document.createElement("div");
  ex.className = "examples";
  EXAMPLES.forEach((text, i) => {
    const f = FEATURES[i];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "feature";
    b.title = `“${text}” 질문하기`;
    b.innerHTML = `<span class="f-head"><span class="f-ico"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${f.icon}</svg></span>
      <span class="f-title">${esc(f.title)}</span></span><span class="f-desc">${esc(f.desc)}</span>
      <span class="f-ex"><span class="f-ex-text">${esc(text)}</span><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span>`;
    b.addEventListener("click", () => ask(text));
    ex.appendChild(b);
  });
  el.appendChild(ex);
  log.appendChild(el);
}

$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = q.value;
  q.value = "";
  autosize();
  ask(text);
});
function autosize() {
  if (!q.value) { q.style.height = ""; return; }      // 비면 CSS 기본 높이(44px)로
  q.style.height = "0px";       // auto는 flex 행 높이로 늘어나 측정이 틀어진다
  q.style.height = Math.max(44, Math.min(q.scrollHeight + 2, 160)) + "px";
}
q.addEventListener("input", autosize);
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("ask").requestSubmit(); }
});
// 답을 기다리는 문항이 있으면 입력창 밖에서 A~F 키로 고른다
document.addEventListener("keydown", (e) => {
  if (!activeQuiz || busy || e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName)) return;
  const label = e.key.toUpperCase();
  if (activeQuiz.cards[label]) { e.preventDefault(); pick(label); }
});
$("reset").addEventListener("click", () => {
  if (!busy) { newConversation(); q.focus(); }
});
$("refresh").addEventListener("click", () => loadProgress());

// 진도 패널 접기/펼치기 (상태는 브라우저에 기억)
const togglePanel = $("togglePanel");
function setPanel(open) {
  document.body.classList.toggle("panel-collapsed", !open);
  togglePanel.setAttribute("aria-expanded", String(open));
  store("ccaf_panel_open", open ? "1" : "0");
}
togglePanel.addEventListener("click", () => {
  const open = document.body.classList.contains("panel-collapsed");
  setPanel(open);
  if (open) loadProgress();
});
setPanel(store("ccaf_panel_open") !== "0");

// 근거 배지 → 가이드 원문 발췌를 말풍선 안에 펼치고, 같은 배지를 다시 누르면 접는다
log.addEventListener("click", (e) => {
  // 채점 카드의 "나머지 보기 해설" 아코디언 (높이 전환은 CSS)
  const more = e.target.closest(".more-toggle");
  if (more) {
    const open = more.getAttribute("aria-expanded") !== "true";
    more.setAttribute("aria-expanded", String(open));
    more.parentElement.classList.toggle("open", open);
    more.nextElementSibling.firstElementChild.inert = !open;
    return;
  }
  const btn = e.target.closest("button.cite[data-doc]");
  if (!btn) return;
  const msg = btn.closest(".msg");
  const id = btn.dataset.doc;
  const open = msg.querySelector(".source");
  msg.querySelectorAll("button.cite").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (open) {
    const same = open.dataset.doc === id;
    open.remove();
    if (same) return;
  }
  const box = document.createElement("div");
  box.className = "source";
  box.dataset.doc = id;
  const text = sources.get(id);
  box.innerHTML = `<div class="src-head">${esc(id)} · 시험 가이드 원문</div>` + (text
    ? esc(text)
    : '<div class="src-note">이 대화에서 받은 검색 결과에 원문이 없습니다. 원문이 필요하면 이 TS를 질문해 보세요.</div>');
  (btn.closest("p, ul, ol") || btn.closest(".cite-row")).after(box);
  btn.setAttribute("aria-expanded", "true");
});

// trace 체크박스와 카드의 trace 토글은 개발 모드(?debug=1)에서만
document.body.classList.toggle("debug", DEBUG);
const showTrace = $("showTrace");
showTrace.checked = DEBUG && store("ccaf_show_trace") === "1";
document.body.classList.toggle("show-trace", showTrace.checked);
showTrace.addEventListener("change", () => {
  document.body.classList.toggle("show-trace", showTrace.checked);
  store("ccaf_show_trace", showTrace.checked ? "1" : "0");
});
uid.addEventListener("change", () => {
  if (currentUid()) { store("ccaf_user_id", uid.value.trim()); newConversation(); }
  loadProgress();
});

// 프로필은 선택 메뉴에 있는 값만 (예전 자유 입력으로 저장된 값이 메뉴에 없으면 기본 web_demo)
const savedUser = store("ccaf_user_id");
uid.value = [...uid.options].some((o) => o.value === savedUser) ? savedUser : "web_demo";
showIntro();
restoreSession();
loadProgress();
