"""문제 은행 + 풀이 기록 MCP 서버 (DESIGN §3-2, §5-3, §5-4). Day 4 mcp_server.py 구조.

단독 실행하지 않는다. 클라이언트(src/agent.py)가 `sys.executable -m src.quiz_bank_server`로 stdio 자식 프로세스로 띄운다.

- 도구: get_quiz, submit_answer (P0). get_progress·get_weak_domains는 P1에서 추가
- resource: quiz://catalog (도메인·TS·시나리오별 출제 가능 문항 수)
- 출제는 data/quiz_pool.json의 status == "approved" 문항만. 런타임에 문제를 만들지 않는다
- get_quiz는 정답·해설을 뺀 QuizPublic만 반환. 정답은 submit_answer가 코드로 채점한 뒤에만 공개
- 조건에 맞는 문항이 없으면 에러가 아니라 status "no_match" + alternatives
- 에러는 {"isError", "errorCategory", "isRetryable", "message"} (TS 2.2)

핵심 로직은 QuizBank 클래스(순수 Python)에 두고 MCP 도구는 얇게 감싼다 (LLM 없이 테스트 가능).
"""
import json
import os
import random
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from src import config
from src.schemas import LABELS, GradeResult, QuizIssued, QuizItem, QuizPublic, StudyAttempt, StudyLog, UserStudyLog

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")       # Store namespace 규칙: 점 금지
TASK_ID_RE = re.compile(r"^(?:TS[-\s]?)?([1-5])\.([1-7])$", re.IGNORECASE)
MIN_ATTEMPTS_FOR_WEAKNESS = 3                           # 이보다 적으면 "기록 부족" → 시험 비중만 사용
WEAKNESS_FLOOR = 0.2                                     # 정답률이 높아도 그 도메인이 아예 안 나오지는 않게
MAX_ALTERNATIVES = 6


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def error(category: str, message: str, retryable: bool = False) -> dict:
    return {"isError": True, "errorCategory": category, "isRetryable": retryable, "message": message}


def normalize_task_id(task_id: str) -> Optional[str]:
    m = TASK_ID_RE.match(task_id.strip())
    return f"TS-{m.group(1)}.{m.group(2)}" if m else None


def to_public(item: QuizItem) -> QuizPublic:
    return QuizPublic(quiz_id=item.quiz_id, task_id=item.task_id, scenario_id=item.scenario_id, stem=item.stem,
                      options=[{"label": o.label, "text": o.text} for o in item.options])


