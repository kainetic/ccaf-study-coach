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
    if (it.r === "u") { disableChoices(); addUser(it.t); }
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

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
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

let activeQuiz = null;          // 답을 기다리는 문항 카드 묶음 {cards}

function disableChoices() {
  document.querySelectorAll(".choices .btn, .opt").forEach((b) => { b.disabled = true; });
}

function pick(label) {
  if (!activeQuiz || busy) return;
  const card = activeQuiz.cards[label];
  if (!card) return;
  card.classList.add("picked");
  ask(label);
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
  el.innerHTML = renderMarkdown(q.before);
  const hint = document.createElement("p");
  hint.className = "opt-hint";
  hint.textContent = "보기를 눌러 답하세요 · 키보드 " + q.options.map((o) => o.label).join("/");
  el.appendChild(hint);
  const box = document.createElement("div");
  box.className = "options";
  const cards = {};
  for (const o of q.options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "opt";
    b.innerHTML = `<span class="key">${o.label}</span><span>${inline(esc(o.text))}</span>`;
    b.addEventListener("click", () => pick(o.label));
    box.appendChild(b);
    cards[o.label] = b;
  }
  el.appendChild(box);
  // "답을 A~D 중 하나로 알려 주세요" 안내는 카드 힌트와 겹치므로 뺀다
  const rest = q.after.split(/\r?\n/).filter((l) => !/^\s*답을\s*[A-F]~[A-F]/.test(l)).join("\n");
  if (rest.trim()) el.insertAdjacentHTML("beforeend", renderMarkdown(rest));
  return cards;
}

