import json

import pytest

from core import llm, quizgen
from core.config import ModelCfg
from core.documents import Chunk

MODEL = ModelCfg(label="M", id="m", base_url="u", api_key="k")
VERIFIER = ModelCfg(label="V", id="v", base_url="u", api_key="k")
CHUNKS = [Chunk(doc="d.pdf", page=1, text="SELECT 문은 데이터를 조회한다. " * 5)]


def _q(i):
    return {"question": f"문제 {i}", "choices": ["a", "b", "c", "d"], "answer": 2,
            "explanation": "e", "page": 1, "concept": "c"}


def _json(n):
    return json.dumps({"questions": [_q(i) for i in range(n)]}, ensure_ascii=False)


def _broken_json(n_ok):
    """n_ok개의 정상 문항 뒤에, 문자열 안에 이스케이프 안 된 큰따옴표가 있는 문항."""
    ok = ",".join(json.dumps(_q(i), ensure_ascii=False) for i in range(n_ok))
    bad = '{"question": "WHERE name = "kim" 의 결과는?", "choices": ["a","b","c","d"], "answer": 1}'
    return '{"questions": [' + ok + "," + bad + "]}"


@pytest.fixture
def script(monkeypatch):
    """chat_with_status 응답을 순서대로 돌려주고 호출 기록을 남긴다."""
    calls = []
    responses = []

    def fake(model, messages, **kw):
        calls.append({"model": model, "messages": messages, **kw})
        return responses.pop(0)

    monkeypatch.setattr(llm, "chat_with_status", fake)
    fake.calls = calls
    fake.responses = responses
    return fake


def test_valid_json_no_repair(script):
    script.responses.append((_json(5), False))
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert len(res.questions) == 5
    assert len(script.calls) == 1
    assert res.note == ""
    assert res.repaired is False and res.truncated is False


def test_broken_json_triggers_repair(script):
    script.responses.append((_broken_json(2), False))
    script.responses.append((_json(3), False))
    res = quizgen.generate_report(CHUNKS, 3, "기본", MODEL)
    assert [c["purpose"] for c in script.calls] == ["quiz", "repair"]
    assert res.repaired is True
    assert len(res.questions) == 3
    assert res.note == ""


def test_repair_also_broken_salvages_original(script):
    script.responses.append((_broken_json(3), False))
    script.responses.append(("이건 JSON이 아닙니다", False))
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert len(res.questions) == 3
    assert res.repaired is False
    assert "3문제만" in res.note


def test_truncated_skips_repair_and_salvages(script):
    full = _json(5)
    cut = full[: full.rfind('{"question"') + 40]   # 5번째 문항 중간에서 잘림
    script.responses.append((cut, True))
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert len(script.calls) == 1
    assert len(res.questions) == 4
    assert res.truncated is True
    assert "4문제만" in res.note


def test_nothing_salvageable(script):
    script.responses.append(("죄송합니다. 만들 수 없습니다.", False))
    script.responses.append(("still nothing", False))
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert res.questions == []
    assert "읽지 못했어요" in res.note


def test_salvage_handles_fence_and_leading_text():
    raw = "물론이죠!\n```json\n" + _json(2) + "\n```"
    assert len(quizgen.salvage_questions(raw)) == 2


def test_salvage_stops_at_broken_object():
    assert len(quizgen.salvage_questions(_broken_json(2))) == 2


def test_salvage_ignores_invalid_objects():
    bad = dict(_q(9), choices=["a", "b", "c"])
    raw = json.dumps({"questions": [_q(0), bad, _q(1)]}, ensure_ascii=False)
    assert len(quizgen.salvage_questions(raw)) == 2


def test_salvage_without_questions_key():
    assert quizgen.salvage_questions('{"foo": [1,2]}') == []
    assert quizgen.salvage_questions("") == []


def test_parse_questions_raises_on_broken():
    with pytest.raises(ValueError):
        quizgen.parse_questions(_broken_json(1))


def test_prompt_and_limits():
    prompt = quizgen.QUIZ_PROMPT.format(n=3, level="x", context="c", extra="")
    assert "작은따옴표" in prompt
    assert quizgen.QUIZ_MAX_TOKENS >= 6000
    assert quizgen.CHECK_MAX_TOKENS >= 1500
    assert quizgen.REPAIR_MAX_TOKENS >= 6000


def test_verifier_uses_larger_max_tokens(script, monkeypatch):
    script.responses.append((_json(2), False))
    seen = {}

    def fake_chat(model, messages, **kw):
        seen.update(kw)
        return json.dumps({"answers": [2, 2]})

    monkeypatch.setattr(llm, "chat", fake_chat)
    res = quizgen.generate_report(CHUNKS, 2, "기본", MODEL, VERIFIER)
    assert seen["max_tokens"] >= 1500
    assert all(q["check"] == "ok" for q in res.questions)


def test_generate_returns_list_without_raising(script):
    script.responses.append((_broken_json(2), False))
    script.responses.append(("garbage", False))
    out = quizgen.generate(CHUNKS, 5, "기본", MODEL)
    assert isinstance(out, list) and len(out) == 2


def test_repair_call_failure_still_salvages(monkeypatch):
    """복구 호출 자체가 실패해도(한도 초과·네트워크) 원문에서 살릴 수 있는 문제는 보여준다."""
    responses = [(_broken_json(3), False)]

    def fake(model, messages, **kw):
        if kw.get("purpose") == "repair":
            raise llm.LLMError("[429] 한도 초과")
        return responses.pop(0)

    monkeypatch.setattr(llm, "chat_with_status", fake)
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert len(res.questions) == 3
    assert "3문제만" in res.note


# ---- Copilot 크로스체크 반영 ----

def test_empty_repair_falls_back_to_salvage(script):
    """복구 응답이 유효하지만 빈 목록이면, 원문에서 살릴 수 있는 문제를 살린다."""
    script.responses.append((_broken_json(3), False))
    script.responses.append(('{"questions": []}', False))
    res = quizgen.generate_report(CHUNKS, 5, "기본", MODEL)
    assert len(res.questions) == 3
    assert res.repaired is False
    assert "3문제만" in res.note


def test_note_when_fewer_questions_even_if_json_valid(script):
    """JSON은 멀쩡하지만 형식이 틀린 문항이 걸러져 요청보다 적으면 안내한다."""
    bad = {"question": "보기 3개", "choices": ["a", "b", "c"], "answer": 1}
    raw = json.dumps({"questions": [_q(0), _q(1), bad]}, ensure_ascii=False)
    script.responses.append((raw, False))
    res = quizgen.generate_report(CHUNKS, 3, "기본", MODEL)
    assert len(res.questions) == 2
    assert "2문제만" in res.note