class QuizBank:
    """출제·채점·기록. 파일 쓰기는 lock + 원자적 교체."""

    def __init__(self, pool_path: Path = config.QUIZ_POOL_PATH, log_path: Path = config.STUDY_LOG_PATH):
        items = [QuizItem.model_validate(d) for d in json.loads(pool_path.read_text(encoding="utf-8"))]
        self.all_items = {i.quiz_id: i for i in items}
        self.approved = sorted((i for i in items if i.status == "approved"), key=lambda i: i.quiz_id)
        self.task_domain = {i.task_id: i.domain for i in items}
        self.log_path = log_path
        self._lock = threading.Lock()

    # ---- 기록 ----
    def _load_log(self) -> StudyLog:
        if not self.log_path.is_file():
            return StudyLog()
        return StudyLog.model_validate_json(self.log_path.read_text(encoding="utf-8"))

    def _save_log(self, log: StudyLog) -> None:
        tmp = self.log_path.with_suffix(self.log_path.suffix + ".tmp")
        tmp.write_text(log.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.log_path)

    # ---- 검증 ----
    def _check_filters(self, user_id: str, domain, task_id, scenario_id) -> tuple[Optional[dict], Optional[str]]:
        if not USER_ID_RE.match(user_id or ""):
            return error("validation", f"user_id 형식 오류: {user_id!r} (영문·숫자·_·-만, 점 금지)"), None
        if domain is not None and domain not in config.DOMAIN_WEIGHTS:
            return error("validation", f"domain은 1~5: {domain!r}"), None
        if scenario_id is not None and scenario_id not in range(1, 7):
            return error("validation", f"scenario_id는 1~6: {scenario_id!r}"), None
        tid = None
        if task_id:
            tid = normalize_task_id(str(task_id))
            if tid is None or tid not in self.task_domain:
                return error("validation", f"알 수 없는 task_id: {task_id!r} (예: TS-1.4)"), None
            if domain is not None and self.task_domain[tid] != domain:
                return error("validation", f"{tid}는 D{self.task_domain[tid]} 소속이라 domain {domain}과 맞지 않음"), None
        return None, tid

    @staticmethod
    def _match(i: QuizItem, domain, task_id, scenario_id) -> bool:
        return ((domain is None or i.domain == domain) and (task_id is None or i.task_id == task_id)
                and (scenario_id is None or i.scenario_id == scenario_id))

    # ---- 출제 ----
    def _domain_factor(self, attempts: list[StudyAttempt], d: int) -> float:
        mine = [a for a in attempts if a.domain == d]
        if len(mine) < MIN_ATTEMPTS_FOR_WEAKNESS:
            return 1.0
        acc = sum(a.correct for a in mine) / len(mine)
        return max(WEAKNESS_FLOOR, 1.0 - acc)

    def _alternatives(self, unsolved: list[QuizItem], domain, task_id, scenario_id) -> list[dict]:
        """조건을 하나씩 뺐을 때 출제 가능한 조합. 시나리오 → TS → 도메인 순으로 완화 (주제를 최대한 유지)."""
        out: list[dict] = []
        given = {"scenario_id": scenario_id, "task_id": task_id, "domain": domain}
        for drop in ("scenario_id", "task_id", "domain"):
            if given[drop] is None:
                continue
            kept = {**given, drop: None}
            if drop == "task_id" and kept["domain"] is None:
                kept["domain"] = self.task_domain[task_id]      # TS를 풀어도 같은 도메인 안에서 찾는다
            pool = [i for i in unsolved if self._match(i, kept["domain"], kept["task_id"], kept["scenario_id"])]
            groups = Counter((i.domain, i.task_id, i.scenario_id) for i in pool)
            for (d, t, s), n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0])):
                out.append({"domain": d, "task_id": t, "scenario_id": s, "count": n, "relaxed": drop})
        seen, uniq = set(), []
        for a in out:
            key = (a["task_id"], a["scenario_id"])
            if key not in seen:
                seen.add(key)
                uniq.append(a)
        return uniq[:MAX_ALTERNATIVES]

    def get_quiz(self, user_id: str, domain: Optional[int] = None, task_id: Optional[str] = None,
                 scenario_id: Optional[int] = None) -> dict:
        err, tid = self._check_filters(user_id, domain, task_id, scenario_id)
        if err:
            return err
        with self._lock:
            log = self._load_log()
            user = log.users.get(user_id, UserStudyLog())
            solved = {a.quiz_id for a in user.attempts}
            solved_bullets = {b for a in user.attempts for b in a.focus_bullet_ids}
            issued = {q.quiz_id for q in user.issued}
            unsolved_all = [i for i in self.approved if i.quiz_id not in solved]

            cands = [i for i in self.approved if self._match(i, domain, tid, scenario_id)]
            label = " · ".join(x for x in (f"D{domain}" if domain else "", tid or "",
                                           f"SC-{scenario_id}" if scenario_id else "") if x) or "전체"
            if not cands:
                return {"status": "no_match", "reason": "not_in_pool",
                        "message": f"{label} 조합의 승인 문제가 풀에 없음",
                        "alternatives": self._alternatives(unsolved_all, domain, tid, scenario_id)}
            unsolved = [i for i in cands if i.quiz_id not in solved]
            if not unsolved:
                return {"status": "no_match", "reason": "all_solved",
                        "message": f"{label} 범위의 문제 {len(cands)}개를 모두 풀었음",
                        "alternatives": self._alternatives(unsolved_all, domain, tid, scenario_id)}

            rng = random.Random(f"{user_id}:{len(user.attempts)}:{len(user.issued)}")
            if domain is None and tid is None and scenario_id is None:
                # 조건이 없으면 도메인을 시험 비중 × 약점 계수로 고른다 (기록 부족 도메인은 비중만)
                doms = sorted({i.domain for i in unsolved})
                w = [config.DOMAIN_WEIGHTS[d] * self._domain_factor(user.attempts, d) for d in doms]
                pick_d = rng.choices(doms, weights=w, k=1)[0]
                unsolved = [i for i in unsolved if i.domain == pick_d]
            # 아직 안 낸 문제 → 안 푼 세부 항목을 다루는 문제 우선
            tiers = [
                [i for i in unsolved if i.quiz_id not in issued and not set(i.focus_bullet_ids) <= solved_bullets],
                [i for i in unsolved if i.quiz_id not in issued],
                unsolved,
            ]
            item = rng.choice(next(t for t in tiers if t))
            user.issued.append(QuizIssued(quiz_id=item.quiz_id, issued_at=now_iso()))
            log.users[user_id] = user
            self._save_log(log)
            return {"status": "ok", "domain": item.domain, "remaining_in_scope": len(unsolved) - 1,
                    "quiz": to_public(item).model_dump()}

    # ---- 채점 ----
    def submit_answer(self, user_id: str, quiz_id: str, choice: str) -> dict:
        if not USER_ID_RE.match(user_id or ""):
            return error("validation", f"user_id 형식 오류: {user_id!r} (영문·숫자·_·-만, 점 금지)")
        c = (choice or "").strip().upper().rstrip(").")
        if c not in LABELS:
            return error("validation", f"choice는 A·B·C·D 중 하나: {choice!r}")
        item = self.all_items.get(quiz_id)
        if item is None:
            return error("validation", f"알 수 없는 quiz_id: {quiz_id}")
        if item.status != "approved":
            return error("validation", f"출제 대상이 아닌 문제: {quiz_id} (status {item.status})")
        result = GradeResult(quiz_id=item.quiz_id, choice=c, correct=(c == item.answer), answer=item.answer,
                             option_rationales={o.label: o.rationale for o in item.options}, task_id=item.task_id)
        with self._lock:
            log = self._load_log()
            user = log.users.setdefault(user_id, UserStudyLog())
            user.attempts.append(StudyAttempt(quiz_id=item.quiz_id, task_id=item.task_id, domain=item.domain,
                                              focus_bullet_ids=item.focus_bullet_ids, choice=c,
                                              correct=result.correct, answered_at=now_iso()))
            self._save_log(log)
        return {"status": "graded", **result.model_dump(),
                "options": {o.label: o.text for o in item.options}}

    # ---- 카탈로그 ----
    def catalog(self) -> dict:
        by_dom = Counter(i.domain for i in self.approved)
        by_ts: dict[str, Counter] = defaultdict(Counter)
        for i in self.approved:
            by_ts[i.task_id][f"SC-{i.scenario_id}"] += 1
        return {"total": len(self.approved),
                "domains": {f"D{d}": by_dom[d] for d in sorted(by_dom)},
                "tasks": {t: dict(sorted(c.items())) for t, c in sorted(by_ts.items())}}


