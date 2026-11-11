import json

import pytest

from core import persist, wrongnote as wn


def make_note(qid="n1", question="질문?"):
    return {
        "id": qid, "doc": "a.pdf", "question": question, "choices": ["a", "b", "c", "d"],
        "answer": 1, "my_answer": 2, "explanation": "해설", "page": 3, "concept": "개념",
        "suggested": "개념 미숙지", "reason": "이유", "cause": "개념 미숙지", "confirmed": False,
        "box": 0, "due": "2026-01-02", "added": "2026-01-01", "history": [], "mastered": False,
    }


def make_export(*notes, bank=()):
    return json.dumps({"version": 1, "notes": list(notes), "bank": list(bank)}, ensure_ascii=False)


def test_payload_empty():
    assert persist.payload() == ""


def test_payload_with_notes():
    wn.notes().append(make_note())
    data = json.loads(persist.payload())
    assert data["notes"][0]["id"] == "n1"


def test_save_arg_none_before_load():
    wn.notes().append(make_note())
    assert persist.save_arg() is None


def test_apply_loaded_none_stays_unloaded():
    assert persist.apply_loaded(None) is None
    assert persist.save_arg() is None


def test_apply_loaded_empty_marks_loaded():
    assert persist.apply_loaded("") is None
    assert persist.save_arg() == ""


def test_apply_loaded_imports_notes_and_bank():
    exp = make_export(make_note("a"), make_note("b", "다른?"), bank=[make_q()])
    msg = persist.apply_loaded(exp)
    assert msg and "2" in msg
    assert len(wn.notes()) == 2
    assert len(wn.bank()) == 1
    assert persist.save_arg() != ""


def test_restore_merges_without_duplicating():
    wn.notes().append(make_note("a"))
    msg = persist.apply_loaded(make_export(make_note("a"), make_note("b", "다른?")))
    assert "1" in msg
    assert sorted(n["id"] for n in wn.notes()) == ["a", "b"]


def test_apply_loaded_second_call_ignored():
    exp = make_export(make_note("a"))
    persist.apply_loaded(exp)
    assert persist.apply_loaded(make_export(make_note("z"))) is None
    assert [n["id"] for n in wn.notes()] == ["a"]
    persist.apply_loaded(exp)
    assert len(wn.notes()) == 1


def test_invalid_json_returns_warning():
    msg = persist.apply_loaded("{not json")
    assert msg.startswith("⚠")
    assert persist.save_arg() is not None


def test_clear_then_save_arg_empty():
    wn.notes().append(make_note())
    persist.apply_loaded("")
    persist.clear()
    assert persist.save_arg() == ""
    assert wn.notes() == [] and wn.bank() == []


# ---- Copilot 크로스체크 반영: 문제 은행만 복원 / 구조가 깨진 저장분 ----

def make_q(qid="q1"):
    return {"id": qid, "question": "x?", "choices": ["a", "b", "c", "d"], "answer": 2,
            "explanation": "e", "page": 1, "concept": "개념"}


def test_bank_only_restore_returns_message():
    msg = persist.apply_loaded(make_export(bank=[make_q()]))
    assert msg and not msg.startswith("⚠")
    assert len(wn.bank()) == 1


def test_note_missing_fields_is_skipped():
    broken = make_note("bad")
    del broken["mastered"]
    msg = persist.apply_loaded(make_export(make_note("ok"), broken))
    assert [n["id"] for n in wn.notes()] == ["ok"]
    assert "1개는 건너뛰" in msg


def test_note_with_unknown_cause_is_skipped():
    weird = make_note("w")
    weird["cause"] = "없는 원인"
    persist.apply_loaded(make_export(weird))
    assert wn.notes() == []


def test_wrong_top_level_shape_returns_warning():
    msg = persist.apply_loaded(json.dumps({"version": 1, "notes": "oops"}))
    assert msg.startswith("⚠")
    assert wn.notes() == []
    assert persist.save_arg() is not None
