"""
5차시 실습 03 — 정산 비서 AI Streamlit 웹서비스

Colab 노트북에서 %%writefile로 저장한 뒤 실행하는 단일 파일 앱입니다.

핵심 구조
- LangGraph Workflow: 입력 파싱 → 피드백 파싱 → 안전성 검사 → 전략 분기 → 계산 → 리포트 생성
- LLM 역할: 자연어 예외 조건을 구조화 JSON과 rate로 변환, 설명문 생성
- 계산 역할: 순수 Python 계산 엔진이 금액 계산과 총액 검증 수행

실행:
  streamlit run apps/settlement_assistant_app.py
"""

import copy
import json
import operator
import os
import re
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

import streamlit as st
from langgraph.graph import END, START, StateGraph
from openai import OpenAI



RATE_TABLE = """
| 상황 | rate 종류 | 권장 기준 |
|---|---:|---:|
| 술 미섭취 / 전혀 안 먹음 | discount_rate | 1.0 |
| 거의 안 먹음 / 한 입만 | discount_rate | 0.7 |
| 소량 섭취 / 절반 정도 | discount_rate | 0.5 |
| 중도 귀가, 절반 이상 자리를 비움 | discount_rate | 0.5 |
| 잠깐 있다 감 | discount_rate | 0.3 |
| 지각 비율 명시 | surcharge_rate | 명시 비율, 예: 20% → 0.2 |
| 지각 금액 명시 | surcharge_amount | 명시 금액, 예: 5000 |
"""

PARSE_SYSTEM_PROMPT = f"""
너는 모임 정산 상황을 구조화하는 정산 파서다.
사용자 자연어를 읽고 반드시 JSON 객체만 반환한다.

중요 원칙:
1. LLM은 산술 계산을 하지 않는다. 금액 계산은 별도 Python 계산 엔진이 한다.
2. 너는 total_amount, items, participants, exceptions, rate만 구조화한다.
3. 예외 조건은 아래 rate 기준표를 따른다.
4. 술 미섭취는 target_items를 ["주류"]로 둔다. 주류 항목이 없으면 ["공통"]으로 둔다.
5. 중도 귀가/잠깐 있다 감은 모든 항목을 target_items로 둔다.
6. 지각은 사용자가 명시한 비율/금액이 있으면 그대로 쓰고, 명시가 없으면 surcharge_rate 0.2를 기본값으로 둔다.
7. 항목 금액 합계가 total_amount보다 작으면 기타/공통 항목을 추가해 합계를 맞춘다.
8. 확실하지 않은 참여자나 금액은 임의로 많이 만들지 말고 가능한 범위에서 구조화한다.

rate 기준표:
{RATE_TABLE}

반환 JSON 스키마:
{{
  "total_amount": 80000,
  "items": [{{"name": "주류", "amount": 30000}}, {{"name": "안주", "amount": 50000}}],
  "participants": [
    {{"name": "A", "exceptions": []}},
    {{"name": "D", "exceptions": [{{"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0, "reason": "술을 마시지 않음"}}]}}
  ]
}}
""".strip()

FEEDBACK_SYSTEM_PROMPT = f"""
너는 기존 정산 JSON에 사용자 피드백을 반영하기 위한 추가 예외 조건 파서다.
반드시 JSON 객체만 반환한다.

반환 형식:
{{
  "name": "참여자명",
  "additional_exception": {{
    "type": "예외 유형",
    "target_items": ["항목명"],
    "discount_rate": 0.5,
    "reason": "피드백 근거"
  }}
}}

지각비처럼 금액이 명시된 경우에는 surcharge_amount를 사용한다.
지각 비율이 명시된 경우에는 surcharge_rate를 사용한다.
할인/감액 조건은 discount_rate를 사용한다.

rate 기준표:
{RATE_TABLE}
""".strip()


REPORT_SYSTEM_PROMPT = """
너는 정산 결과를 사용자가 이해하기 쉽게 설명하는 정산 비서다.
단, 금액을 새로 계산하거나 수정하지 말고 입력으로 주어진 calculation_result의 숫자만 사용한다.
출력은 다음 구조를 따른다.

1. 한 줄 요약
2. 참여자별 부담액
3. 예외 조건 반영 근거
4. 카카오톡 공유용 메시지

친절하되 과도하게 장황하지 않게 작성한다.
""".strip()