# ---------------------------------------------------------------- MCP

def build_server(bank: Optional[QuizBank] = None):
    from fastmcp import FastMCP

    bank = bank or QuizBank()
    mcp = FastMCP("ccaf-quiz-bank")

    def safe(fn, *args, **kwargs) -> str:
        try:
            out = fn(*args, **kwargs)
        except OSError as e:
            out = error("transient", f"기록 파일 입출력 오류: {e}", retryable=True)
        except Exception as e:   # noqa: BLE001 — 도구 결과로 구조화해서 돌려준다
            out = error("internal", f"{type(e).__name__}: {e}")
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def get_quiz(user_id: str, domain: Optional[int] = None, task_id: Optional[str] = None,
                 scenario_id: Optional[int] = None) -> str:
        """검토를 통과한(approved) 사전 생성 문제 풀에서 연습 문제 1개를 정답·해설 없이 꺼낸다.

        언제 쓰나: 사용자가 연습 문제·퀴즈를 요청할 때. 조건이 있으면 domain(1~5), task_id("TS-1.4"),
        scenario_id(1~6)로 거른다. 조건이 없으면 시험 비중 × 약점으로 도메인을 고른다.
        언제 쓰지 않나: 개념 설명, 답 제출(submit_answer 사용), 문제를 새로 만들어 달라는 요청(만들지 않음).

        반환(JSON 문자열):
          - {"status": "ok", "quiz": {quiz_id, task_id, scenario_id, stem, options[{label,text}]}, ...}
          - {"status": "no_match", "reason": "not_in_pool"|"all_solved", "alternatives": [{task_id, scenario_id, count, relaxed}]}
            → 문제를 지어내지 말고 alternatives 중 하나를 제안하거나 그 조건으로 다시 호출
          - 에러: {"isError": true, "errorCategory": "validation", ...}

        예: get_quiz(user_id="demo_new", domain=1) / get_quiz(user_id="demo_new", task_id="TS-1.7", scenario_id=5)
        """
        return safe(bank.get_quiz, user_id, domain, task_id, scenario_id)

    @mcp.tool()
    def submit_answer(user_id: str, quiz_id: str, choice: str) -> str:
        """직전에 출제한 문제의 답을 코드로 채점하고 풀이 기록에 저장한다 (채점과 기록을 한 번에).

        언제 쓰나: 사용자가 출제된 문제에 답(A~D)을 말했을 때만. quiz_id는 직전 get_quiz의 quiz_id.
        언제 쓰지 않나: 출제된 문제가 없을 때, 정답을 미리 알고 싶다는 요청.

        반환(JSON 문자열): {"status": "graded", quiz_id, choice, correct, answer, option_rationales{A..D}, options{A..D}, task_id}
        에러: 알 수 없는 quiz_id, A~D가 아닌 choice → {"isError": true, "errorCategory": "validation", ...}

        예: submit_answer(user_id="demo_new", quiz_id="Q-1.4-K2", choice="B")
        """
        return safe(bank.submit_answer, user_id, quiz_id, choice)

    @mcp.resource("quiz://catalog")
    def quiz_catalog() -> str:
        """도메인·TS·시나리오별 출제 가능(approved) 문항 수."""
        return json.dumps(bank.catalog(), ensure_ascii=False)

    return mcp


if __name__ == "__main__":
    build_server().run(show_banner=False)   # stdio
