import streamlit as st

from core import answer, demo


def test_current_mode_default_hybrid():
    assert answer.current_mode() == answer.MODE_HYBRID


def test_current_mode_respects_grounded():
    st.session_state[answer.SS_MODE] = answer.MODE_GROUNDED
    assert answer.current_mode() == answer.MODE_GROUNDED


def test_current_mode_invalid_falls_back():
    st.session_state[answer.SS_MODE] = "bogus"
    assert answer.current_mode() == answer.MODE_HYBRID


def test_general_messages_structure():
    hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    msgs = answer.general_messages("Q?", hist)
    assert msgs[0] == {"role": "system", "content": answer.GENERAL_SYSTEM}
    assert msgs[1:3] == hist
    assert msgs[-1] == {"role": "user", "content": "Q?"}


def test_grounded_messages_system_by_mode():
    g = answer.grounded_messages(answer.MODE_GROUNDED, "Q?", "CTX", [])
    h = answer.grounded_messages(answer.MODE_HYBRID, "Q?", "CTX", [])
    assert g[0]["content"] == answer.GROUNDED_SYSTEM
    assert h[0]["content"] == answer.NOTES_SYSTEM
    for msgs in (g, h):
        assert msgs[-1]["role"] == "user"
        assert "[근거]\nCTX" in msgs[-1]["content"]
        assert "[질문]\nQ?" in msgs[-1]["content"]


def test_general_system_forbids_page_citation():
    assert "쪽 번호" in answer.GENERAL_SYSTEM
    assert "인용하지 않는다" in answer.GENERAL_SYSTEM


def test_compose_hybrid_both():
    out = answer.compose(answer.MODE_HYBRID, "GEN", "GRD")
    a, b = out.index("#### 🤖 AI 설명"), out.index("#### 📄 내 자료에서")
    assert a < b
    assert "AI 모델의 일반 지식 · 업로드 자료 근거 없음 · 교차검증 대상 아님" in out
    assert out.index("GEN") < b < out.index("GRD")


def test_compose_hybrid_no_grounded():
    assert answer.NO_MATERIAL in answer.compose(answer.MODE_HYBRID, "GEN", None)


def test_compose_hybrid_no_general():
    assert "AI 설명을 받지 못했습니다" in answer.compose(answer.MODE_HYBRID, None, "GRD")


def test_compose_grounded():
    assert answer.compose(answer.MODE_GROUNDED, "GEN", "GRD") == "GRD"
    out = answer.compose(answer.MODE_GROUNDED, "GEN", None)
    assert out.startswith("업로드한 자료에서 관련 내용을 찾지 못했습니다. 다른 표현으로")
    assert "🤖" not in out and "GEN" not in out


def test_history_for_model_slices_like_today():
    msgs = [{"role": "user", "content": str(i), "meta": {}} for i in range(10)]
    out = answer.history_for_model(msgs)
    assert out == [{"role": "user", "content": str(i)} for i in range(3, 9)]


def test_demo_general():
    out = demo.respond("general", [{"role": "user", "content": "DIKW가 뭐야?"}], {})
    assert "데모 응답" in out and "일반 설명" in out


def test_demo_notes_same_as_answer():
    hint = {"chunks": [{"doc": "d.pdf", "page": 3, "text": "데이터는 가공되지 않은 객관적인 사실이다."}]}
    assert demo.respond("notes", [], hint) == demo.respond("answer", [], hint)
    assert "[d.pdf p.3]" in demo.respond("notes", [], hint)


def test_compose_hybrid_has_no_html_tags():
    """답변은 HTML 허용 없이 그린다 (모델 출력에 HTML이 섞여도 실행되지 않게). 라벨도 마크다운만 쓴다."""
    from core import answer
    out = answer.compose(answer.MODE_HYBRID, "g", "n")
    assert "<" not in out and ">" not in out


# ---- Copilot 크로스체크 반영 ----

def test_mapping_text_hybrid_ignores_general_explanation():
    """교차 출제 안내는 AI 일반 설명의 단어로 찾지 않는다 (자료와 무관한 개념이 섞이지 않게)."""
    from core import answer
    out = answer.mapping_text(answer.MODE_HYBRID, "윈도우 함수?", "자료 요약 RANK")
    assert out == "윈도우 함수?\n자료 요약 RANK"


def test_mapping_text_hybrid_without_material_uses_question_only():
    from core import answer
    assert answer.mapping_text(answer.MODE_HYBRID, "윈도우 함수?", None) == "윈도우 함수?"


def test_mapping_text_grounded_needs_material():
    from core import answer
    assert answer.mapping_text(answer.MODE_GROUNDED, "q", None) is None
    assert answer.mapping_text(answer.MODE_GROUNDED, "q", "자료 답") == "q\n자료 답"


def test_safe_verify_keeps_answer_when_verifier_fails(monkeypatch):
    """교차검증 호출이 실패해도 예외를 올리지 않고 '검증 실패' 결과를 돌려준다 (본문 답변은 유지)."""
    from core import crosscheck, llm
    from core.config import ModelCfg

    def boom(*a, **k):
        raise llm.LLMError("한도 초과")

    monkeypatch.setattr(llm, "chat", boom)
    model = ModelCfg(label="V", id="v", base_url="u", api_key="k")
    out = crosscheck.safe_verify(model, "ctx", "ans")
    assert out["overall"] == "⚠️"
    assert "한도 초과" in out["summary"]
    assert out["claims"] == []
    assert out["verifier"] == "V"
