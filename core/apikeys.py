"""API 설정(연결 방식·주소·키)을 사용자가 동의할 때만 이 브라우저의 localStorage에 기억한다.

서버에는 아무것도 저장하지 않는다. 브라우저 입출력은 core/browser_storage.py가 맡는다.
복원 값은 위젯이 이미 만들어진 뒤에 도착하므로, PENDING에 보관했다가 rerun 후
render_controls() 맨 위(apply_pending)에서 위젯 생성 전에 반영한다.
"""

from __future__ import annotations

import json

import streamlit as st

from core import config

STORAGE_KEY = "certmate.api.v1"
REMEMBER = "remember_api"
LOADED = "_api_storage_loaded"
PENDING = "_api_pending"

FIELDS = ["conn_mode", config.SS_MULTIAI_URL, config.SS_MULTIAI_KEY, *config.SS_VENDOR_KEY.values()]


def payload() -> str:
    """저장할 문자열. 기억하기가 꺼져 있으면 빈 문자열(= 저장된 값 삭제)."""
    if not st.session_state.get(REMEMBER):
        return ""
    values = {}
    for k in FIELDS:
        v = st.session_state.get(k)
        if isinstance(v, str) and v.strip():
            values[k] = v.strip()
    return json.dumps({"version": 1, "values": values}, ensure_ascii=False)


def save_arg() -> str | None:
    """불러오기 전에는 None (빈 새 세션이 기존 저장분을 덮어쓰지 않게)."""
    if not st.session_state.get(LOADED, False):
        return None
    return payload()


def apply_loaded(value: str | None) -> bool:
    """브라우저가 보낸 값을 PENDING에 보관한다. rerun이 필요하면 True."""
    if st.session_state.get(LOADED, False) or value is None:
        return False
    st.session_state[LOADED] = True
    if value == "":
        return False
    try:
        data = json.loads(value)
        values = data["values"]
        if not isinstance(values, dict):
            return False
        kept = {}
        for k, v in values.items():
            if k not in FIELDS or not isinstance(v, str) or not v.strip():
                continue
            if k == "conn_mode" and v.strip() not in (config.MODE_MULTIAI, config.MODE_DIRECT):
                continue
            kept[k] = v.strip()
    except Exception:
        return False
    if not kept:
        return False
    st.session_state[PENDING] = kept
    return True


def apply_pending() -> None:
    """render_controls() 맨 위에서, 위젯을 만들기 전에 호출한다."""
    pending = st.session_state.pop(PENDING, None)
    if not pending:
        return
    for k, v in pending.items():
        st.session_state[k] = v
        if k != "conn_mode":
            st.session_state["_w_" + k] = v
    st.session_state[REMEMBER] = True
