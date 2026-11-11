"""두 저장소(오답노트·API 설정)를 숨은 컴포넌트 하나로 처리하는지 확인한다.

컴포넌트가 둘이면 처음 값이 따로 도착해 rerun이 겹치고, 늦게 온 rerun이 화면의 옛 위젯 값
(체크 해제·빈 칸)으로 세션을 덮어써 저장된 API 설정이 지워졌다 (2026-11-11 브라우저에서 재현).
"""

import json

import streamlit as st

from core import apikeys, browser_storage, config, persist, wrongnote as wn
from tests.test_persist import make_export, make_note


def api_json(**values):
    return json.dumps({"version": 1, "values": values})


def test_items_before_load_are_none_for_both():
    assert browser_storage.items() == {persist.STORAGE_KEY: None, apikeys.STORAGE_KEY: None}


def test_handle_none_keeps_both_unloaded():
    assert browser_storage.handle(None) is False
    assert browser_storage.items() == {persist.STORAGE_KEY: None, apikeys.STORAGE_KEY: None}


def test_handle_non_dict_is_treated_as_no_answer():
    assert browser_storage.handle("garbage") is False
    assert browser_storage.items() == {persist.STORAGE_KEY: None, apikeys.STORAGE_KEY: None}


def test_handle_empty_values_marks_both_loaded():
    assert browser_storage.handle({persist.STORAGE_KEY: "", apikeys.STORAGE_KEY: ""}) is False
    assert browser_storage.items() == {persist.STORAGE_KEY: "", apikeys.STORAGE_KEY: ""}


def test_handle_restores_both_in_one_step():
    value = {
        persist.STORAGE_KEY: make_export(make_note("a")),
        apikeys.STORAGE_KEY: api_json(**{config.SS_MULTIAI_KEY: "sk-test"}),
    }
    assert browser_storage.handle(value) is True
    assert [n["id"] for n in wn.notes()] == ["a"]
    assert st.session_state[apikeys.PENDING] == {config.SS_MULTIAI_KEY: "sk-test"}
    assert st.session_state[persist.MSG]


def test_handle_wrongnote_only_needs_rerun():
    assert browser_storage.handle({persist.STORAGE_KEY: make_export(make_note("a")),
                                   apikeys.STORAGE_KEY: ""}) is True


def test_handle_missing_key_counts_as_empty():
    assert browser_storage.handle({}) is False
    assert browser_storage.items() == {persist.STORAGE_KEY: "", apikeys.STORAGE_KEY: ""}