// 채점 결과: 말풍선 머리줄 + 직전 문항 카드에 내 선택(정답/오답)과 정답 표시
function markGraded(el, submit, answer) {
  el.classList.add(submit.correct ? "graded-ok" : "graded-bad");
  const head = document.createElement("div");
  head.className = "verdict";
  head.textContent = submit.correct ? `✓ 정답 (선택 ${submit.choice})` : `✗ 오답 (선택 ${submit.choice})`;
  el.prepend(head);
  if (!activeQuiz) return;
  const correct = (answer.match(/정답\s*[:：]\s*([A-F])/) || [])[1];
  const mine = activeQuiz.cards[submit.choice];
  if (mine) { mine.classList.remove("picked"); mine.classList.add(submit.correct ? "right" : "wrong"); }
  if (correct && activeQuiz.cards[correct]) activeQuiz.cards[correct].classList.add("right");
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

function addBot(data) {
  const el = document.createElement("div");
  el.className = "msg bot";
  const answer = data.answer || "";
  const trace = data.trace || [];
  const submit = trace.find((s) => s.step === "submit" && s.status === "graded");
  const quizOk = trace.some((s) => s.step === "get_quiz" && s.status === "ok");
  if (quizOk) {
    el.dataset.quiz = "1";
  } else {
    el.innerHTML = renderMarkdown(answer);
  }
  if (submit) { markGraded(el, submit, answer); nextActions(el, submit); }
  activeQuiz = quizOk ? { cards: quizBlock(el, answer) } : null;
  el.appendChild(traceSummary(data));
  log.appendChild(el);
  scrollDown();
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

async function ask(question) {
  question = question.trim();
  if (!question || busy) return;
  const user = currentUid();
  if (!user) { addError("user_id는 영문·숫자·_·-만 쓸 수 있습니다 (점 금지, 64자 이하)."); return; }
  disableChoices();
  addUser(question);
  record({ r: "u", t: question });
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
    addBot(data);
    record({ r: "b", d: { answer: data.answer, trace: data.trace, contexts: data.contexts } });
    if ((data.trace || []).some((s) => s.step === "submit" && s.status === "graded")) loadProgress();
  } catch (e) {
    const msg = "오류: " + (e.message || "서버에 연결할 수 없습니다.");
    addError(msg);
    record({ r: "e", t: msg });
  } finally {
    clearInterval(timer);
    wait.remove();
    setBusy(false);
    q.focus();
  }
}

function pct(x) { return x == null ? "–" : Math.round(x * 100) + "%"; }

async function loadProgress() {
  const box = $("progress");
  const user = currentUid();
  $("panelUser").textContent = user ? `· ${user}` : "";
  if (!user) { box.innerHTML = '<p class="note">user_id 형식이 올바르지 않습니다.</p>'; return; }
  try {
    const res = await fetch("/progress?user_id=" + encodeURIComponent(user));
    const p = await res.json();
    if (!res.ok || p.isError) throw new Error(p.message || p.detail || `HTTP ${res.status}`);
    // 약점 판정은 서버 get_weak_domains 결과(weak)를 그대로 쓴다 (UI에서 계산하지 않음)
    const weak = p.weak && !p.weak.isError ? p.weak : null;
    const weakIds = new Set(weak ? weak.top.map((w) => w.domain) : []);
    const dis = busy ? " disabled" : "";
    const doms = p.domains.map((d) => `
      <button type="button" class="dom${weakIds.has(d.domain) ? " weak" : ""}" data-domain="${d.domain}"${dis}
        title="Domain ${d.domain} 연습 문제 풀기">
        <span class="row"><span class="d-name">D${d.domain} ${esc(d.name)}${weakIds.has(d.domain) ? '<span class="weak-tag">약점</span>' : ""}</span>
          <span class="d-acc">${pct(d.accuracy)}</span></span>
        <span class="track"><span class="fill" style="width:${d.accuracy == null ? 0 : d.accuracy * 100}%"></span></span>
        <span class="row meta"><span class="weight">시험 비중 ${pct(d.weight)}</span><span>정답 ${d.correct}/${d.attempts}</span>
          <span class="go">문제 풀기 →</span></span>
      </button>`).join("");
    let weakBox = "";
    if (weak && weak.top.length) {
      const names = weak.top.map((w) => `D${w.domain} (정답률 ${pct(w.accuracy)})`).join(", ");
      weakBox = `<div class="weak-box"><span class="w-title">약점 도메인</span>${esc(names)}
        <button type="button" class="btn primary" id="weakQuiz" data-domain="${weak.top[0].domain}"${dis}>D${weak.top[0].domain} 약점 문제 풀기</button></div>`;
    } else if (weak) {
      weakBox = `<div class="weak-box note">${esc(weak.message || "약점을 판단할 기록이 부족합니다.")}</div>`;
    }
    const kpi = (label, value, sub, cls = "") =>
      `<div class="kpi${cls}"><div class="k-label">${label}</div><div class="k-value">${value}</div><div class="k-sub">${sub}</div></div>`;
    box.innerHTML = `
      <div class="kpis">
        ${kpi("정답률", pct(p.accuracy), `${p.correct}/${p.attempts} 정답`, " primary")}
        ${kpi("TS 커버리지", `${p.task_coverage.solved}<small>/${p.task_coverage.total}</small>`, "푼 Task Statement")}
        ${kpi("세부 항목 커버리지", `${p.bullet_coverage.solved}<small>/${p.bullet_coverage.total}</small>`, "다룬 세부 항목")}
        ${kpi("최근 학습일", p.last_answered_at ? esc(String(p.last_answered_at).slice(5, 10).replace("-", ".")) : "–",
              p.last_answered_at ? esc(String(p.last_answered_at).slice(0, 4)) + "년" : "기록 없음")}
      </div>${weakBox}
      <div class="section-title">도메인별 진도<span>막대 = 정답률 · 회색 칩 = 시험 비중</span></div>
      ${doms}
      <p class="note">도메인을 누르면 그 도메인 문제를 냅니다. 약점 점수 = 시험 비중 × (1 − 정답률), 시도 3회 이상 도메인만 (서버 계산).</p>`;
    box.querySelectorAll("[data-domain]").forEach((b) => b.addEventListener("click", () => {
      ask(`Domain ${b.dataset.domain}에서 연습 문제 하나 내줘`);
    }));
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
    <p>개념 질문, 연습 문제 출제·채점, 학습 계획, 약점 분석을 도와드려요. 아래에서 시작하거나 직접 질문해 보세요.</p>`;
  const ex = document.createElement("div");
  ex.className = "examples";
  EXAMPLES.forEach((text, i) => {
    const f = FEATURES[i];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "feature";
    b.innerHTML = `<span class="f-ico"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${f.icon}</svg></span>
      <span class="f-title">${esc(f.title)}</span><span class="f-desc">${esc(f.desc)}</span>
      <span class="f-ex">“${esc(text)}”</span>`;
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
$("refresh").addEventListener("click", loadProgress);

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

const showTrace = $("showTrace");
showTrace.checked = store("ccaf_show_trace") === "1";
document.body.classList.toggle("show-trace", showTrace.checked);
showTrace.addEventListener("change", () => {
  document.body.classList.toggle("show-trace", showTrace.checked);
  store("ccaf_show_trace", showTrace.checked ? "1" : "0");
});
uid.addEventListener("change", () => {
  if (currentUid()) { store("ccaf_user_id", uid.value.trim()); newConversation(); }
  loadProgress();
});

uid.value = store("ccaf_user_id") || "web_demo";
showIntro();
restoreSession();
loadProgress();
