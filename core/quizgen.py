"""문제 생성 + (선택) 검증 모델 정답 대조.

이해도 확인 페이지와 오답노트의 '유사 유형 문제'가 함께 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core import llm
from core.config import ModelCfg
from core.documents import Chunk, format_context

# 추론형 모델은 보이지 않는 추론 토큰도 한도에 포함되므로 넉넉히 잡는다.
QUIZ_MAX_TOKENS = 6000
CHECK_MAX_TOKENS = 1500
REPAIR_MAX_TOKENS = 6000

QUIZ_PROMPT = """아래 [자료]만 근거로 4지선다 문제 {n}개를 만들어라. 난이도: {level}.
규칙:
- 정답은 반드시 자료에서 확인할 수 있어야 한다.
- 오답 보기는 그럴듯하지만 자료와 분명히 다른 내용으로 만든다.
- 시중 기출문제를 그대로 옮기지 말고 자료 내용으로 새로 만든다.
- 각 문제에 근거 쪽 번호(page, 숫자)와 핵심 개념명(concept)을 적는다.
- answer는 정답 보기의 번호(1~4)다.
- 문자열 안에는 큰따옴표(")를 쓰지 않는다. SQL 문자열 값은 작은따옴표(')로 쓴다.
- JSON 외의 글(설명, 코드 블록 표시)은 쓰지 않는다.
{extra}
JSON만 출력하라.
{{"questions": [{{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 1, "explanation": "...", "page": 12, "concept": "..."}}]}}

[자료]
{context}
"""

REPAIR_PROMPT = """아래 [깨진 텍스트]는 문제 목록 JSON이 망가진 것이다.
내용을 최대한 그대로 살려서 올바른 JSON만 출력하라. 형식: {{"questions": [...]}}
규칙:
- 새 문제를 지어내지 말고, 주어진 문제만 담는다.
- 문자열 안의 큰따옴표는 작은따옴표로 바꾸거나 \\" 로 이스케이프하고, 빠진 쉼표·괄호를 고친다.
- JSON 외의 글(설명, 코드 블록 표시)은 쓰지 않는다.

[깨진 텍스트]
{broken}
"""

CHECK_PROMPT = """아래 [자료]만 보고 각 문제의 정답 번호(1~4)를 골라라. 자료로 확인할 수 없으면 0을 적어라.
JSON만 출력하라. {{"answers": [2, 1, ...]}}

[자료]
{context}

[문제]
{questions}
"""


@dataclass
class QuizResult:
    questions: list[dict]
    note: str = ""
    raw: str = ""
    repaired: bool = False
    truncated: bool = False


def _valid(q: dict) -> bool:
    try:
        return len(q.get("choices", [])) == 4 and 1 <= int(q.get("answer", 0)) <= 4 and bool(q.get("question"))
    except (TypeError, ValueError, AttributeError):
        return False


def _clean(items: list) -> list[dict]:
    """형식이 맞는 문항만 남기고 answer를 int로 맞춘다."""
    out = [q for q in items if isinstance(q, dict) and _valid(q)]
    for q in out:
        q["answer"] = int(q["answer"])
    return out


def parse_questions(raw: str) -> list[dict]:
    """JSON 전체를 읽는다. 읽지 못하면 ValueError."""
    try:
        items = llm.parse_json(raw).get("questions", [])
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(items, list):
        raise ValueError("questions 형식이 목록이 아닙니다.")
    return _clean(items)


def salvage_questions(raw: str) -> list[dict]:
    """깨지거나 잘린 JSON에서 온전한 문항 객체만 건진다. 예외를 내지 않는다."""
    try:
        key = raw.find('"questions"')
        if key == -1:
            return []
        pos = raw.find("[", key)
        if pos == -1:
            return []
        pos += 1
        dec = json.JSONDecoder()
        items: list = []
        while True:
            while pos < len(raw) and (raw[pos].isspace() or raw[pos] == ","):
                pos += 1
            if pos >= len(raw):
                break
            try:
                obj, pos = dec.raw_decode(raw, pos)
            except ValueError:
                break
            items.append(obj)
        return _clean(items)
    except Exception:
        return []


def _verify(qs: list[dict], context: str, verifier: ModelCfg) -> None:
    listing = "\n".join(
        f"{i+1}. {q['question']} " + " ".join(f"({j+1}) {c}" for j, c in enumerate(q["choices"]))
        for i, q in enumerate(qs)
    )
    chk = llm.chat(
        verifier,
        [{"role": "user", "content": CHECK_PROMPT.format(context=context, questions=listing)}],
        purpose="quiz_check", max_tokens=CHECK_MAX_TOKENS, temperature=0,
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


def generate_report(
    chunks: list[Chunk],
    n: int,
    level: str,
    main: ModelCfg,
    verifier: ModelCfg | None = None,
    extra: str = "",
) -> QuizResult:
    """문제를 만들고(깨진 JSON은 복구 시도), 검증 모델이 있으면 check = ok / review 를 붙인다."""
    context = format_context(chunks)
    raw, truncated = llm.chat_with_status(
        main,
        [{"role": "user", "content": QUIZ_PROMPT.format(n=n, level=level, context=context, extra=extra)}],
        purpose="quiz", max_tokens=QUIZ_MAX_TOKENS, temperature=0.5,
        demo_hint={"chunks": [c.as_dict() for c in chunks], "n": n},
    )

    repaired = False
    try:
        qs = parse_questions(raw)
    except ValueError:
        qs = None
        if not truncated:   # 잘린 글은 복구 호출로 되살릴 수 없고 지어낼 위험만 있다.
            try:   # 복구 호출이 실패해도(한도 초과 등) 아래에서 원문의 온전한 문제는 살린다
                fixed, _ = llm.chat_with_status(
                    main,
                    [{"role": "user", "content": REPAIR_PROMPT.format(broken=raw)}],
                    purpose="repair", max_tokens=REPAIR_MAX_TOKENS, temperature=0,
                )
                qs = parse_questions(fixed) or None   # 빈 목록이면 복구 실패로 보고 원문에서 살린다
                repaired = qs is not None
            except (ValueError, llm.LLMError):
                qs = None
        if qs is None:
            qs = salvage_questions(raw)

    note = ""
    if not qs:
        note = "AI가 보낸 문제 형식을 읽지 못했어요. 문항 수를 줄이거나 다시 시도해 주세요."
    elif len(qs) < n:   # 잘림·깨짐·형식이 틀린 문항 걸러짐 등 원인과 관계없이 모자라면 알린다
        note = (f"AI가 보낸 문제 일부가 잘리거나 형식이 맞지 않아 {len(qs)}문제만 만들었어요. "
                f"다시 시도하면 {n}문제를 받을 수 있어요.")

    if verifier and qs:
        _verify(qs, context, verifier)
    return QuizResult(questions=qs, note=note, raw=raw, repaired=repaired, truncated=truncated)


def generate(
    chunks: list[Chunk],
    n: int,
    level: str,
    main: ModelCfg,
    verifier: ModelCfg | None = None,
    extra: str = "",
) -> list[dict]:
    """generate_report의 문항 목록만 돌려준다 (오답노트 등 기존 호출부용)."""
    return generate_report(chunks, n, level, main, verifier, extra).questions
