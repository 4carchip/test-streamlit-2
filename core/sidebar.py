"""왼쪽 사이드바: API 설정 + AI 모델 선택 + 교차검증(선택) + 사용량."""

from __future__ import annotations

import streamlit as st

from core import usage
from core.config import (
    LABELS, MODE_DIRECT, MODE_LABEL, MODE_MULTIAI, SS_MULTIAI_KEY, SS_MULTIAI_URL, SS_VENDOR_KEY,
    VENDOR_NAME, ModelCfg, cost_unit, credit_limit, default_id, default_mode, resolve,
    secret_multiai_key, secret_multiai_url, secret_vendor_key,
)

PLATFORM_URL = "https://ai.hknu.ac.kr"


def _fmt(n: float) -> str:
    return f"{n:,.0f}" if n >= 100 else f"{n:,.4f}" if n < 0.01 else f"{n:,.2f}"


def _kept_input(label: str, store_key: str, **kwargs) -> str:
    """페이지·연결 방식을 바꿔도 입력값이 지워지지 않는 text_input.

    Streamlit은 화면에서 사라진 위젯의 값을 지우므로, 값을 별도 키(store_key)에 보관했다가 복원한다.
    """
    widget_key = "_w_" + store_key
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(store_key, "")
    value = st.text_input(label, key=widget_key, **kwargs)
    st.session_state[store_key] = value
    return value


def _secret_hint(value: str) -> str:
    return "비워 두면 Secrets에 저장된 값을 씁니다." if value else "Secrets에 저장된 값이 없습니다."


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


def _model_status(m: ModelCfg) -> str:
    price = f"100만 토큰당 입력 {m.input_price:g} / 출력 {m.output_price:g} USD"
    state = "🟢 연결 준비됨" if not m.is_demo else "⚪ 키 없음 → 데모 응답"
    return f"{state}  \nID `{m.id}` · {price}"


def _default_verifier(main_label: str) -> str:
    """설명 모델과 다른 회사 모델을 기본 검증 모델로 고른다."""
    if main_label.startswith("GPT"):
        return "Claude Sonnet 5.5"
    return "GPT-6.1 Sol"


def render_controls() -> None:
    """페이지 실행 전에 그린다: API 설정·모델 선택·교차검증."""
    with st.sidebar:
        # ---------------- API 설정 ----------------
        st.markdown("### 🔐 API 설정")
        st.session_state.setdefault("conn_mode", default_mode())
        st.radio(
            "연결 방식", [MODE_MULTIAI, MODE_DIRECT], key="conn_mode", horizontal=True,
            format_func=lambda m: MODE_LABEL[m],
            help="학교 멀티AI: 주소+키 하나로 GPT·Claude 모두 사용 / 개별 API 키: 회사별 키로 직접 호출",
        )
        if st.session_state["conn_mode"] == MODE_MULTIAI:
            _kept_input("API 주소 (base_url)", SS_MULTIAI_URL,
                        placeholder=secret_multiai_url() or "https://…/v1",
                        help="멀티AI 플랫폼 예시 코드에 나오는 주소. " + _secret_hint(secret_multiai_url()))
            _kept_input("API 키", SS_MULTIAI_KEY, type="password",
                        placeholder="Secrets 값 사용 중" if secret_multiai_key() else "멀티AI에서 발급받은 키",
                        help=_secret_hint(secret_multiai_key()))
        else:
            for vendor in ("openai", "anthropic"):
                who = "GPT" if vendor == "openai" else "Claude"
                _kept_input(f"{VENDOR_NAME[vendor]} API 키 ({who})", SS_VENDOR_KEY[vendor], type="password",
                            placeholder="Secrets 값 사용 중" if secret_vendor_key(vendor) else "",
                            help=_secret_hint(secret_vendor_key(vendor)))
        st.caption("입력한 키는 이 브라우저 세션에만 보관되며 어디에도 저장되지 않습니다.")

        # ---------------- 모델 선택 ----------------
        st.markdown("### 🤖 AI 모델")
        st.selectbox("설명 모델", LABELS, key="main_model", help="질문에 답하고 문제를 만드는 모델")
        main = resolve(st.session_state["main_model"])
        st.caption(_model_status(main))
        if main.is_demo:
            st.warning("API 키가 없어 데모 응답으로 동작합니다. 위에 키를 입력하세요.")

        with st.expander("모델 ID 직접 지정 (고급)"):
            st.caption("학교 플랫폼의 모델 ID가 공식 ID와 다를 때만 바꾸세요. 비우면 기본값으로 돌아갑니다.")
            label = st.session_state["main_model"]
            _kept_input(f"{label} 모델 ID", f"cfg_id_{label}", placeholder=default_id(label))

        # ---------------- 교차검증 (선택) ----------------
        st.session_state.setdefault("crosscheck_on", False)
        st.toggle(
            "교차검증 사용 (선택)", key="crosscheck_on",
            help="다른 모델이 '자료만 보고' 답변을 다시 판정합니다. 호출이 한 번 더 일어나 사용량이 늘어납니다.",
        )
        if st.session_state["crosscheck_on"]:
            others = [l for l in LABELS if l != main.label]
            if st.session_state.get("verifier_model") not in others:
                st.session_state["verifier_model"] = _default_verifier(main.label)
            st.selectbox("검증 모델", others, key="verifier_model", help="설명 모델과 다른 회사의 모델을 추천합니다.")
            ver = resolve(st.session_state["verifier_model"])
            if ver.is_demo:
                st.caption("⚪ 검증 모델 키가 없어 데모 판정으로 동작합니다.")


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
        cost = sum(r["cost"] for r in sess.values())
        c1, c2 = st.columns(2)
        c1.metric("이번 세션 호출", f"{calls}회")
        c2.metric("토큰", f"{(total_in + total_out):,}")
        if cost:
            st.metric("예상 비용", f"{_fmt(cost)} {unit}")
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
        st.caption("비용은 공식 가격 × 토큰 수로 계산한 추정치입니다. 학교 크레딧 차감량은 멀티AI 대시보드에서 확인하세요.")


def selected_models() -> tuple[ModelCfg, ModelCfg | None]:
    """(설명 모델, 검증 모델 또는 None)."""
    main = resolve(st.session_state.get("main_model", LABELS[0]))
    verifier = None
    if st.session_state.get("crosscheck_on"):
        verifier = resolve(st.session_state.get("verifier_model") or _default_verifier(main.label))
    return main, verifier
