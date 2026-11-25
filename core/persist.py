"""오답노트를 이 브라우저의 localStorage에 자동 저장·복원한다.

서버에는 아무것도 저장하지 않는다. 브라우저 쪽 입출력은 core/browser_storage.py(숨은 컴포넌트 하나)가 맡고,
이 모듈은 '무엇을 저장하고, 받은 값을 어떻게 합칠지'만 정한다.
저장 대상은 wrongnote.export_json()에 담기는 것(오답 + 문제 은행)뿐이다. PDF·대화는 저장하지 않는다. API 설정은 사용자가 고를 때만 core/apikeys.py가 따로 다룬다.
"""

from __future__ import annotations

import json

import streamlit as st

from core import wrongnote as wn

STORAGE_KEY = "certmate.wrongnote.v1"
LOADED = "_wn_storage_loaded"
MSG = "_wn_storage_msg"



def payload() -> str:
    """저장할 문자열. 오답도 문제 은행도 없으면 빈 문자열(= 저장된 값 삭제)."""
    if not wn.notes() and not wn.bank():
        return ""
    return wn.export_json()


def save_arg() -> str | None:
    """브라우저에 보낼 저장 값. 아직 불러오기 전이면 None (빈 새 세션이 기존 저장분을 덮어쓰지 않게)."""
    if not st.session_state.get(LOADED, False):
        return None
    return payload()


def apply_loaded(value: str | None) -> str | None:
    """브라우저가 보내온 값을 반영한다. 사용자에게 보여줄 안내 문장(없으면 None)을 돌려준다."""
    if st.session_state.get(LOADED, False):
        return None
    if value is None:
        return None
    if value == "":
        st.session_state[LOADED] = True
        return None
    st.session_state[LOADED] = True
    try:
        data = json.loads(value)
        notes, bank = data.get("notes", []), data.get("bank", [])
        if not isinstance(notes, list) or not isinstance(bank, list):
            raise ValueError("notes/bank must be lists")
        good_notes = [n for n in notes if _valid_note(n)]
        good_bank = [q for q in bank if _valid_question(q)]
        bank_before = len(wn.bank())
        count = wn.import_json(json.dumps({"version": 1, "notes": good_notes, "bank": good_bank}))
        bank_added = len(wn.bank()) - bank_before
    except Exception:
        return "⚠ 이 브라우저에 저장된 오답노트를 읽지 못했어요. 새로 시작합니다."
    skipped = len(notes) + len(bank) - len(good_notes) - len(good_bank)
    parts = []
    if count or bank_added:
        extra = f"(문제 은행 {bank_added}개)" if bank_added else ""
        parts.append(f"이 브라우저에 저장된 오답 {count}개{extra}를 불러왔어요.")
    if skipped:
        parts.append(f"형식이 맞지 않는 {skipped}개는 건너뛰었어요.")
    return " ".join(parts) or None


def _valid_question(q) -> bool:
    """화면에서 바로 쓰는 필드가 있는지 확인한다 (예전 형식·손상된 저장분이 앱을 멈추지 않게)."""
    return (isinstance(q, dict) and isinstance(q.get("id"), str) and isinstance(q.get("question"), str)
            and isinstance(q.get("choices"), list) and len(q["choices"]) == 4
            and all(isinstance(c, str) for c in q["choices"])
            and type(q.get("answer")) is int and 1 <= q["answer"] <= 4)


def _valid_note(n) -> bool:
    if not _valid_question(n):
        return False
    my = n.get("my_answer")
    return (all(k in n for k in ("doc", "explanation"))
            and isinstance(n.get("concept"), str)
            and (my is None or (type(my) is int and 1 <= my <= 4))
            and all(n.get(k) in (None, *wn.CAUSES) for k in ("cause", "suggested"))
            and type(n.get("box")) is int and 0 <= n["box"] < len(wn.INTERVALS)
            and isinstance(n.get("due"), str) and isinstance(n.get("history"), list)
            and isinstance(n.get("mastered"), bool))


def clear() -> None:
    """세션의 오답노트를 비운다. 다음 저장에서 빈 값이 나가 브라우저 저장분도 지워진다."""
    st.session_state["wrongnote"] = []
    st.session_state["question_bank"] = []
    st.session_state["bank_solved"] = set()
    st.session_state[LOADED] = True
