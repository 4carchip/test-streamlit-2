"""왼쪽 사이드바: AI 모델 선택 + 교차검증(선택) + 사용량."""

from __future__ import annotations

import streamlit as st

from core import usage
from core.config import ModelCfg, cost_unit, credit_limit, is_demo_mode, load_models

PLATFORM_URL = "https://ai.hknu.ac.kr"


def _fmt(n: float) -> str:
    return f"{n:,.0f}" if n >= 100 else f"{n:,.2f}"


def _usage_table(data: dict, unit: str) -> None:
    if not data:
        st.caption("아직 호출 기록이 없습니다.")
        return
    for label, row in data.items():
        est = " (추정)" if row.get("estimated") else ""
        cost = f" · {_fmt(row['cost'])} {unit}" if row.get("cost") else ""
        st.markdown(
            f"**{label}**  \n"
            f"{row['calls']}회 · 입력 {row['input']:,} / 출력 {row['output']:,} 토큰{est}{cost}"
        )


def render_controls() -> None:
    """페이지 실행 전에 그린다: 모델 선택·교차검증 설정."""
    models = load_models()
    labels = [m.label for m in models]

    with st.sidebar:
        st.markdown("### 🤖 AI 모델")
        if is_demo_mode():
            st.warning("데모 모드: API 키가 설정되지 않아 실제 AI를 호출하지 않습니다. 'API 연결 안내' 페이지를 참고하세요.")

        st.selectbox("설명 모델", labels, key="main_model", help="질문에 답하고 문제를 만드는 모델")

        st.session_state.setdefault("crosscheck_on", False)
        st.toggle(
            "교차검증 사용 (선택)",
            key="crosscheck_on",
            help="다른 모델이 '자료만 보고' 답변을 다시 판정합니다. 호출이 한 번 더 일어나 사용량이 늘어납니다.",
        )
        if st.session_state.get("crosscheck_on"):
            others = [l for l in labels if l != st.session_state.get("main_model")] or labels
            st.selectbox("검증 모델", others, key="verifier_model", help="설명 모델과 다른 회사의 모델을 추천합니다.")
            if len(labels) < 2:
                st.caption("모델이 하나뿐이라 같은 모델로 검증합니다. 효과가 제한적입니다.")


def render_usage() -> None:
    """페이지 실행 후에 그린다: 방금 호출한 사용량까지 반영된다."""
    with st.sidebar:
        st.divider()
        st.markdown("### 📊 사용량")
        unit = cost_unit()
        sess = usage.session_usage()
        total_in = sum(r["input"] for r in sess.values())
        total_out = sum(r["output"] for r in sess.values())
        calls = sum(r["calls"] for r in sess.values())
        c1, c2 = st.columns(2)
        c1.metric("이번 세션 호출", f"{calls}회")
        c2.metric("토큰", f"{(total_in + total_out):,}")
        _usage_table(sess, unit)

        limit = credit_limit()
        if limit > 0:
            spent = sum(r["cost"] for r in usage.total_usage().values())
            st.progress(min(spent / limit, 1.0), text=f"예산 대비 사용: {_fmt(spent)} / {_fmt(limit)} {unit} (추정)")

        with st.expander("앱 누적 사용량 (이 서버 기준)"):
            _usage_table(usage.total_usage(), unit)

        b1, b2 = st.columns(2)
        if b1.button("세션 기록 지우기", use_container_width=True):
            usage.reset_session()
            st.rerun()
        b2.link_button("실제 크레딧 확인", PLATFORM_URL, use_container_width=True)
        st.caption("앱에 표시되는 값은 토큰 수 기반 추정치입니다. 정확한 잔여 크레딧은 멀티AI 대시보드에서 확인하세요.")


def selected_models() -> tuple[ModelCfg, ModelCfg | None]:
    """(설명 모델, 검증 모델 또는 None)."""
    models = {m.label: m for m in load_models()}
    first = next(iter(models.values()))
    main = models.get(st.session_state.get("main_model"), first)
    verifier = None
    if st.session_state.get("crosscheck_on"):
        verifier = models.get(st.session_state.get("verifier_model")) or next(
            (m for m in models.values() if m.label != main.label), main
        )
    return main, verifier
