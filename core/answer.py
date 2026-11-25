"""답변 방식(자료 + AI 설명 / 자료만)의 프롬프트와 조립 로직. Streamlit UI 호출 없음."""

from __future__ import annotations

import re

import streamlit as st

MODE_HYBRID = "hybrid"
MODE_GROUNDED = "grounded"
MODES = [MODE_HYBRID, MODE_GROUNDED]
MODE_LABEL = {MODE_HYBRID: "자료 + AI 설명", MODE_GROUNDED: "자료만"}
SS_MODE = "answer_mode"

GROUNDED_SYSTEM = """너는 대학생의 자격증·전공 공부를 돕는 학습 조교 'CertMate'다.
규칙:
1. 반드시 [근거]에 있는 내용으로만 답한다. 근거에 없으면 "업로드한 자료에서 찾을 수 없습니다"라고 말하고 추측하지 않는다.
2. 문장마다 근거 위치를 [자료명 p.쪽] 형식으로 붙인다.
3. 다음 순서로 쓴다: **핵심 설명** → **부연 설명**(쉬운 예시, 헷갈리는 개념과 비교) → **시험 포인트**.
4. 다른 자격증 시험에 나오는지 여부는 말하지 않는다. (앱이 공식 출제기준 데이터로 따로 안내한다)
5. 한국어로, 대학생이 이해하기 쉽게 쓴다."""

GENERAL_SYSTEM = """너는 자격증(예: ADsP, 정보처리기사)을 공부하는 한국 대학생을 돕는 학습 조교 'CertMate'다.
너의 일반 지식으로 질문의 개념을 설명한다.
다음 순서로 간결하게 쓴다: **핵심 설명** → **쉬운 예시**(관련 있으면 코드/SQL 예시) → **헷갈리기 쉬운 개념**.
규칙:
1. 쪽 번호를 인용하지 않는다. 사용자의 자료를 인용하거나 인용하는 척하지 않는다.
2. 어떤 시험에 나오는지는 말하지 않는다. (앱이 따로 안내한다)
3. 확실하지 않으면 추측하지 말고 모른다고 말한다.
4. 한국어로 쓴다.
5. 제목(#)은 쓰지 않는다. 섹션 이름은 **굵은 글씨**로 쓴다.
6. 예시 표가 필요하거나 사용자가 요청하면 마크다운 표를 최대 6행까지만 만들고, 이어서 SQL과 결과 표를 보여 준다.
7. 답변을 끝까지 완결해서 쓴다. 적당한 길이 안에서, 길게 쓰기보다 간결하게 쓰는 것을 우선한다."""

NOTES_SYSTEM = """너는 대학생의 공부를 돕는 학습 조교 'CertMate'다.
[근거]에 있는 내용 중 [질문]과 관련된 것만 요약한다.
규칙:
1. 글머리표(-)로 정리하고, 모든 항목 끝에 [자료명 p.쪽] 형식으로 근거 위치를 붙인다.
2. 마지막에 자료에서 뽑은 한 줄 **시험 포인트**를 쓴다.
3. 외부 지식을 쓰지 않고, 일반적인 개념 설명을 반복하지 않는다.
4. [질문]이 앞선 대화를 이어가는 말(예: "예시로 설명해줘")이면 이전 질문의 주제를 기준으로 관련성을 판단한다. 그 주제를 [근거]가 다루지 않을 때만 정확히 "업로드한 자료에 관련 내용이 없습니다."라고만 쓴다.
5. 제목(#)은 쓰지 않는다. 섹션 이름은 **굵은 글씨**로 쓴다.
6. 한국어로 쓴다."""

GENERAL_MAX_TOKENS = 4000
NOTES_MAX_TOKENS = 2500
TRUNCATED_NOTE = "⚠️ 답변이 길이 제한으로 잘렸습니다. '이어서 설명해줘'라고 물어보세요."
_EMPTY_TRUNCATED = "⚠️ 길이 한도 안에서 설명을 마치지 못했습니다. 질문을 조금 좁혀서 다시 물어보세요."

