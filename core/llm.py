"""멀티AI(OpenAI 호환 API) 호출.

학교 멀티AI 플랫폼이 OpenAI 호환 형식(base_url + API 키 + chat/completions)을
제공한다는 가정으로 작성했다. 형식이 다르면 이 파일의 _call()만 고치면 된다.
"""

from __future__ import annotations

import json
import re
import time

import streamlit as st

from core import demo, usage
from core.config import ModelCfg


class LLMError(RuntimeError):
    """화면에 그대로 보여 줄 수 있는 오류."""


@st.cache_resource(show_spinner=False)
def _client(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, timeout=120, max_retries=1)


def _explain(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    hints = {
        401: "API 키가 틀렸거나 만료되었습니다. 'API 연결 안내' 페이지에서 키를 다시 확인하세요.",
        403: "이 키로 해당 모델을 쓸 권한이 없습니다. 플랫폼에서 모델 사용 권한을 확인하세요.",
        404: "주소(base_url) 또는 모델 ID가 맞지 않습니다. 플랫폼에 표시된 값과 비교하세요.",
        429: "호출 한도나 크레딧을 초과했습니다. 잠시 후 다시 시도하거나 사용량을 확인하세요.",
    }
    if status in hints:
        return f"[{status}] {hints[status]}"
    return f"{type(exc).__name__}: {exc}"


def _call(model: ModelCfg, messages: list[dict], max_tokens: int, temperature: float):
    """모델마다 받는 파라미터가 달라서 순서대로 시도한다."""
    from openai import BadRequestError

    client = _client(model.base_url, model.api_key)
    attempts = [
        {"max_tokens": max_tokens, "temperature": temperature},
        {"max_completion_tokens": max_tokens},   # 최신 추론형 모델
        {},
    ]
    last: Exception | None = None
    for extra in attempts:
        try:
            return client.chat.completions.create(model=model.id, messages=messages, **extra)
        except BadRequestError as exc:   # 지원하지 않는 파라미터 → 다음 조합
            last = exc
    raise last  # type: ignore[misc]


def chat(
    model: ModelCfg,
    messages: list[dict],
    *,
    purpose: str = "answer",
    max_tokens: int = 1500,
    temperature: float = 0.2,
    demo_hint: dict | None = None,
) -> str:
    """모델을 호출하고 사용량을 기록한 뒤 텍스트를 돌려준다."""
    if model.is_demo:
        text = demo.respond(purpose, messages, demo_hint or {})
        prompt_text = "".join(m["content"] for m in messages)
        usage.record(model, usage.estimate_tokens(prompt_text), usage.estimate_tokens(text), estimated=True)
        time.sleep(0.3)
        return text

    try:
        resp = _call(model, messages, max_tokens, temperature)
    except Exception as exc:  # 네트워크·인증·한도 오류를 한 곳에서 안내
        raise LLMError(_explain(exc)) from exc

    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    u = getattr(resp, "usage", None)
    if u and getattr(u, "prompt_tokens", None) is not None:
        usage.record(model, u.prompt_tokens, u.completion_tokens or 0)
    else:
        prompt_text = "".join(m["content"] for m in messages)
        usage.record(model, usage.estimate_tokens(prompt_text), usage.estimate_tokens(text), estimated=True)
    return text


def parse_json(text: str) -> dict:
    """모델이 ```json 블록이나 앞뒤 설명을 붙여도 JSON 부분만 꺼낸다."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON을 찾지 못했습니다.")
    return json.loads(candidate[start : end + 1])


def ping(model: ModelCfg) -> tuple[bool, str, float]:
    """연결 테스트: (성공 여부, 메시지, 걸린 시간 초)."""
    t0 = time.time()
    try:
        text = chat(
            model,
            [{"role": "user", "content": "연결 테스트입니다. 'OK'라고만 답하세요."}],
            purpose="ping",
            max_tokens=20,
        )
        return True, text or "(빈 응답)", time.time() - t0
    except LLMError as exc:
        return False, str(exc), time.time() - t0