# ⭐ [설명 함수 SA-FUNC-01] Secret 처리 — Streamlit Cloud의 st.secrets와 환경변수를 함께 지원합니다.
def get_setting_from_env_or_secrets(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def get_api_key_from_env_or_secrets() -> str:
    return get_setting_from_env_or_secrets("OPENAI_API_KEY", "")


def get_default_model() -> str:
    return get_setting_from_env_or_secrets("OPENAI_MODEL", "gpt-4.1-mini")


def make_client(api_key: str) -> Optional[OpenAI]:
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


# ⭐ [설명 함수 SA-FUNC-02A] JSON 복구 — 코드펜스나 설명이 섞여도 JSON 객체를 복원합니다.
def extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("빈 응답입니다.")
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("JSON 객체를 찾지 못했습니다.")


# ⭐ [설명 함수 SA-FUNC-02B] 구조화 출력 호출 — Responses API를 우선 사용하고 실패 시 Chat Completions로 대체합니다.
def call_llm_json(client: Optional[OpenAI], model: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    if client is None:
        raise RuntimeError("OPENAI_API_KEY가 없어 LLM JSON 호출을 건너뜁니다.")
    response_error: Optional[Exception] = None
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            text={"format": {"type": "json_object"}},
        )
        return extract_json_object(response.output_text)
    except Exception as exc:
        response_error = exc
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return extract_json_object(response.choices[0].message.content or "")
    except Exception as exc:
        raise RuntimeError(f"LLM JSON 호출 실패: {response_error} / {exc}") from exc


def call_llm_text(client: Optional[OpenAI], model: str, system_prompt: str, user_prompt: str) -> str:
    if client is None:
        raise RuntimeError("OPENAI_API_KEY가 없어 LLM 텍스트 호출을 건너뜁니다.")
    response_error: Optional[Exception] = None
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return response.output_text
    except Exception as exc:
        response_error = exc
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        raise RuntimeError(f"LLM 텍스트 호출 실패: {response_error} / {exc}") from exc

def parse_amount_number(num: str, unit: str) -> int:
    value = float(num.replace(",", ""))
    if unit in ["만원", "만"]:
        return int(value * 10000)
    if unit in ["천원", "천"]:
        return int(value * 1000)
    return int(value)


def format_won(amount: float | int) -> str:
    return f"{int(round(amount)):,}원"


def ordered_unique(values: List[str]) -> List[str]:
    out = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


# ⭐ [설명 함수 SA-FUNC-03A] 총액 파싱 — 정규식으로 총액 후보를 찾습니다.
def extract_total_amount_rule(text: str) -> Optional[int]:
    patterns = [
        r"총\s*(\d+(?:\.\d+)?)\s*(만원|만|천원|천|원)",
        r"합계\s*(\d+(?:\.\d+)?)\s*(만원|만|천원|천|원)",
        r"전체\s*(\d+(?:\.\d+)?)\s*(만원|만|천원|천|원)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return parse_amount_number(m.group(1), m.group(2))
    m = re.search(r"(\d{1,3}(?:,\d{3})+)\s*원", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


# ⭐ [설명 함수 SA-FUNC-03B] 항목 파싱 — 항목명/금액을 추출하고 합계 차이를 공통비로 보정합니다.
def extract_items_rule(text: str, total_amount: Optional[int]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    stop_names = {"총", "합계", "전체", "금액", "총액"}
    for m in re.finditer(r"([가-힣A-Za-z0-9]+)\s*(\d+(?:\.\d+)?)\s*(만원|만|천원|천|원)", text):
        name = m.group(1).strip()
        amount = parse_amount_number(m.group(2), m.group(3))
        if name in stop_names:
            continue
        if len(name) <= 1 and re.match(r"[A-Z]", name):
            continue
        items.append({"name": name, "amount": amount})
    # 중복 항목 제거
    clean: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        key = (item["name"], item["amount"])
        if key not in seen:
            clean.append(item)
            seen.add(key)
    items = clean
    if total_amount and items:
        item_sum = sum(i["amount"] for i in items)
        diff = total_amount - item_sum
        if diff > 0:
            items.append({"name": "기타/공통", "amount": diff})
    return items


# ⭐ [설명 함수 SA-FUNC-03C] 참여자 파싱 — 영문 이니셜과 한국어 이름 후보를 처리합니다.
def extract_participants_rule(text: str) -> List[str]:
    letters = re.findall(r"\b[A-Z]\b", text)
    if len(set(letters)) >= 2:
        return ordered_unique(letters)

    m = re.search(r"(?:참여자|멤버|사람|인원)(?:는|:)?\s*([가-힣A-Za-z,\s]+)", text)
    if m:
        names = re.split(r"[,/\s]+|와|과|랑|하고", m.group(1))
        names = [n for n in names if 1 <= len(n) <= 5 and n not in ["있어", "있고", "총"]]
        if len(names) >= 2:
            return ordered_unique(names)

    # 한국어 이름 후보: "철수, 영희, 민수" 같은 패턴에 대응
    candidates = re.findall(r"[가-힣]{2,4}", text)
    blacklist = {"총액", "주류", "안주", "공통", "만원", "참여자", "정산", "지각", "중도", "귀가"}
    names = [c for c in candidates if c not in blacklist]
    return ordered_unique(names[:6])


def item_names_from_items(items: List[Dict[str, Any]]) -> List[str]:
    return [item["name"] for item in items] or ["공통"]


def participant_focused_context(text: str, name: str, participants: List[str], width: int = 80) -> str:
    """해당 참여자 이름 이후의 짧은 구간만 잘라 다른 참여자의 예외 조건이 섞이지 않게 한다."""
    windows = []
    others = [p for p in participants if p != name]
    for m in re.finditer(re.escape(name), text):
        start = m.start()
        end = min(len(text), m.end() + width)
        # 다음 참여자 이름이 나오면 그 앞에서 자른다.
        for other in others:
            pos = text.find(other, m.end())
            if pos != -1:
                end = min(end, pos)
        # 문장 경계가 더 가까우면 그 앞에서 자른다.
        boundary = re.search(r"[.!?\n]", text[m.end():end])
        if boundary:
            end = min(end, m.end() + boundary.start())
        windows.append(text[start:end])
    return " ".join(windows)

def explicit_percent(window: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", window)
    if m:
        return round(float(m.group(1)) / 100, 4)
    return None


def explicit_surcharge_amount(window: str) -> Optional[int]:
    m = re.search(r"(?:지각비|벌금|추가)\s*(\d+(?:\.\d+)?)\s*(만원|만|천원|천|원)", window)
    if m:
        return parse_amount_number(m.group(1), m.group(2))
    return None


# ⭐ [설명 함수 SA-FUNC-03D] 예외 규칙 추론 — 술 미섭취·지각·중도 귀가를 rate로 변환합니다.
def infer_exceptions_rule(text: str, participants: List[str], items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    item_names = item_names_from_items(items)
    exceptions: Dict[str, List[Dict[str, Any]]] = {name: [] for name in participants}
    for name in participants:
        win = participant_focused_context(text, name, participants)
        if not win:
            continue
        if "술" in win and any(k in win for k in ["안", "못", "미섭취", "안마", "안마", "안 마"]):
            target = [i for i in item_names if "주류" in i or "술" in i or i == "주류"] or ["주류"]
            exceptions[name].append({
                "type": "술 미섭취",
                "target_items": target,
                "discount_rate": 1.0,
                "reason": "술을 마시지 않음",
            })
        if any(k in win for k in ["거의 안", "한 입", "한입"]):
            exceptions[name].append({
                "type": "거의 안 먹음",
                "target_items": item_names,
                "discount_rate": 0.7,
                "reason": "거의 먹지 않음",
            })
        elif any(k in win for k in ["조금", "소량", "절반"]):
            exceptions[name].append({
                "type": "소량 섭취",
                "target_items": item_names,
                "discount_rate": 0.5,
                "reason": "일부만 섭취",
            })
        if any(k in win for k in ["중도", "먼저", "일찍", "귀가"]):
            exceptions[name].append({
                "type": "중도 귀가",
                "target_items": item_names,
                "discount_rate": 0.5,
                "reason": "중도 귀가",
            })
        if any(k in win for k in ["잠깐", "잠시"]):
            exceptions[name].append({
                "type": "잠깐 참석",
                "target_items": item_names,
                "discount_rate": 0.3,
                "reason": "잠깐 참석",
            })
        if any(k in win for k in ["늦", "지각"]):
            amount = explicit_surcharge_amount(win)
            pct = explicit_percent(win)
            exc = {"type": "지각", "target_items": item_names, "reason": "늦게 도착"}
            if amount is not None:
                exc["surcharge_amount"] = amount
            else:
                exc["surcharge_rate"] = pct if pct is not None else 0.2
            exceptions[name].append(exc)
    return exceptions


# ⭐ [설명 함수 SA-FUNC-03] 규칙 기반 fallback — API Key가 없거나 LLM 호출이 실패해도 시연이 가능합니다.
def rule_based_parse(raw_input: str) -> Dict[str, Any]:
    total = extract_total_amount_rule(raw_input)
    items = extract_items_rule(raw_input, total)
    participants = extract_participants_rule(raw_input)
    if not participants:
        participants = ["A", "B", "C", "D"]
    if total is None:
        total = sum(i["amount"] for i in items) if items else 0
    exceptions = infer_exceptions_rule(raw_input, participants, items)
    return {
        "total_amount": total,
        "items": items,
        "participants": [
            {"name": name, "exceptions": exceptions.get(name, [])}
            for name in participants
        ],
        "parser": "rule_based_fallback",
    }


# ⭐ [설명 함수 SA-FUNC-03E] 피드백 파싱 — 기존 정산 JSON에 추가할 예외 조건 하나를 찾습니다.
def rule_based_feedback(raw_input: str, parsed_json: Dict[str, Any]) -> Dict[str, Any]:
    participants = [p["name"] for p in parsed_json.get("participants", [])]
    items = parsed_json.get("items", [])
    exceptions = infer_exceptions_rule(raw_input, participants, items)
    for name, excs in exceptions.items():
        if excs:
            return {"name": name, "additional_exception": excs[0]}
    # 이름만 있고 조건이 애매하면 코멘트를 남긴다.
    for name in participants:
        if name in raw_input:
            return {"name": name, "additional_exception": {"type": "수동 확인 필요", "target_items": item_names_from_items(items), "discount_rate": 0.0, "reason": raw_input}}
    return {"name": participants[0] if participants else "A", "additional_exception": {"type": "수동 확인 필요", "target_items": item_names_from_items(items), "discount_rate": 0.0, "reason": raw_input}}


# ⭐ [설명 함수 SA-FUNC-04A] 정규화 — 누락 필드와 항목 합계를 계산 엔진 계약에 맞춥니다.
def normalize_parsed_json(parsed_json: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(parsed_json)
    data.setdefault("items", [])
    data.setdefault("participants", [])
    total = int(data.get("total_amount") or 0)
    items = data["items"]
    if not items and total > 0:
        items.append({"name": "공통", "amount": total})
    if items:
        item_sum = sum(int(i.get("amount", 0)) for i in items)
        if total and item_sum < total:
            items.append({"name": "기타/공통", "amount": total - item_sum})
        elif not total:
            data["total_amount"] = item_sum
    item_names = item_names_from_items(items)
    for p in data["participants"]:
        p.setdefault("exceptions", [])
        for exc in p["exceptions"]:
            exc.setdefault("target_items", item_names)
            if not exc.get("target_items"):
                exc["target_items"] = item_names
    return data


# ⭐ [설명 함수 SA-FUNC-04B] 검증 — 총액·참여자·rate 범위를 계산 전에 검사합니다.
def validate_parsed_json(parsed_json: Dict[str, Any]) -> List[str]:
    errors = []
    total = parsed_json.get("total_amount")
    participants = parsed_json.get("participants") or []
    items = parsed_json.get("items") or []
    if not total or total <= 0:
        errors.append("총액(total_amount)이 필요합니다.")
    if not participants:
        errors.append("참여자(participants)가 필요합니다.")
    names = [p.get("name") for p in participants]
    if len(names) != len(set(names)):
        errors.append("중복 참여자 이름이 있습니다.")
    if items and total:
        item_sum = sum(int(item.get("amount", 0)) for item in items)
        if item_sum != int(total):
            errors.append(f"항목 합계({item_sum})와 총액({total})이 일치하지 않습니다.")
    item_names = set(item_names_from_items(items))
    for p in participants:
        for exc in p.get("exceptions", []):
            for key in ["discount_rate", "surcharge_rate"]:
                if key in exc:
                    rate = exc[key]
                    if rate is None or not (0 <= float(rate) <= 1):
                        errors.append(f"{p.get('name')}의 {key} 값이 0~1 범위를 벗어났습니다.")
            if "surcharge_amount" in exc and exc["surcharge_amount"] is not None and int(exc["surcharge_amount"]) < 0:
                errors.append(f"{p.get('name')}의 surcharge_amount는 0 이상이어야 합니다.")
            for t in exc.get("target_items", []):
                if t not in item_names and t not in ["공통", "주류"]:
                    # 주류는 LLM이 항목 누락 시 넣을 수 있어 경고만 하지 않는다.
                    pass
    return errors


def _calc_step1(items: List[Dict[str, Any]], participants: List[Dict[str, Any]]) -> tuple[Dict[str, float], Dict[str, List[str]]]:
    amounts = {p["name"]: 0.0 for p in participants}
    discount_logs: Dict[str, List[str]] = {}

    for item in items:
        item_name = item["name"]
        item_amount = float(item["amount"])
        excluded = set()
        partial_discounts: Dict[str, float] = {}

        for p in participants:
            for exc in p.get("exceptions", []):
                targets = exc.get("target_items", [])
                if item_name in targets or "공통" in targets or (item_name == "주류" and "주류" in targets):
                    if "discount_rate" in exc:
                        rate = float(exc["discount_rate"])
                        if rate >= 1.0:
                            excluded.add(p["name"])
                        elif rate > 0:
                            partial_discounts[p["name"]] = max(partial_discounts.get(p["name"], 0.0), rate)

        eligible = [p for p in participants if p["name"] not in excluded]
        if not eligible:
            continue
        per_person = item_amount / len(eligible)

        for name in excluded:
            discount_logs.setdefault(name, []).append(f"{item_name}: 전액 제외")

        total_discount_amount = 0.0
        for p in eligible:
            name = p["name"]
            rate = partial_discounts.get(name, 0.0)
            final = per_person * (1 - rate)
            amounts[name] += final
            if rate > 0:
                discount_amt = per_person * rate
                total_discount_amount += discount_amt
                discount_logs.setdefault(name, []).append(
                    f"{item_name}: 1인 몫 {format_won(per_person)} × (1-{rate}) = {format_won(final)}"
                )

        non_discounted = [p for p in eligible if p["name"] not in partial_discounts]
        if total_discount_amount > 0 and non_discounted:
            redistribute = total_discount_amount / len(non_discounted)
            for p in non_discounted:
                amounts[p["name"]] += redistribute

    return amounts, discount_logs


def _apply_surcharge_floor_rounding(amounts: Dict[str, float], participants: List[Dict[str, Any]], total_amount: int, discount_logs: Dict[str, List[str]]) -> Dict[str, Any]:
    n = len(participants)
    base = total_amount / n if n else 0
    step1_amounts = dict(amounts)
    surcharge_logs: Dict[str, List[str]] = {}
    surcharge_deductions: Dict[str, Dict[str, Any]] = {}

    surcharged_names = {
        p["name"]
        for p in participants
        if any("surcharge_rate" in e or "surcharge_amount" in e for e in p.get("exceptions", []))
    }

    for p in participants:
        name = p["name"]
        for exc in p.get("exceptions", []):
            surcharge = 0.0
            before = amounts[name]
            if "surcharge_rate" in exc:
                surcharge = before * float(exc["surcharge_rate"])
                surcharge_logs.setdefault(name, []).append(f"할증: {format_won(before)} × {exc['surcharge_rate']} = {format_won(surcharge)}")
            elif "surcharge_amount" in exc:
                surcharge = float(exc["surcharge_amount"])
                surcharge_logs.setdefault(name, []).append(f"고정 할증: {format_won(surcharge)}")

            if surcharge:
                amounts[name] += surcharge
                targets = [q for q in participants if q["name"] != name and q["name"] not in surcharged_names]
                if not targets:
                    targets = [q for q in participants if q["name"] != name]
                if targets:
                    deduction = surcharge / len(targets)
                    for t in targets:
                        amounts[t["name"]] -= deduction
                    surcharge_deductions[name] = {"targets": [t["name"] for t in targets], "per_person": round(deduction)}

    # 최소 부담 하한선: 균등 분담액의 30%, 단 0원 완전 제외자는 면제
    floor = base * 0.3
    floor_applied: List[str] = []
    total_floor_extra = 0.0
    for name in list(amounts.keys()):
        if amounts[name] == 0:
            continue
        if amounts[name] < floor:
            total_floor_extra += floor - amounts[name]
            amounts[name] = floor
            floor_applied.append(name)

    if total_floor_extra > 0:
        candidates = [name for name in amounts.keys() if name not in floor_applied and amounts[name] > 0]
        total_candidates = sum(amounts[name] for name in candidates)
        for name in candidates:
            if total_candidates:
                amounts[name] -= total_floor_extra * amounts[name] / total_candidates

    int_amounts = {name: int(round(value)) for name, value in amounts.items()}
    diff = int(total_amount) - sum(int_amounts.values())
    rounding_adjusted = None
    if diff and int_amounts:
        # 부담액이 가장 큰 사람에게 반올림 오차 보정
        target = max(int_amounts, key=lambda name: int_amounts[name])
        int_amounts[target] += diff
        rounding_adjusted = target

    participants_out = []
    for p in participants:
        name = p["name"]
        participants_out.append({
            "name": name,
            "final_amount": int_amounts.get(name, 0),
            "breakdown": {
                "base_equal_share": int(round(base)),
                "after_discount": int(round(step1_amounts.get(name, 0))),
            },
        })

    return {
        "participants": participants_out,
        "total_amount": int(total_amount),
        "sum_participants": sum(p["final_amount"] for p in participants_out),
        "total_verified": sum(p["final_amount"] for p in participants_out) == int(total_amount),
        "floor_applied": floor_applied,
        "rounding_adjusted": rounding_adjusted,
        "discount_logs": discount_logs,
        "surcharge_logs": surcharge_logs,
        "surcharge_deductions": surcharge_deductions,
    }


# ⭐ [설명 함수 SA-FUNC-05] 계산 엔진 — LLM이 아니라 Python이 금액과 총액 일치를 책임집니다.
def calculate(parsed_json: Dict[str, Any]) -> Dict[str, Any]:
    data = normalize_parsed_json(parsed_json)
    errors = validate_parsed_json(data)
    if errors:
        raise ValueError(" / ".join(errors))
    total_amount = int(data["total_amount"])
    participants = data["participants"]
    items = data.get("items") or [{"name": "공통", "amount": total_amount}]
    amounts, discount_logs = _calc_step1(items, participants)
    return _apply_surcharge_floor_rounding(amounts, participants, total_amount, discount_logs)


def build_deterministic_report(parsed_json: Dict[str, Any], calculation_result: Dict[str, Any]) -> str:
    lines = ["## 💸 정산 결과", ""]
    lines.append(f"총액: **{format_won(calculation_result['total_amount'])}**")
    lines.append("")
    lines.append("### 참여자별 부담액")
    for p in calculation_result["participants"]:
        lines.append(f"- {p['name']}: **{format_won(p['final_amount'])}**")
    lines.append("")
    lines.append("### 검증")
    lines.append(f"- 참여자 합계: {format_won(calculation_result['sum_participants'])}")
    lines.append(f"- 총액 일치 여부: {'✅ 일치' if calculation_result['total_verified'] else '⚠️ 불일치'}")
    if calculation_result.get("discount_logs"):
        lines.append("")
        lines.append("### 감액 근거")
        for name, logs in calculation_result["discount_logs"].items():
            lines.append(f"- {name}: " + " / ".join(logs))
    if calculation_result.get("surcharge_logs"):
        lines.append("")
        lines.append("### 할증 근거")
        for name, logs in calculation_result["surcharge_logs"].items():
            lines.append(f"- {name}: " + " / ".join(logs))
    lines.append("")
    lines.append("### 카카오톡 공유용")
    share = ["[정산 결과]"]
    for p in calculation_result["participants"]:
        share.append(f"{p['name']}: {format_won(p['final_amount'])}")
    share.append(f"합계: {format_won(calculation_result['sum_participants'])}")
    lines.append("```\n" + "\n".join(share) + "\n```")
    return "\n".join(lines)


# ⭐ [설명 함수 SA-FUNC-06] State 스키마 — Node 사이에서 이동하는 공용 데이터 계약입니다.
class SettlementState(TypedDict, total=False):
    raw_input: str
    mode: Literal["initial", "feedback"]
    parsed_json: Dict[str, Any]
    strategy: Literal["SIMPLE", "EXCEPTION"]
    calculation_result: Dict[str, Any]
    calc_explanation: str
    final_report: str
    safety_error: str
    feedback_history: List[str]
    trace: Annotated[List[str], operator.add]


# ⭐ [설명 함수 SA-FUNC-07A] DetectModeNode — 기존 parsed_json 존재 여부로 최초/피드백 흐름을 나눕니다.
def detect_mode_node(state: SettlementState) -> Dict[str, Any]:
    mode: Literal["initial", "feedback"] = "feedback" if state.get("parsed_json") else "initial"
    return {"mode": mode, "trace": [f"DetectModeNode: {mode}"]}


# ⭐ [설명 함수 SA-FUNC-07B] InputParsingNode Factory — 외부 의존성(client/model)을 closure로 주입합니다.
def input_parsing_node_factory(client: Optional[OpenAI], model: str):
    def input_parsing_node(state: SettlementState) -> Dict[str, Any]:
        raw = state["raw_input"]
        try:
            parsed = call_llm_json(client, model, PARSE_SYSTEM_PROMPT, raw)
            parsed["parser"] = "openai"
        except Exception as exc:
            parsed = rule_based_parse(raw)
            parsed["parser_error"] = str(exc)
        parsed = normalize_parsed_json(parsed)
        return {"parsed_json": parsed, "trace": [f"InputParsingNode: parser={parsed.get('parser')}"]}
    return input_parsing_node


# ⭐ [설명 함수 SA-FUNC-07C] FeedbackParsingNode — 기존 JSON을 보존하며 예외 조건만 추가합니다.
def feedback_parsing_node_factory(client: Optional[OpenAI], model: str):
    def feedback_parsing_node(state: SettlementState) -> Dict[str, Any]:
        raw = state["raw_input"]
        parsed_json = copy.deepcopy(state["parsed_json"])
        prompt = f"""
기존 정산 JSON과 사용자 피드백을 보고 추가 예외 조건 하나를 JSON으로 반환해라.
반환 형식:
{{"name": "참여자명", "additional_exception": {{"type": "...", "target_items": ["..."], "discount_rate": 0.5, "reason": "..."}}}}
또는 지각비 금액이면 surcharge_amount를 사용한다.

기존 정산 JSON:
{json.dumps(parsed_json, ensure_ascii=False)}

사용자 피드백:
{raw}
""".strip()
        try:
            feedback = call_llm_json(client, model, FEEDBACK_SYSTEM_PROMPT, prompt)
        except Exception:
            feedback = rule_based_feedback(raw, parsed_json)
        name = feedback.get("name")
        additional = feedback.get("additional_exception")
        if name and additional:
            for p in parsed_json.get("participants", []):
                if p.get("name") == name:
                    p.setdefault("exceptions", []).append(additional)
                    break
        parsed_json = normalize_parsed_json(parsed_json)
        history = state.get("feedback_history", []) + [raw]
        return {"parsed_json": parsed_json, "feedback_history": history, "trace": [f"FeedbackParsingNode: target={name}"]}
    return feedback_parsing_node


# ⭐ [설명 함수 SA-FUNC-07D] SafetyCheckNode — 계산 전 입력 계약을 확인합니다.
def safety_check_node(state: SettlementState) -> Dict[str, Any]:
    data = normalize_parsed_json(state["parsed_json"])
    errors = validate_parsed_json(data)
    if errors:
        return {"parsed_json": data, "safety_error": " / ".join(errors), "trace": ["SafetyCheckNode: error"]}
    return {"parsed_json": data, "safety_error": "", "trace": ["SafetyCheckNode: ok"]}


# ⭐ [설명 함수 SA-FUNC-07E] RouteRequestNode — 예외 존재 여부를 전략 값으로 기록합니다.
def route_request_node(state: SettlementState) -> Dict[str, Any]:
    has_exception = any(p.get("exceptions") for p in state["parsed_json"].get("participants", []))
    strategy: Literal["SIMPLE", "EXCEPTION"] = "EXCEPTION" if has_exception else "SIMPLE"
    return {"strategy": strategy, "trace": [f"RouteRequestNode: {strategy}"]}


# ⭐ [설명 함수 SA-FUNC-07F] CalculationNode — 순수 Python 계산 엔진을 호출합니다.
def calculation_node(state: SettlementState) -> Dict[str, Any]:
    if state.get("safety_error"):
        return {"trace": ["CalculationNode: skipped"]}
    try:
        result = calculate(state["parsed_json"])
        return {"calculation_result": result, "trace": ["CalculationNode: calculated"]}
    except Exception as exc:
        return {"safety_error": str(exc), "trace": ["CalculationNode: error"]}


# ⭐ [설명 함수 SA-FUNC-07G] ReportGenerationNode — 계산 결과 숫자는 바꾸지 않고 설명만 생성합니다.
def report_generation_node_factory(client: Optional[OpenAI], model: str):
    def report_generation_node(state: SettlementState) -> Dict[str, Any]:
        if state.get("safety_error"):
            report = f"⚠️ 정산을 진행할 수 없습니다.\n\n오류: {state['safety_error']}"
            return {"final_report": report, "calc_explanation": report, "trace": ["ReportGenerationNode: safety_error"]}
        parsed_json = state["parsed_json"]
        calculation_result = state["calculation_result"]
        fallback = build_deterministic_report(parsed_json, calculation_result)
        try:
            prompt = json.dumps({"parsed_json": parsed_json, "calculation_result": calculation_result}, ensure_ascii=False, indent=2)
            report = call_llm_text(client, model, REPORT_SYSTEM_PROMPT, prompt)
            if not report.strip():
                report = fallback
        except Exception:
            report = fallback
        return {"final_report": report, "calc_explanation": fallback, "trace": ["ReportGenerationNode: generated"]}
    return report_generation_node


# ⭐ [설명 함수 SA-FUNC-08A] 조건 분기 Router — Edge가 읽을 짧은 분기 키만 반환합니다.
def mode_router(state: SettlementState) -> Literal["initial", "feedback"]:
    return state["mode"]


# ⭐ [설명 함수 SA-FUNC-08B] 안전성 Router — 오류가 있으면 계산을 건너뛰고 리포트로 이동합니다.
def safety_router(state: SettlementState) -> Literal["error", "ok"]:
    return "error" if state.get("safety_error") else "ok"


# 학생 실습 범위: 이 build_graph() 함수만 완성합니다.
def build_graph(client: Optional[OpenAI], model: str):
    builder = StateGraph(SettlementState)

    # [SA-GRAPH-TODO-01] TODO: 아래 7개 Node를 builder에 등록하세요.
    # 등록할 이름:
    # detect_mode / input_parsing / feedback_parsing / safety_check
    # route_request / calculation / report_generation
    builder.add_node("detect_mode", detect_mode_node)
    builder.add_node("input_parsing", input_parsing_node_factory(client, model))
    builder.add_node("feedback_parsing", feedback_parsing_node_factory(client, model))
    builder.add_node("safety_check", safety_check_node)
    builder.add_node("route_request", route_request_node)
    builder.add_node("calculation", calculation_node)
    builder.add_node("report_generation", report_generation_node_factory(client, model))

    # [SA-GRAPH-TODO-02] TODO: START에서 detect_mode로 연결하세요.
    builder.add_edge(START, "detect_mode")

    # [SA-GRAPH-TODO-03] TODO: mode_router를 사용해
    # initial → input_parsing, feedback → feedback_parsing으로 분기하세요.
    builder.add_conditional_edges(
        "detect_mode",
        mode_router,
        {
            "initial": "input_parsing",
            "feedback": "feedback_parsing",
        },
    )

    # [SA-GRAPH-TODO-04] TODO: input_parsing과 feedback_parsing을
    # 모두 safety_check로 연결하세요.
    for node in ["input_parsing", "feedback_parsing"]:
        builder.add_edge(node, "safety_check")

    # [SA-GRAPH-TODO-05] TODO: safety_router를 사용해
    # error → report_generation, ok → route_request로 분기하세요.
    builder.add_conditional_edges(
        "safety_check",
        safety_router,
        {
            "error": "report_generation",
            "ok": "route_request",
        },
    )

    # [SA-GRAPH-TODO-06] TODO: 아래 정상 경로와 종료를 연결하고 compile 결과를 반환하세요.
    # route_request → calculation → report_generation → END
    builder.add_edge("route_request", "calculation")
    builder.add_edge("calculation", "report_generation")
    builder.add_edge("report_generation", END)

    return builder.compile()




EXAMPLES = [
    "총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어.",
    "총 12만원, A B C D E. 주류 5만원, 안주 5만원, 공통비 2만원. B는 술 미섭취, C는 안주를 거의 안 먹었고, E는 중도 귀가했어.",
    "A, B, C 셋이서 총 45000원 먹었어. 예외 없이 똑같이 나눠줘.",
    "총 10만원이고 민수, 지현, 서연, 도윤이 있어. 주류 4만원 안주 6만원. 지현은 술 안 마셨고 도윤은 지각비 5000원 더 내기로 했어.",
]


def reset_session():
    for key in ["messages", "parsed_json", "last_result", "last_trace", "feedback_history"]:
        st.session_state.pop(key, None)


def ensure_state():
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "안녕하세요! 모임 정산 상황을 자연어로 입력하면 예외 조건을 반영해 정산표를 만들어드릴게요."}
        ]
    st.session_state.setdefault("feedback_history", [])


def render_result(result: Dict[str, Any]):
    if result.get("safety_error"):
        st.error(result["safety_error"])
    calc = result.get("calculation_result")
    if calc:
        st.subheader("참여자별 부담액")
        cols = st.columns(min(4, max(1, len(calc.get("participants", [])))))
        for idx, p in enumerate(calc.get("participants", [])):
            with cols[idx % len(cols)]:
                st.metric(p["name"], format_won(p["final_amount"]))
        st.caption(f"합계 검증: {format_won(calc.get('sum_participants', 0))} / 총액 {format_won(calc.get('total_amount', 0))} — {'일치' if calc.get('total_verified') else '불일치'}")

    with st.expander("구조화 JSON 보기"):
        st.json(result.get("parsed_json", {}))
    with st.expander("계산 엔진 상세 로그"):
        st.json(result.get("calculation_result", {}))
    with st.expander("LangGraph 실행 Trace"):
        st.write(" → ".join(result.get("trace", [])))


# ⭐ [설명 함수 SA-FUNC-09] UI-Graph 연결 — session_state를 State로 바꾸고 결과를 다시 저장합니다.
def run_settlement(raw_input: str, graph) -> Dict[str, Any]:
    initial_state: SettlementState = {
        "raw_input": raw_input,
        "trace": [],
        "feedback_history": st.session_state.get("feedback_history", []),
    }
    if st.session_state.get("parsed_json"):
        initial_state["parsed_json"] = st.session_state["parsed_json"]
    result = graph.invoke(initial_state)
    if result.get("parsed_json") and not result.get("safety_error"):
        st.session_state["parsed_json"] = result["parsed_json"]
    if result.get("calculation_result"):
        st.session_state["last_result"] = result["calculation_result"]
    st.session_state["last_trace"] = result.get("trace", [])
    st.session_state["feedback_history"] = result.get("feedback_history", st.session_state.get("feedback_history", []))
    return result

# ⭐ [설명 함수 SA-FUNC-10] Streamlit UI — 입력, 세션, 결과 카드, Trace를 한 화면에 묶습니다.
def main():
    st.set_page_config(
        page_title="💸 정산 비서 AI",
        page_icon="💸",
        layout="wide",
    )
    ensure_state()

    st.title("💸 정산 비서 AI")
    st.caption("LangGraph + GPT API + 규칙 기반 계산 엔진으로 만드는 기본 웹서비스 구현 실습")

    with st.sidebar:
        st.header("⚙️ 설정")
        env_key = get_api_key_from_env_or_secrets()
        api_key_input = st.text_input("OPENAI_API_KEY", value="", type="password", help="비워두면 Colab 환경변수/Secrets의 OPENAI_API_KEY를 사용합니다.")
        api_key = api_key_input or env_key
        model = st.text_input("Model", value=get_default_model())
        st.divider()
        st.header("📋 예시 입력")
        for i, example in enumerate(EXAMPLES, start=1):
            if st.button(f"예시 {i} 채우기", key=f"ex{i}"):
                st.session_state["pending_example"] = example
        st.divider()
        st.header("🧪 설계 원칙")
        st.markdown("""
    - LLM은 **예외 조건과 rate 판단**만 담당
    - 금액 계산은 **순수 Python 계산 엔진** 담당
    - 총액 일치 여부는 계산 엔진에서 검증
    - 추가 피드백은 기존 JSON에 예외 조건을 더해 재계산
    """)
        if st.button("🗑️ 대화 초기화"):
            reset_session()
            st.rerun()

    client = make_client(api_key)
    graph = build_graph(client, model)

    if not api_key:
        st.warning("OPENAI_API_KEY가 없어 규칙 기반 fallback으로 동작합니다. 수업에서는 API Key를 설정한 뒤 LLM 파싱 결과와 비교해보세요.")

    if "pending_example" in st.session_state:
        st.info("아래 입력창에 예시가 채워졌습니다. 필요하면 수정 후 전송하세요.")
        default_value = st.session_state.pop("pending_example")
    else:
        default_value = ""

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    with st.form("settlement_form", clear_on_submit=True):
        user_input = st.text_area(
            "정산 상황 입력",
            value=default_value,
            placeholder="예: 총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어.",
            height=120,
        )
        submitted = st.form_submit_button("정산하기 / 피드백 반영")

    if submitted and user_input.strip():
        st.session_state.messages.append({"role": "user", "content": user_input.strip()})
        with st.spinner("LangGraph 워크플로우 실행 중..."):
            result = run_settlement(user_input.strip(), graph)
        report = result.get("final_report", "결과를 생성하지 못했습니다.")
        st.session_state.messages.append({"role": "assistant", "content": report})
        st.rerun()

    if st.session_state.get("last_result"):
        st.divider()
        st.header("📊 마지막 정산 결과 상세")
        render_result({
            "parsed_json": st.session_state.get("parsed_json", {}),
            "calculation_result": st.session_state.get("last_result", {}),
            "trace": st.session_state.get("last_trace", []),
            "safety_error": "",
        })

        st.subheader("💬 추가 피드백 예시")
        st.markdown("""
    이미 정산 결과가 있는 상태에서 아래처럼 입력하면 기존 JSON에 조건을 추가해 재계산합니다.

    - `C는 안주도 거의 안 먹었어.`
    - `B는 중도 귀가했어.`
    - `도윤은 지각비 5000원 말고 20% 더 내기로 했어.`
    """)


if __name__ == "__main__":
    main()
