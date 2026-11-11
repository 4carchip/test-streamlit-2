"""이 브라우저의 localStorage와 세션을 맞춘다 (오답노트 + 선택한 경우 API 설정).

숨은 컴포넌트는 반드시 '하나'만 둔다. 둘이면 처음 값이 따로 도착해 rerun이 겹치고,
늦게 온 rerun이 화면에 남은 옛 위젯 값(체크 해제·빈 칸)으로 세션을 덮어써
저장된 API 설정이 지워진다 (2026-11-11 재현).

무엇을 저장·복원할지는 persist(오답노트)와 apikeys(API 설정)가 정하고, 여기서는 묶어서 주고받기만 한다.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core import apikeys, persist

_COMPONENT_DIR = Path(__file__).resolve().parent / "storage_component"
_COMPONENT = None


def _component():
    global _COMPONENT
    if _COMPONENT is None:
        _COMPONENT = components.declare_component("browser_storage", path=str(_COMPONENT_DIR))
    return _COMPONENT


def items() -> dict[str, str | None]:
    """브라우저에 보낼 {저장 키: 저장 값}. None은 '아직 불러오기 전이니 건드리지 말 것', ""는 삭제."""
    return {persist.STORAGE_KEY: persist.save_arg(), apikeys.STORAGE_KEY: apikeys.save_arg()}


def handle(value) -> bool:
    """브라우저가 보낸 {저장 키: 저장 값}을 반영한다. 화면을 다시 그려야 하면 True."""
    if not isinstance(value, dict):      # 아직 응답 전(None)이거나 알 수 없는 값: 아무것도 하지 않는다
        return False
    msg = persist.apply_loaded(value.get(persist.STORAGE_KEY) or "")
    if msg:
        st.session_state[persist.MSG] = msg
    api_pending = apikeys.apply_loaded(value.get(apikeys.STORAGE_KEY) or "")
    return bool(msg) or api_pending


def sync() -> None:
    """app.py에서 페이지를 그린 뒤 한 번 호출한다. 실패해도 앱은 계속 동작한다."""
    try:
        value = _component()(items=items(), key="browser_storage", default=None)
    except Exception:
        return
    if handle(value):
        st.rerun()
