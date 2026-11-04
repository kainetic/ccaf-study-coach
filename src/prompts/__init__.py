"""문제 생성·검토 프롬프트 (오프라인 전용, DESIGN §5-2).

프롬프트 파일은 `<<<SYSTEM>>>` / `<<<USER>>>` 구분선으로 나뉘고, 자리표시자는 string.Template의 `$name`.
본문에 `$` 문자가 필요하면 `$$`로 쓴다.
"""
import hashlib
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent
PROMPT_FILES = ("quiz_generate.md", "quiz_review.md", "quiz_retry.md")

SYSTEM_MARK = "<<<SYSTEM>>>"
USER_MARK = "<<<USER>>>"


def _read(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def load(name: str) -> Template:
    """구분선이 없는 템플릿 (quiz_retry)."""
    return Template(_read(name).strip())


def load_pair(name: str) -> tuple[str, Template]:
    """(system 문자열, user 템플릿). system에는 자리표시자를 두지 않는다."""
    text = _read(name)
    if SYSTEM_MARK not in text or USER_MARK not in text:
        raise ValueError(f"{name}.md: {SYSTEM_MARK}/{USER_MARK} 구분선이 없음")
    system, user = text.split(USER_MARK, 1)
    return system.replace(SYSTEM_MARK, "").strip(), Template(user.strip())


def prompt_version() -> str:
    """프롬프트 파일과 FIX_HINTS(이 파일)의 sha1 앞 8자리. 매니페스트 슬롯마다 기록한다."""
    h = hashlib.sha1()
    for f in (*PROMPT_FILES, "__init__.py"):
        h.update((PROMPT_DIR / f).read_bytes())
    return h.hexdigest()[:8]


# 재시도 피드백의 "How to fix"에 넣는 패턴별 고정 지시문 (DESIGN §5-2 7단계, TS 4.4)
FIX_HINTS: dict[str, str] = {
    "parse": "Return the result only through the structured output, with every field filled: "
             "stem, exactly 4 options (label, text, rationale), answer, source_excerpt.",
    "code": "Fix each [code] problem exactly as stated. In particular: exactly 4 options labeled A-D once each; "
            "stem and options in English with no Korean; every rationale in Korean; no option letters or "
            "'option X' references anywhere; source_excerpt copied character-for-character from one line of the "
            "task statement; correct option no longer than 1.25x the average distractor and 1.15x the longest "
            "distractor; no internal terms in rationales.",
    "answer_not_supported": "Make the correct option follow directly from the focus item(s). Quote the supporting "
                            "words verbatim in source_excerpt and make the correct option apply exactly that idea.",
    "multiple_correct": "Add one concrete constraint to the stem (a requirement, failure symptom, or business rule) "
                        "that only the correct option satisfies, and rewrite any distractor that could still be "
                        "defended so it clearly violates that constraint.",
    "distractor_obviously_wrong": "Replace weak distractors with realistic but inferior approaches a practitioner "
                                  "might actually choose — e.g. relying on prompt instructions where programmatic "
                                  "enforcement is needed, or a nearby concept from the same task statement applied "
                                  "in the wrong place. Match the correct option's length and specificity.",
    "off_focus_bullet": "Re-center the question on the focus item(s): a candidate who does not know the focus "
                        "item should not be able to answer it. With two focus items, the decision must need both.",
    "out_of_scope": "Remove anything that needs knowledge outside the task statement (exact numbers, pricing, "
                    "API parameter names not mentioned, other products). Test only the concept in the focus item(s).",
    "scenario_mismatch": "Rewrite the situation so it fits the SCENARIO given above (its system, users, tools and "
                         "goal). Do not reuse the previous situation.",
    "other": "Fix every problem listed above; keep the stem unambiguous and every rationale consistent with the answer.",
}
