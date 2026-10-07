"""모델 목록과 API 연결 설정.

API 키는 두 곳에서 받을 수 있다.
1. 사이드바 입력칸 (이 브라우저 세션에만 보관, 저장하지 않음)
2. Streamlit secrets  (.streamlit/secrets.toml 또는 Streamlit Cloud > Settings > Secrets)
사이드바 입력이 비어 있으면 secrets 값을 쓰고, 둘 다 없으면 그 모델은 '데모 응답'으로 동작한다.

연결 방식
- 학교 멀티AI: 주소(base_url) 하나 + 키 하나로 GPT·Claude를 모두 호출 (OpenAI 호환 방식 가정)
- 개별 API 키: GPT는 OpenAI 키, Claude는 Anthropic 키로 각 회사 API를 직접 호출
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

# ---------------------------------------------------------------------------
# 모델 목록 (사이드바 선택창 순서 그대로)
# id는 각 회사 공식 API 기준. 학교 플랫폼 ID가 다르면 secrets의 [model_ids]나
# 사이드바 '모델 ID 직접 지정'에서 바꾸면 된다.
# 가격: 100만 토큰당 USD (공식 발표 기준, 2026년 10월 확인 — 사용량 '추정'에만 쓰임)
# ---------------------------------------------------------------------------
CATALOG = [
    # label,              vendor,      id,                   입력,  출력
    ("GPT-6.1 Sol",       "openai",    "gpt-6.1-sol",        2.0,  10.0),
    ("GPT-6 Sol",         "openai",    "gpt-6-sol",          2.0,  10.0),
    ("GPT-6 Luna",        "openai",    "gpt-6-luna",         0.1,   0.5),
    ("GPT-5.6 Sol",       "openai",    "gpt-5.6-sol",        4.0,  20.0),
    ("Claude Opus 5.5",   "anthropic", "claude-opus-5-5",    4.0,  20.0),
    ("Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5",  2.0,  10.0),
    ("Claude Opus 5",     "anthropic", "claude-opus-5",      5.0,  25.0),
    ("Claude Sonnet 5",   "anthropic", "claude-sonnet-5",    2.0,  10.0),
]
LABELS = [c[0] for c in CATALOG]
_BY_LABEL = {c[0]: c for c in CATALOG}

VENDOR_NAME = {"openai": "OpenAI", "anthropic": "Anthropic"}
VENDOR_URL = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1/",   # Anthropic의 OpenAI SDK 호환 주소
}
VENDOR_SECRET = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

MODE_MULTIAI = "multiai"
MODE_DIRECT = "direct"
MODE_LABEL = {MODE_MULTIAI: "학교 멀티AI", MODE_DIRECT: "개별 API 키"}

# 사이드바 입력값이 저장되는 session_state 키
SS_MULTIAI_URL = "cfg_multiai_url"
SS_MULTIAI_KEY = "cfg_multiai_key"
SS_VENDOR_KEY = {"openai": "cfg_openai_key", "anthropic": "cfg_anthropic_key"}


@dataclass(frozen=True)
class ModelCfg:
    label: str
    id: str
    base_url: str
    api_key: str
    input_price: float = 0.0
    output_price: float = 0.0
    vendor: str = ""
    demo: bool = False

    @property
    def is_demo(self) -> bool:
        return self.demo


def _secrets() -> dict:
    """secrets.toml이 없어도 오류 없이 빈 dict를 돌려준다."""
    try:
        return st.secrets.to_dict()
    except Exception:
        return {}


def multiai_settings() -> dict:
    return _secrets().get("multiai", {}) or {}


def _ss(key: str) -> str:
    return str(st.session_state.get(key, "") or "").strip()


def secret_multiai_url() -> str:
    return str(multiai_settings().get("base_url", "") or os.getenv("MULTIAI_BASE_URL", "")).strip()


def secret_multiai_key() -> str:
    return str(multiai_settings().get("api_key", "") or os.getenv("MULTIAI_API_KEY", "")).strip()


def secret_vendor_key(vendor: str) -> str:
    name = VENDOR_SECRET[vendor]
    return str(_secrets().get(name, "") or os.getenv(name, "")).strip()


def default_mode() -> str:
    """secrets에 멀티AI 키가 있으면 멀티AI, 회사별 키만 있으면 개별 키 방식."""
    if secret_multiai_key():
        return MODE_MULTIAI
    if secret_vendor_key("openai") or secret_vendor_key("anthropic"):
        return MODE_DIRECT
    return MODE_MULTIAI


def current_mode() -> str:
    return st.session_state.get("conn_mode") or default_mode()


def default_id(label: str) -> str:
    ids = _secrets().get("model_ids", {}) or {}
    return str(ids.get(label, "") or _BY_LABEL[label][2]).strip()


def model_id_for(label: str) -> str:
    """사이드바 직접 지정 > secrets [model_ids] > 기본 ID 순서."""
    override = _ss(f"cfg_id_{label}")
    if override:
        return override
    return default_id(label)


def connection(vendor: str) -> tuple[str, str]:
    """(base_url, api_key). 사이드바 입력이 우선, 비어 있으면 secrets."""
    if current_mode() == MODE_MULTIAI:
        return (_ss(SS_MULTIAI_URL) or secret_multiai_url(), _ss(SS_MULTIAI_KEY) or secret_multiai_key())
    return VENDOR_URL[vendor], (_ss(SS_VENDOR_KEY[vendor]) or secret_vendor_key(vendor))


def resolve(label: str) -> ModelCfg:
    label = label if label in _BY_LABEL else LABELS[0]
    _, vendor, _, pin, pout = _BY_LABEL[label]
    base_url, api_key = connection(vendor)
    return ModelCfg(
        label=label,
        id=model_id_for(label),
        base_url=base_url,
        api_key=api_key,
        input_price=pin,
        output_price=pout,
        vendor=vendor,
        demo=not (base_url and api_key),
    )


def load_models() -> list[ModelCfg]:
    return [resolve(l) for l in LABELS]


def is_demo_mode() -> bool:
    return all(m.is_demo for m in load_models())


def credit_limit() -> float:
    """사이드바 예산 막대(선택). 0이면 그리지 않는다."""
    try:
        return float(multiai_settings().get("credit_limit", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def cost_unit() -> str:
    return str(multiai_settings().get("cost_unit", "USD"))
