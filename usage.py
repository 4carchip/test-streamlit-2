"""API 사용량 기록.

모든 모델 호출은 llm.chat()을 거치고, 그때 여기에 토큰 수를 남긴다.
- 이번 세션 사용량: st.session_state (새로고침하면 초기화)
- 앱 누적 사용량:   .cache/usage.json (같은 서버에서 실행한 모든 세션 합계, 참고용)

학교 멀티AI 대시보드의 실제 크레딧과는 다를 수 있으므로 '추정치'로 표시한다.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import streamlit as st

from core.config import ModelCfg

_FILE = Path(".cache/usage.json")
_LOCK = threading.Lock()


def _empty() -> dict:
    return {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "estimated": False}


def _add(bucket: dict, label: str, tin: int, tout: int, cost: float, estimated: bool) -> None:
    row = bucket.setdefault(label, _empty())
    row["calls"] += 1
    row["input"] += int(tin)
    row["output"] += int(tout)
    row["cost"] += float(cost)
    row["estimated"] = row["estimated"] or estimated


def estimate_tokens(text: str) -> int:
    """응답에 usage가 없을 때 쓰는 대략값 (한국어 약 2글자 = 1토큰)."""
    return max(1, len(text) // 2)


def record(model: ModelCfg, tin: int, tout: int, estimated: bool = False) -> None:
    cost = tin / 1_000_000 * model.input_price + tout / 1_000_000 * model.output_price
    _add(st.session_state.setdefault("usage", {}), model.label, tin, tout, cost, estimated)

    # 누적 기록은 실패해도 앱 동작에는 영향이 없게 한다.
    try:
        with _LOCK:
            data = json.loads(_FILE.read_text("utf-8")) if _FILE.exists() else {}
            _add(data, model.label, tin, tout, cost, estimated)
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def session_usage() -> dict:
    return st.session_state.get("usage", {})


def total_usage() -> dict:
    try:
        return json.loads(_FILE.read_text("utf-8")) if _FILE.exists() else {}
    except Exception:
        return {}


def reset_session() -> None:
    st.session_state["usage"] = {}
