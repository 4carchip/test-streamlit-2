"""설정 읽기.

API 주소·키·모델 목록은 코드에 적지 않고 모두 Streamlit secrets에서 읽는다.
- 로컬:   .streamlit/secrets.toml  (레포에 올리지 않음)
- 배포:   Streamlit Community Cloud > App settings > Secrets

우선순위: [multiai] + [[models]] → OPENAI_API_KEY(예전 설정) → 데모 모드
데모 모드는 API 호출 없이 화면 흐름만 보여 준다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

DEMO_PREFIX = "demo-"


@dataclass(frozen=True)
class ModelCfg:
    label: str          # 화면에 보이는 이름 (예: "Claude Opus")
    id: str             # API에 보내는 모델 ID (플랫폼에서 확인한 값)
    base_url: str       # OpenAI 호환 API 주소
    api_key: str
    input_price: float = 0.0    # 입력 100만 토큰당 비용 (크레딧/원 등, 선택)
    output_price: float = 0.0   # 출력 100만 토큰당 비용 (선택)

    @property
    def is_demo(self) -> bool:
        return self.id.startswith(DEMO_PREFIX)


DEMO_MODELS = [
    ModelCfg("데모 모델 A (설명용)", DEMO_PREFIX + "a", "", ""),
    ModelCfg("데모 모델 B (검증용)", DEMO_PREFIX + "b", "", ""),
]


def _secrets() -> dict:
    """secrets.toml이 없어도 오류 없이 빈 dict를 돌려준다."""
    try:
        return st.secrets.to_dict()
    except Exception:
        return {}


def multiai_settings() -> dict:
    return _secrets().get("multiai", {}) or {}


def load_models() -> list[ModelCfg]:
    s = _secrets()
    common = s.get("multiai", {}) or {}
    base_url = str(common.get("base_url", "")).strip()
    api_key = str(common.get("api_key", "")).strip()

    models: list[ModelCfg] = []
    for m in s.get("models", []) or []:
        model_id = str(m.get("id", "")).strip()
        if not model_id:
            continue
        models.append(
            ModelCfg(
                label=str(m.get("label", model_id)),
                id=model_id,
                # 모델마다 주소·키가 다르면 모델 항목에 따로 적을 수 있다.
                base_url=str(m.get("base_url", base_url)).strip(),
                api_key=str(m.get("api_key", api_key)).strip(),
                input_price=float(m.get("input_price", 0) or 0),
                output_price=float(m.get("output_price", 0) or 0),
            )
        )

    usable = [m for m in models if m.api_key and m.base_url]
    if usable:
        return usable

    # 예전 설정(OPENAI_API_KEY / OPENAI_MODEL)만 있으면 OpenAI 공식 API로 동작한다.
    key = str(s.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")).strip()
    if key:
        model_id = str(s.get("OPENAI_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")).strip()
        return [ModelCfg(f"OpenAI ({model_id})", model_id, "https://api.openai.com/v1", key)]
    return DEMO_MODELS


def is_demo_mode() -> bool:
    return all(m.is_demo for m in load_models())


def credit_limit() -> float:
    """사이드바 사용량 막대에 쓸 예산(선택). 0이면 막대를 그리지 않는다."""
    try:
        return float(multiai_settings().get("credit_limit", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def cost_unit() -> str:
    return str(multiai_settings().get("cost_unit", "크레딧"))
