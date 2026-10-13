"""문제 생성 + (선택) 검증 모델 정답 대조.

이해도 확인 페이지와 오답노트의 '유사 유형 문제'가 함께 쓴다.
"""

from __future__ import annotations

from core import llm
from core.config import ModelCfg
from core.documents import Chunk, format_context

QUIZ_PROMPT = """아래 [자료]만 근거로 4지선다 문제 {n}개를 만들어라. 난이도: {level}.
규칙:
- 정답은 반드시 자료에서 확인할 수 있어야 한다.
- 오답 보기는 그럴듯하지만 자료와 분명히 다른 내용으로 만든다.
- 시중 기출문제를 그대로 옮기지 말고 자료 내용으로 새로 만든다.
- 각 문제에 근거 쪽 번호(page, 숫자)와 핵심 개념명(concept)을 적는다.
- answer는 정답 보기의 번호(1~4)다.
{extra}
JSON만 출력하라.
{{"questions": [{{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 1, "explanation": "...", "page": 12, "concept": "..."}}]}}

[자료]
{context}
"""

CHECK_PROMPT = """아래 [자료]만 보고 각 문제의 정답 번호(1~4)를 골라라. 자료로 확인할 수 없으면 0을 적어라.
JSON만 출력하라. {{"answers": [2, 1, ...]}}

[자료]
{context}

[문제]
{questions}
"""


def _valid(q: dict) -> bool:
    try:
        return len(q.get("choices", [])) == 4 and 1 <= int(q.get("answer", 0)) <= 4 and bool(q.get("question"))
    except (TypeError, ValueError):
        return False


def generate(
    chunks: list[Chunk],
    n: int,
    level: str,
    main: ModelCfg,
    verifier: ModelCfg | None = None,
    extra: str = "",
) -> list[dict]:
    """문제를 만들고, 검증 모델이 있으면 직접 풀게 해서 check = ok / review 를 붙인다."""
    context = format_context(chunks)
    raw = llm.chat(
        main,
        [{"role": "user", "content": QUIZ_PROMPT.format(n=n, level=level, context=context, extra=extra)}],
        purpose="quiz", max_tokens=3000, temperature=0.5,
        demo_hint={"chunks": [c.as_dict() for c in chunks], "n": n},
    )
    qs = [q for q in llm.parse_json(raw).get("questions", []) if _valid(q)]
    for q in qs:
        q["answer"] = int(q["answer"])

    if verifier and qs:
        listing = "\n".join(
            f"{i+1}. {q['question']} " + " ".join(f"({j+1}) {c}" for j, c in enumerate(q["choices"]))
            for i, q in enumerate(qs)
        )
        chk = llm.chat(
            verifier,
            [{"role": "user", "content": CHECK_PROMPT.format(context=context, questions=listing)}],
            purpose="quiz_check", max_tokens=300, temperature=0,
            demo_hint={"keys": [q["answer"] for q in qs]},
        )
        try:
            answers = llm.parse_json(chk).get("answers", [])
        except Exception:
            answers = []
        for i, q in enumerate(qs):
            a = answers[i] if i < len(answers) else 0
            try:
                q["check"] = "ok" if int(a or 0) == q["answer"] else "review"
            except (TypeError, ValueError):
                q["check"] = "review"
    return qs
