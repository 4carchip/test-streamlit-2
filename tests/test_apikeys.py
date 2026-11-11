import json

import streamlit as st

from core import apikeys, config


def ss():
    return st.session_state


def test_payload_empty_when_remember_off():
    ss()[config.SS_MULTIAI_KEY] = "sk-1"
    assert apikeys.payload() == ""


def test_payload_only_non_empty_fields():
    ss()[apikeys.REMEMBER] = True
    ss()["conn_mode"] = config.MODE_DIRECT
    ss()[config.SS_MULTIAI_KEY] = "  sk-1  "
    ss()[config.SS_MULTIAI_URL] = "   "
    ss()["cfg_id_GPT-6.1 Sol"] = "x"
    ss()["unrelated"] = "y"
    data = json.loads(apikeys.payload())
    assert data["version"] == 1
    assert data["values"] == {"conn_mode": config.MODE_DIRECT, config.SS_MULTIAI_KEY: "sk-1"}


def test_payload_conn_mode_alone():
    ss()[apikeys.REMEMBER] = True
    ss()["conn_mode"] = config.MODE_MULTIAI
    assert json.loads(apikeys.payload())["values"] == {"conn_mode": config.MODE_MULTIAI}


def test_save_arg_none_before_load():
    ss()[apikeys.REMEMBER] = True
    assert apikeys.save_arg() is None


def test_apply_loaded_none_stays_unloaded():
    assert apikeys.apply_loaded(None) is False
    assert apikeys.save_arg() is None


def test_apply_loaded_empty_marks_loaded():
    assert apikeys.apply_loaded("") is False
    assert ss()[apikeys.LOADED] is True
    assert apikeys.save_arg() == ""


def test_apply_loaded_valid_filters():
    raw = json.dumps({"version": 1, "values": {
        "conn_mode": "bogus", config.SS_MULTIAI_URL: " http://x/v1 ", config.SS_MULTIAI_KEY: 5,
        "evil": "z", config.SS_VENDOR_KEY["openai"]: "  "}})
    assert apikeys.apply_loaded(raw) is True
    assert ss()[apikeys.PENDING] == {config.SS_MULTIAI_URL: "http://x/v1"}
    assert ss()[apikeys.LOADED] is True


def test_apply_loaded_valid_conn_mode_kept():
    raw = json.dumps({"values": {"conn_mode": config.MODE_DIRECT}})
    assert apikeys.apply_loaded(raw) is True
    assert ss()[apikeys.PENDING] == {"conn_mode": config.MODE_DIRECT}


def test_apply_loaded_second_call_ignored():
    apikeys.apply_loaded("")
    raw = json.dumps({"values": {config.SS_MULTIAI_KEY: "k"}})
    assert apikeys.apply_loaded(raw) is False
    assert apikeys.PENDING not in ss()


def test_apply_loaded_invalid_json():
    assert apikeys.apply_loaded("{not json") is False
    assert ss()[apikeys.LOADED] is True
    assert apikeys.PENDING not in ss()


def test_apply_loaded_wrong_shape():
    assert apikeys.apply_loaded(json.dumps({"values": "x"})) is False
    assert ss()[apikeys.LOADED] is True
    assert apikeys.PENDING not in ss()


def test_apply_loaded_nothing_kept():
    assert apikeys.apply_loaded(json.dumps({"values": {"evil": "z"}})) is False
    assert ss()[apikeys.LOADED] is True
    assert apikeys.PENDING not in ss()


def test_apply_pending_sets_keys():
    ss()[apikeys.PENDING] = {"conn_mode": config.MODE_DIRECT, config.SS_MULTIAI_KEY: "k"}
    apikeys.apply_pending()
    assert ss()["conn_mode"] == config.MODE_DIRECT
    assert "_w_conn_mode" not in ss()
    assert ss()[config.SS_MULTIAI_KEY] == "k"
    assert ss()["_w_" + config.SS_MULTIAI_KEY] == "k"
    assert ss()[apikeys.REMEMBER] is True
    assert apikeys.PENDING not in ss()


def test_apply_pending_noop_without_pending():
    apikeys.apply_pending()
    assert ss() == {}


def test_unchecking_clears_storage():
    raw = json.dumps({"values": {config.SS_MULTIAI_KEY: "k"}})
    apikeys.apply_loaded(raw)
    apikeys.apply_pending()
    assert apikeys.save_arg() != ""
    ss()[apikeys.REMEMBER] = False
    assert apikeys.save_arg() == ""