NO_MATERIAL = "업로드한 자료에서 관련 내용을 찾지 못했습니다."
_REFUSAL = NO_MATERIAL + " 다른 표현으로 물어보거나, 해당 내용이 있는 자료를 올려 주세요."


def current_mode() -> str:
    mode = st.session_state.get(SS_MODE)
    return mode if mode in MODES else MODE_HYBRID


def history_for_model(messages: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["content"]} for m in messages[-7:-1]]


def general_messages(question: str, history: list[dict]) -> list[dict]:
    return [{"role": "system", "content": GENERAL_SYSTEM}, *history,
            {"role": "user", "content": question}]


def grounded_messages(mode: str, question: str, context: str, history: list[dict]) -> list[dict]:
    system = GROUNDED_SYSTEM if mode == MODE_GROUNDED else NOTES_SYSTEM
    return [{"role": "system", "content": system}, *history,
            {"role": "user", "content": f"[근거]\n{context}\n\n[질문]\n{question}"}]


def finish(text: str, truncated: bool) -> str:
    """길이 제한으로 잘린 답변에는 안내 문구를 붙인다."""
    if not truncated:
        return text
    if not text.strip():
        return _EMPTY_TRUNCATED
    body = text.rstrip()
    fence = _scan_fences(body.splitlines())[-1]
    if fence:   # 코드 블록 중간에서 잘렸으면 닫아서 안내 문구가 코드 안에 갇히지 않게 한다
        body += "\n" + fence
    return body + "\n\n" + TRUNCATED_NOTE


_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _scan_fences(lines: list[str]) -> list[str | None]:
    """줄마다 '그 줄을 처리한 뒤 열려 있는 코드 펜스'(없으면 None)를 돌려준다. 마지막 원소 = 끝에서의 상태.

    CommonMark처럼 닫는 펜스는 여는 펜스와 같은 문자, 같거나 더 긴 길이, 뒤에 공백만 있어야 한다.
    """
    states: list[str | None] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE.match(line.rstrip("\r\n"))
        if m:
            mark, rest = m.group(1), m.group(2)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not rest.strip():
                fence = None
        states.append(fence)
    return states or [None]


def demote_headings(text: str) -> str:
    """모델이 쓴 # 제목을 **굵은 글씨**로 바꾼다. 코드 블록 안은 건드리지 않는다."""
    lines = text.splitlines(keepends=True)
    states = _scan_fences(lines)
    out: list[str] = []
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        inside = states[i] is not None or (i > 0 and states[i - 1] is not None)  # 펜스 줄 자체와 블록 안
        h = None if inside or _FENCE.match(body) else _HEADING.match(body)
        out.append(f"**{h.group(1)}**{eol}" if h else line)
    return "".join(out)


def search_query(question: str, messages: list[dict]) -> str:
    """후속 질문("예시로 설명해줘")도 검색되도록 직전 사용자 질문을 함께 쓴다."""
    for m in reversed(messages[:-1]):
        if m.get("role") == "user":
            return f"{m['content']}\n{question}"
    return question


def compose(mode: str, general: str | None, grounded: str | None) -> str:
    if general:
        general = demote_headings(general)
    if grounded:
        grounded = demote_headings(grounded)
    if mode == MODE_GROUNDED:
        return grounded if grounded else _REFUSAL
    return (
        "#### 🤖 AI 설명\n\n"
        "_AI 모델의 일반 지식 · 업로드 자료 근거 없음 · 교차검증 대상 아님_\n\n"
        f"{general or '_(AI 설명을 받지 못했습니다)_'}\n\n"
        "#### 📄 내 자료에서\n\n"
        f"{grounded or NO_MATERIAL}"
    )


def mapping_text(mode: str, question: str, grounded: str | None) -> str | None:
    """교차 출제 안내(출제기준 검색)에 넘길 글. AI 일반 설명은 넣지 않는다 (자료와 무관한 개념이 섞이지 않게).

    None이면 안내를 만들지 않는다 (자료만 모드에서 자료를 못 찾은 경우).
    """
    if grounded:
        return f"{question}\n{grounded}"
    return question if mode == MODE_HYBRID else None
