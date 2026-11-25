from types import SimpleNamespace as NS

from core import answer, llm, usage
from core.config import ModelCfg


def _real_model():
    return ModelCfg(id="m", label="M", base_url="http://x", api_key="k")


def _resp(finish):
    return NS(choices=[NS(message=NS(content="abc"), finish_reason=finish)], usage=None)


def _patch(monkeypatch, finish):
    monkeypatch.setattr(llm, "_call", lambda *a, **k: _resp(finish))
    monkeypatch.setattr(usage, "record", lambda *a, **k: None)


def test_chat_with_status_truncated(monkeypatch):
    _patch(monkeypatch, "length")
    assert llm.chat_with_status(_real_model(), [{"role": "user", "content": "q"}]) == ("abc", True)


def test_chat_with_status_not_truncated(monkeypatch):
    _patch(monkeypatch, "stop")
    assert llm.chat_with_status(_real_model(), [{"role": "user", "content": "q"}]) == ("abc", False)


def test_chat_still_returns_text(monkeypatch):
    _patch(monkeypatch, "length")
    assert llm.chat(_real_model(), [{"role": "user", "content": "q"}]) == "abc"


def test_chat_with_status_demo(monkeypatch):
    monkeypatch.setattr(usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(llm.demo, "respond", lambda *a, **k: "demo text")
    model = ModelCfg(id="demo", label="D", base_url="", api_key="", demo=True)
    assert model.is_demo
    assert llm.chat_with_status(model, [{"role": "user", "content": "q"}]) == ("demo text", False)


def test_finish_unchanged_when_not_truncated():
    assert answer.finish("hello", False) == "hello"


def test_finish_appends_note():
    out = answer.finish("hello  \n", True)
    assert out == "hello\n\n" + answer.TRUNCATED_NOTE


def test_finish_empty_truncated():
    assert "마치지 못했습니다" in answer.finish("  ", True)


def test_demote_basic():
    assert answer.demote_headings("# A") == "**A**"
    assert answer.demote_headings("### B ##") == "**B**"
    assert answer.demote_headings("#tag") == "#tag"
    assert answer.demote_headings("normal\ntext") == "normal\ntext"


def test_demote_skips_fences():
    t = "```python\n# comment\n```\n# H"
    assert answer.demote_headings(t) == "```python\n# comment\n```\n**H**"
    t = "~~~\n# c\n~~~"
    assert answer.demote_headings(t) == t


def test_compose_hybrid_demotes():
    out = answer.compose(answer.MODE_HYBRID, "# 제목\n본문", None)
    assert "**제목**" in out
    assert "#### 🤖 AI 설명" in out
    assert not any(line.startswith("# ") for line in out.splitlines())


def test_compose_grounded_demotes():
    out = answer.compose(answer.MODE_GROUNDED, None, "## 제목\n본문")
    assert "**제목**" in out and "## " not in out


def test_search_query():
    u1 = {"role": "user", "content": "윈도우 함수가 뭐야?"}
    a = {"role": "assistant", "content": "답"}
    u2 = {"role": "user", "content": "예시로 설명해줘"}
    assert answer.search_query("예시로 설명해줘", [u1, a, u2]) == "윈도우 함수가 뭐야?\n예시로 설명해줘"
    assert answer.search_query("q", [{"role": "user", "content": "q"}]) == "q"
    u0 = {"role": "user", "content": "old"}
    assert answer.search_query("예시로 설명해줘", [u0, a, u1, a, u2]).startswith("윈도우 함수가 뭐야?\n")


def test_prompts_and_limits():
    assert "제목(#)" in answer.GENERAL_SYSTEM
    assert "제목(#)" in answer.NOTES_SYSTEM
    assert "6행" in answer.GENERAL_SYSTEM
    assert answer.GENERAL_MAX_TOKENS >= 4000


# ---- Copilot 크로스체크 반영 ----

def test_finish_closes_unclosed_code_fence_before_note():
    """SQL 코드 블록 중간에서 잘리면 안내 문구가 코드 블록에 갇히지 않게 펜스를 닫는다."""
    from core import answer
    out = answer.finish("예시:\n```sql\nSELECT RANK() OVER (ORDER", True)
    body, note = out.rsplit("\n\n", 1)
    assert note == answer.TRUNCATED_NOTE
    assert body.count("```") == 2


def test_finish_keeps_closed_fences_as_is():
    from core import answer
    out = answer.finish("```sql\nSELECT 1;\n```\n설명 중", True)
    assert out.count("```") == 2


def test_demote_headings_respects_longer_fence():
    """```` 로 연 블록 안의 ``` 는 닫는 펜스가 아니다 → 그 안의 # 주석은 그대로."""
    from core import answer
    text = "````markdown\n```python\n# 주석\n```\n# 여전히 블록 안\n````\n# 제목"
    out = answer.demote_headings(text)
    assert "# 주석" in out and "# 여전히 블록 안" in out
    assert out.endswith("**제목**")
