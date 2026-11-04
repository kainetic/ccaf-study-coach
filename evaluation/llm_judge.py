"""LLM judge (DESIGN §9-1). Day 7 llm_judge.py 확장: 점수 하나가 아니라 expected_traits·forbidden 항목별 판정.

- 모델: 런타임과 같은 Sonnet 4.5 temperature 0 (src.llm.get_runtime_llm, 쓰로틀링 시에만 전환 — 실제 모델을 결과에 기록)
- 루브릭은 상수로 고정하고 JUDGE_VERSION으로 구분한다 (round 간 비교는 같은 judge로)
- 입력: 질문, 답변, 시스템이 실제로 한 일(trace 요약: 경로·도구·출제·기록 조회 결과 수치). 가이드 원문은 넣지 않는다
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schemas import JudgeItem, JudgeVerdict  # noqa: E402

JUDGE_VERSION = "s3-v1"

RUBRIC = """너는 CCA-F 시험 학습 도우미의 답변을 채점하는 평가자다. 아래 기준을 그대로 적용한다.

[expected_traits] 각 항목이 답변에 충족되었는지 판정한다 (ok=true면 충족).
- 표현이 달라도 의미가 같으면 충족. 답변에 없는 내용은 충족이 아니다. 길이나 문체는 보지 않는다.
- "A 또는 B", "A 1개 이상"은 하나만 있어도 충족.
- "출처 X 표기"는 답변의 [근거: …] 표기(또는 본문에 명시한 출처)에 X가 있어야 충족. "출처 X 또는 Y"는 하나면 충족.
- "정답은 이번 응답에 공개하지 않음" 같은 부정 조건은 답변에 그 내용이 없으면 충족.
- 수치·기록 근거 항목은 [시스템이 실제로 한 일]과 대조한다. 답변의 정답률·시간 배분이 거기 나온 값과 맞으면 기록 근거로 본다.

[forbidden] 각 항목을 위반했는지 판정한다 (ok=true면 위반하지 않음).
- 답변이 그 항목에 해당하는 내용을 실제로 주장·제공했을 때만 위반이다. 부정하거나 경고하는 맥락의 언급은 위반이 아니다.

reason은 판정 근거를 한국어 한 문장으로 쓴다. 항목 번호 순서대로 빠짐없이 판정한다."""


def build_prompt(question: str, answer: str, traits: list[str], forbidden: list[str], facts: dict) -> str:
    t = "\n".join(f"{i}. {x}" for i, x in enumerate(traits, start=1)) or "(없음)"
    f = "\n".join(f"{i}. {x}" for i, x in enumerate(forbidden, start=1)) or "(없음)"
    return (f"{RUBRIC}\n\n[질문]\n{question}\n\n[답변]\n{answer}\n\n"
            f"[시스템이 실제로 한 일]\n{json.dumps(facts, ensure_ascii=False)}\n\n"
            f"[expected_traits]\n{t}\n\n[forbidden]\n{f}")


def _align(items: list[JudgeItem], n: int, kind: str) -> list[JudgeItem]:
    """judge가 항목을 빠뜨리면 그 항목은 불충족(실패)으로 둔다 — 조용히 통과시키지 않는다."""
    by = {i.index: i for i in items}
    return [by.get(k) or JudgeItem(index=k, ok=False, reason=f"judge가 {kind} {k}번을 판정하지 않음") for k in range(1, n + 1)]


async def judge_case(question: str, answer: str, traits: list[str], forbidden: list[str], facts: dict) -> tuple[JudgeVerdict, str]:
    """반환 (판정, 실제 judge 모델)."""
    from src.llm import get_runtime_llm
    judge = get_runtime_llm().with_structured_output(JudgeVerdict, include_raw=True)
    out = await judge.ainvoke(build_prompt(question, answer, traits, forbidden, facts))
    if out.get("parsed") is None:
        raise RuntimeError(f"judge 구조화 출력 실패: {out.get('parsing_error')}")
    v: JudgeVerdict = out["parsed"]
    served = (out["raw"].response_metadata or {}).get("served_by", "?")
    return JudgeVerdict(traits=_align(v.traits, len(traits), "traits"),
                        forbidden=_align(v.forbidden, len(forbidden), "forbidden")), served
