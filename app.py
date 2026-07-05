"""
일정 비서 AI Streamlit 웹서비스

핵심 구조
- LangGraph Workflow: 모드 감지 → 일정 파싱/수정 파싱 → 일정 검증 → 요청 분기 → 일정 정리 → 리포트 생성
- LLM 역할: 자연어 일정에서 날짜, 시간, 장소, 우선순위, 메모를 구조화 JSON으로 변환
- Python 역할: 시간 정렬, 충돌 검사, 일정표 생성, fallback 파싱

실행:
  streamlit run apps/schedule_assistant_app.py
"""

import json
import operator
import os
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from zoneinfo import ZoneInfo

import streamlit as st
from langgraph.graph import END, START, StateGraph
from openai import OpenAI


PARSE_SYSTEM_PROMPT = """
너는 사용자의 자연어 일정을 구조화하는 일정 파서다.
반드시 JSON 객체만 반환한다.

중요 원칙:
1. 사용자의 일정을 임의로 만들지 않는다.
2. 날짜, 시간, 장소, 우선순위, 카테고리, 메모를 가능한 범위에서 추출한다.
3. 날짜나 시간이 불명확하면 null로 둔다.
4. 시간은 가능하면 24시간제 HH:MM 형식으로 반환한다.
5. 일정이 여러 개면 events 배열에 모두 넣는다.
6. 우선순위는 시험, 발표, 마감, 면접, 알바, 병원처럼 중요도가 높으면 high, 보통은 medium, 가벼운 약속은 low로 둔다.
7. 사용자 입력 앞에 제공되는 기준 날짜 정보를 반드시 따른다.
   - '오늘'은 기준 날짜와 같은 날짜다.
   - '내일'은 기준 날짜에 하루를 더한 날짜다.
   - '모레'는 기준 날짜에 이틀을 더한 날짜다.
   - 요일 표현은 기준 날짜 이후에 가장 가까운 해당 요일로 계산한다.

반환 JSON 스키마:
{
  "events": [
    {
      "title": "자료구조 과제 제출",
      "date": "2026-07-06",
      "start_time": "14:00",
      "end_time": null,
      "location": null,
      "priority": "high",
      "category": "school",
      "notes": "제출 마감"
    }
  ],
  "preferences": {
    "buffer_minutes": 30,
    "sort_basis": "time"
  }
}
""".strip()

FEEDBACK_SYSTEM_PROMPT = """
너는 기존 일정 JSON에 사용자의 수정 요청을 반영하는 일정 편집기다.
반드시 수정이 반영된 전체 JSON 객체만 반환한다.

규칙:
1. 기존 일정 중 삭제/변경 요청이 있으면 반영한다.
2. 추가 일정이 있으면 events 배열에 추가한다.
3. 애매한 내용은 notes에 남기고 날짜나 시간은 null로 둔다.
4. 새로 계산하거나 없는 일정을 상상해서 추가하지 않는다.
5. 반환 형식은 기존 JSON과 같은 스키마를 유지한다.
6. 사용자 입력 앞에 제공되는 기준 날짜 정보를 반드시 따른다. 오늘/내일/모레/요일 표현은 기준 날짜 기준으로 계산한다.
""".strip()

REPORT_SYSTEM_PROMPT = """
너는 일정 정리 비서다.
입력으로 받은 plan_result와 validation_errors만 근거로 사용해서 답변한다.
출력은 다음 구조로 작성한다.

1. 오늘/이번 일정 한 줄 요약
2. 시간순 일정표
3. 충돌 또는 애매한 일정 안내
4. 바로 복붙 가능한 체크리스트

친절하고 간단하게 작성한다.
""".strip()


class ScheduleState(TypedDict, total=False):
    raw_input: str
    mode: Literal["initial", "feedback"]
    schedule_json: Dict[str, Any]
    validation_errors: List[str]
    schedule_error: str
    plan_result: Dict[str, Any]
    final_message: str
    feedback_history: List[str]
    trace: Annotated[List[str], operator.add]


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


KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """한국 시간 기준 현재 날짜/시간을 반환합니다."""
    return datetime.now(KST)


def current_date_context(base: Optional[datetime] = None) -> Dict[str, str]:
    """오늘/내일 같은 상대 날짜를 LLM과 fallback 파서가 같은 기준으로 계산하게 합니다."""
    base = base or now_kst()
    return {
        "today": base.strftime("%Y-%m-%d"),
        "tomorrow": (base + timedelta(days=1)).strftime("%Y-%m-%d"),
        "day_after_tomorrow": (base + timedelta(days=2)).strftime("%Y-%m-%d"),
        "weekday": ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][base.weekday()],
        "now": base.strftime("%Y-%m-%d %H:%M"),
    }


def build_llm_user_prompt(raw_input: str, purpose: str = "parse") -> str:
    ctx = current_date_context()
    purpose_label = "일정 파싱" if purpose == "parse" else "일정 수정"
    return f"""
[기준 날짜 정보 - 한국 시간 Asia/Seoul]
현재 시각: {ctx['now']}
오늘: {ctx['today']} ({ctx['weekday']})
내일: {ctx['tomorrow']}
모레: {ctx['day_after_tomorrow']}

[처리 목적]
{purpose_label}

[사용자 입력]
{raw_input}
""".strip()


def normalize_date_value(value: Any, base: Optional[datetime] = None) -> Optional[str]:
    """LLM이 '오늘', '내일', '7월 10일'처럼 반환해도 YYYY-MM-DD로 보정합니다."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return normalize_date(text, base=base)


def count_date_hints(text: str) -> int:
    patterns = [
        r"오늘", r"내일", r"모레",
        r"\d{1,2}\s*월\s*\d{1,2}\s*일",
        r"\d{1,2}/\d{1,2}",
        r"20\d{2}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2}",
        r"월요일|화요일|수요일|목요일|금요일|토요일|일요일",
    ]
    return sum(len(re.findall(p, text)) for p in patterns)


def normalize_schedule_dates(data: Dict[str, Any], raw_input: str = "") -> Dict[str, Any]:
    """상대 날짜와 빠진 날짜를 실행 시점의 한국 날짜 기준으로 후처리합니다."""
    base = now_kst()
    inherited_date = normalize_date(raw_input, base=base)
    raw_date_hint_count = count_date_hints(raw_input)
    for event in data.get("events", []):
        combined = " ".join(
            str(event.get(key) or "")
            for key in ["date", "title", "notes"]
        )
        normalized = normalize_date_value(event.get("date"), base=base)
        if normalized is None:
            normalized = normalize_date(combined, base=base)
        # 입력 전체에 날짜 힌트가 1개뿐이면, 여러 일정이 쉼표로 나뉘어도 같은 날짜를 상속합니다.
        if normalized is None and inherited_date and raw_date_hint_count <= 1:
            normalized = inherited_date
        event["date"] = normalized
    return data



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


def call_llm_json(client: Optional[OpenAI], model: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    if client is None:
        raise RuntimeError("OPENAI_API_KEY가 없어 fallback 파서를 사용합니다.")
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        return extract_json_object(response.output_text)
    except Exception:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        return extract_json_object(response.choices[0].message.content or "")


def normalize_date(text: str, base: Optional[datetime] = None) -> Optional[str]:
    base = base or now_kst()
    text = str(text or "")

    if "오늘" in text:
        return base.strftime("%Y-%m-%d")
    if "내일" in text:
        return (base + timedelta(days=1)).strftime("%Y-%m-%d")
    if "모레" in text:
        return (base + timedelta(days=2)).strftime("%Y-%m-%d")

    match = re.search(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", text)
    if match:
        y, m, d = map(int, match.groups())
        return datetime(y, m, d).strftime("%Y-%m-%d")

    match = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if match:
        m, d = map(int, match.groups())
        y = base.year
        candidate = datetime(y, m, d, tzinfo=KST)
        # 지나간 날짜를 입력하면 다음 해 일정으로 보는 편이 일정 비서에 자연스럽습니다.
        if candidate.date() < base.date():
            candidate = datetime(y + 1, m, d, tzinfo=KST)
        return candidate.strftime("%Y-%m-%d")

    match = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if match:
        m, d = map(int, match.groups())
        y = base.year
        candidate = datetime(y, m, d, tzinfo=KST)
        if candidate.date() < base.date():
            candidate = datetime(y + 1, m, d, tzinfo=KST)
        return candidate.strftime("%Y-%m-%d")

    weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
    for word, target in weekdays.items():
        if word in text:
            delta = (target - base.weekday()) % 7
            if delta == 0:
                delta = 7
            return (base + timedelta(days=delta)).strftime("%Y-%m-%d")
    return None


def _time_matches(text: str):
    pattern = re.compile(r"(?:(오전|오후|저녁|밤|낮|새벽)\s*)?(\d{1,2})(?:\s*시|:)(?:\s*(\d{1,2})\s*분?)?")
    return list(pattern.finditer(text))


def normalize_time_from_match(match: re.Match) -> Optional[str]:
    meridiem = match.group(1) or ""
    hour = int(match.group(2))
    minute = int(match.group(3) or 0)
    if meridiem in ["오후", "저녁", "밤"] and hour < 12:
        hour += 12
    if meridiem in ["오전", "새벽"] and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def guess_priority(text: str) -> str:
    high_keywords = ["시험", "마감", "제출", "발표", "면접", "병원", "알바", "회의", "중요"]
    low_keywords = ["놀", "데이트", "게임", "산책", "카페", "친구"]
    if any(k in text for k in high_keywords):
        return "high"
    if any(k in text for k in low_keywords):
        return "low"
    return "medium"


def guess_category(text: str) -> str:
    if any(k in text for k in ["수업", "과제", "시험", "발표", "학교", "강의"]):
        return "school"
    if any(k in text for k in ["알바", "근무", "회의", "출근", "업무"]):
        return "work"
    if any(k in text for k in ["병원", "운동", "헬스", "약", "치과"]):
        return "health"
    if any(k in text for k in ["친구", "데이트", "약속", "카페", "영화"]):
        return "personal"
    return "etc"


def clean_title(text: str) -> str:
    title = text
    remove_patterns = [
        r"20\d{2}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2}",
        r"\d{1,2}\s*월\s*\d{1,2}\s*일",
        r"\d{1,2}/\d{1,2}",
        r"오늘|내일|모레|월요일|화요일|수요일|목요일|금요일|토요일|일요일",
        r"(?:(오전|오후|저녁|밤|낮|새벽)\s*)?\d{1,2}(?:\s*시|:)(?:\s*\d{1,2}\s*분?)?",
        r"부터|까지|에|에는|일정|해야\s*돼|해야돼|있어",
    ]
    for pat in remove_patterns:
        title = re.sub(pat, " ", title)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    return title or "제목 미정 일정"


def fallback_parse_schedule(raw_input: str) -> Dict[str, Any]:
    text = raw_input.strip()
    chunks = [c.strip() for c in re.split(r"[\n,;]+|그리고|또", text) if c.strip()]
    if not chunks:
        chunks = [text]

    events: List[Dict[str, Any]] = []
    inherited_date = normalize_date(text)
    for chunk in chunks:
        date_value = normalize_date(chunk) or inherited_date
        matches = _time_matches(chunk)
        start_time = normalize_time_from_match(matches[0]) if matches else None
        end_time = normalize_time_from_match(matches[1]) if len(matches) >= 2 else None
        event = {
            "title": clean_title(chunk),
            "date": date_value,
            "start_time": start_time,
            "end_time": end_time,
            "location": None,
            "priority": guess_priority(chunk),
            "category": guess_category(chunk),
            "notes": "fallback 파서로 추출됨",
        }
        events.append(event)

    return {"events": events, "preferences": {"buffer_minutes": 30, "sort_basis": "time"}}


def ensure_schedule_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    events = data.get("events") or []
    normalized = []
    for event in events:
        if not isinstance(event, dict):
            continue
        normalized.append(
            {
                "title": event.get("title") or "제목 미정 일정",
                "date": event.get("date"),
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "location": event.get("location"),
                "priority": event.get("priority") or "medium",
                "category": event.get("category") or "etc",
                "notes": event.get("notes") or "",
            }
        )
    preferences = data.get("preferences") or {}
    preferences.setdefault("buffer_minutes", 30)
    preferences.setdefault("sort_basis", "time")
    return {"events": normalized, "preferences": preferences}


def parse_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    date_value = event.get("date")
    time_value = event.get("start_time")
    if not date_value or not time_value:
        return None
    try:
        return datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def end_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    start = parse_datetime(event)
    if start is None:
        return None
    end_time = event.get("end_time")
    if end_time:
        try:
            return datetime.strptime(f"{event.get('date')} {end_time}", "%Y-%m-%d %H:%M")
        except Exception:
            pass
    return start + timedelta(hours=1)


def format_event_line(event: Dict[str, Any]) -> str:
    date_part = event.get("date") or "날짜 미정"
    time_part = event.get("start_time") or "시간 미정"
    if event.get("end_time"):
        time_part += f"~{event['end_time']}"
    priority_icon = {"high": "🔥", "medium": "•", "low": "▫️"}.get(event.get("priority"), "•")
    location = f" @ {event['location']}" if event.get("location") else ""
    return f"{priority_icon} {date_part} {time_part} | {event.get('title', '제목 미정')}{location}"


# =========================
# LangGraph Node 함수들
# =========================

def detect_mode_node(state: ScheduleState) -> Dict[str, Any]:
    raw = state.get("raw_input", "")
    has_existing = bool(state.get("schedule_json", {}).get("events"))
    feedback_words = ["수정", "변경", "바꿔", "옮겨", "추가", "삭제", "빼", "취소", "다시"]
    mode: Literal["initial", "feedback"] = "feedback" if has_existing and any(w in raw for w in feedback_words) else "initial"
    return {"mode": mode, "trace": [f"detect_mode:{mode}"]}


def input_parsing_node_factory(client: Optional[OpenAI], model: str):
    def input_parsing_node(state: ScheduleState) -> Dict[str, Any]:
        raw = state.get("raw_input", "")
        try:
            user_prompt = build_llm_user_prompt(raw, purpose="parse")
            parsed = call_llm_json(client, model, PARSE_SYSTEM_PROMPT, user_prompt)
            parsed = ensure_schedule_schema(parsed)
            parsed = normalize_schedule_dates(parsed, raw)
            trace = "input_parsing:llm"
        except Exception:
            parsed = fallback_parse_schedule(raw)
            parsed = normalize_schedule_dates(parsed, raw)
            trace = "input_parsing:fallback"
        return {"schedule_json": parsed, "trace": [trace]}

    return input_parsing_node


def feedback_parsing_node_factory(client: Optional[OpenAI], model: str):
    def feedback_parsing_node(state: ScheduleState) -> Dict[str, Any]:
        raw = state.get("raw_input", "")
        previous = ensure_schedule_schema(state.get("schedule_json", {}))
        previous = normalize_schedule_dates(previous, "")
        try:
            user_prompt = (
                build_llm_user_prompt(raw, purpose="feedback")
                + "\n\n[기존 일정 JSON]\n"
                + json.dumps(previous, ensure_ascii=False)
                + "\n\n[수정 요청]\n"
                + raw
            )
            parsed = call_llm_json(client, model, FEEDBACK_SYSTEM_PROMPT, user_prompt)
            parsed = ensure_schedule_schema(parsed)
            parsed = normalize_schedule_dates(parsed, raw)
            trace = "feedback_parsing:llm"
        except Exception:
            # fallback에서는 삭제/변경까지 완벽히 처리하기 어렵기 때문에 새 일정은 추가하고, 수정 문장은 기록합니다.
            parsed = previous
            additional = fallback_parse_schedule(raw).get("events", [])
            additional = normalize_schedule_dates({"events": additional, "preferences": {}}, raw).get("events", [])
            if any(w in raw for w in ["추가", "또", "그리고"]):
                parsed["events"].extend(additional)
            parsed.setdefault("preferences", {"buffer_minutes": 30, "sort_basis": "time"})
            trace = "feedback_parsing:fallback"
        history = state.get("feedback_history", []) + [raw]
        return {"schedule_json": parsed, "feedback_history": history, "trace": [trace]}

    return feedback_parsing_node


def schedule_check_node(state: ScheduleState) -> Dict[str, Any]:
    schedule = ensure_schedule_schema(state.get("schedule_json", {}))
    events = schedule.get("events", [])
    messages: List[str] = []
    conflicts: List[str] = []

    if not events:
        return {"schedule_error": "일정 내용을 찾지 못했어요. 예: '내일 오후 2시 데이터분석 과제 제출'처럼 입력해 주세요.", "trace": ["schedule_check:error"]}

    for event in events:
        if not event.get("date"):
            messages.append(f"'{event.get('title')}' 일정의 날짜가 명확하지 않아요.")
        if not event.get("start_time"):
            messages.append(f"'{event.get('title')}' 일정의 시작 시간이 명확하지 않아요.")

    dated_events = [e for e in events if parse_datetime(e) is not None]
    dated_events.sort(key=lambda e: parse_datetime(e) or datetime.max)
    for prev, cur in zip(dated_events, dated_events[1:]):
        prev_end = end_datetime(prev)
        cur_start = parse_datetime(cur)
        if prev_end and cur_start and prev.get("date") == cur.get("date") and cur_start < prev_end:
            conflicts.append(f"'{prev.get('title')}' 일정과 '{cur.get('title')}' 일정 시간이 겹칠 수 있어요.")

    all_messages = messages + conflicts
    return {"validation_errors": all_messages, "trace": ["schedule_check:ok"]}


def route_request_node(state: ScheduleState) -> Dict[str, Any]:
    return {"trace": ["route_request:plan"]}


def plan_generation_node(state: ScheduleState) -> Dict[str, Any]:
    schedule = ensure_schedule_schema(state.get("schedule_json", {}))
    events = schedule.get("events", [])
    sorted_events = sorted(events, key=lambda e: (e.get("date") or "9999-99-99", e.get("start_time") or "99:99", {"high": 0, "medium": 1, "low": 2}.get(e.get("priority"), 1)))

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in sorted_events:
        grouped.setdefault(event.get("date") or "날짜 미정", []).append(event)

    checklist = [f"[ ] {event.get('title', '제목 미정')}" for event in sorted_events]
    plan_result = {
        "events": sorted_events,
        "grouped": grouped,
        "checklist": checklist,
        "validation_errors": state.get("validation_errors", []),
        "event_count": len(sorted_events),
    }
    return {"plan_result": plan_result, "trace": ["plan_generation"]}


def report_generation_node_factory(client: Optional[OpenAI], model: str):
    def report_generation_node(state: ScheduleState) -> Dict[str, Any]:
        if state.get("schedule_error"):
            return {"final_message": state["schedule_error"], "trace": ["report_generation:error"]}

        plan = state.get("plan_result", {})
        if client is not None:
            try:
                user_prompt = json.dumps(plan, ensure_ascii=False, indent=2)
                response = client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                )
                return {"final_message": response.output_text, "trace": ["report_generation:llm"]}
            except Exception:
                pass

        lines = ["## 📅 일정 정리 결과", ""]
        lines.append(f"총 {plan.get('event_count', 0)}개의 일정을 정리했어요.")
        lines.append("")
        grouped = plan.get("grouped", {})
        for date_value, events in grouped.items():
            lines.append(f"### {date_value}")
            for event in events:
                lines.append(f"- {format_event_line(event)}")
            lines.append("")
        errors = plan.get("validation_errors", [])
        if errors:
            lines.append("### ⚠️ 확인 필요")
            for msg in errors:
                lines.append(f"- {msg}")
            lines.append("")
        lines.append("### ✅ 체크리스트")
        for item in plan.get("checklist", []):
            lines.append(f"- {item}")
        return {"final_message": "\n".join(lines), "trace": ["report_generation:fallback"]}

    return report_generation_node


def mode_router(state: ScheduleState) -> str:
    return state.get("mode", "initial")


def schedule_check_router(state: ScheduleState) -> str:
    if state.get("schedule_error"):
        return "error"
    return "ok"
# 학생 실습 범위처럼 그래프 연결만 따로 분리한 파일입니다.
def build_graph(client: Optional[OpenAI], model: str):
    builder = StateGraph(ScheduleState)

    # 1. Node 등록
    builder.add_node("detect_mode", detect_mode_node)
    builder.add_node("input_parsing", input_parsing_node_factory(client, model))
    builder.add_node("feedback_parsing", feedback_parsing_node_factory(client, model))
    builder.add_node("schedule_check", schedule_check_node)
    builder.add_node("route_request", route_request_node)
    builder.add_node("plan_generation", plan_generation_node)
    builder.add_node("report_generation", report_generation_node_factory(client, model))

    # 2. 시작점 연결
    builder.add_edge(START, "detect_mode")

    # 3. 최초 입력 / 수정 요청 분기
    builder.add_conditional_edges(
        "detect_mode",
        mode_router,
        {
            "initial": "input_parsing",
            "feedback": "feedback_parsing",
        },
    )

    # 4. 두 파싱 경로를 검증 Node로 합치기
    for node in ["input_parsing", "feedback_parsing"]:
        builder.add_edge(node, "schedule_check")

    # 5. 검증 결과에 따라 오류 리포트 또는 정상 일정 정리로 분기
    builder.add_conditional_edges(
        "schedule_check",
        schedule_check_router,
        {
            "error": "report_generation",
            "ok": "route_request",
        },
    )

    # 6. 정상 경로와 종료 연결
    builder.add_edge("route_request", "plan_generation")
    builder.add_edge("plan_generation", "report_generation")
    builder.add_edge("report_generation", END)

    return builder.compile()
import streamlit.components.v1 as components
from urllib.parse import urlencode
from urllib.request import Request, urlopen

EXAMPLES = [
    "오늘 1시 병원, 3시 과제 제출, 6시 헬스장 일정 정리해줘.",
    "내일 오전 10시 자료구조 수업, 오후 2시 SQLD 공부, 저녁 7시 친구 약속 있어. SQLD 공부가 제일 중요해.",
    "7월 10일 오후 3시 팀플 회의, 오후 4시 발표 준비, 오후 4시 30분 알바 가야 돼.",
    "월요일 오전 9시 수업, 오후 1시 점심 약속, 오후 2시부터 4시까지 공기업 IT 프로젝트 공부.",
]

PRIORITY_META = {
    "high": {"label": "중요", "emoji": "🔥", "class": "priority-high"},
    "medium": {"label": "보통", "emoji": "✨", "class": "priority-medium"},
    "low": {"label": "여유", "emoji": "🌿", "class": "priority-low"},
}

CATEGORY_META = {
    "school": "🎓 학교",
    "work": "💼 업무",
    "health": "💪 건강",
    "personal": "🧡 개인",
    "etc": "📌 기타",
}

WEATHER_CODE_META = {
    0: ("☀️", "맑음"),
    1: ("🌤️", "대체로 맑음"),
    2: ("⛅", "구름 조금"),
    3: ("☁️", "흐림"),
    45: ("🌫️", "안개"),
    48: ("🌫️", "서리 안개"),
    51: ("🌦️", "약한 이슬비"),
    53: ("🌦️", "이슬비"),
    55: ("🌦️", "강한 이슬비"),
    56: ("🌧️", "차가운 이슬비"),
    57: ("🌧️", "강한 차가운 이슬비"),
    61: ("🌧️", "약한 비"),
    63: ("🌧️", "비"),
    65: ("🌧️", "강한 비"),
    66: ("🌧️", "차가운 비"),
    67: ("🌧️", "강한 차가운 비"),
    71: ("🌨️", "약한 눈"),
    73: ("🌨️", "눈"),
    75: ("❄️", "강한 눈"),
    77: ("❄️", "싸락눈"),
    80: ("🌦️", "약한 소나기"),
    81: ("🌦️", "소나기"),
    82: ("⛈️", "강한 소나기"),
    85: ("🌨️", "약한 눈 소나기"),
    86: ("🌨️", "강한 눈 소나기"),
    95: ("⛈️", "뇌우"),
    96: ("⛈️", "우박 동반 뇌우"),
    99: ("⛈️", "강한 우박 동반 뇌우"),
}


def reset_session():
    for key in ["messages", "schedule_json", "last_plan", "last_trace", "feedback_history", "pending_example"]:
        st.session_state.pop(key, None)


def ensure_state():
    if "messages" not in st.session_state:
        today = current_date_context()
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    f"안녕하세요! 오늘은 **{today['today']} ({today['weekday']})**이에요.\n\n"
                    "자연어로 일정을 말해주면 날짜를 계산해서 시간순으로 정리하고, 겹치는 일정도 확인해드릴게요. "
                    "상단에는 실시간 전자시계와 현재 지역 날씨도 표시돼요."
                ),
            }
        ]
    st.session_state.setdefault("feedback_history", [])


def inject_css():
    st.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(99, 102, 241, 0.18), transparent 30%),
                radial-gradient(circle at top right, rgba(14, 165, 233, 0.14), transparent 28%),
                linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
        }
        section[data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.78);
            border-right: 1px solid rgba(148, 163, 184, 0.22);
        }
        .hero-card {
            padding: 28px 30px;
            border-radius: 26px;
            background: linear-gradient(135deg, #312e81 0%, #2563eb 50%, #06b6d4 100%);
            color: white;
            box-shadow: 0 18px 45px rgba(30, 64, 175, 0.28);
            margin-bottom: 22px;
        }
        .hero-title {
            font-size: 2.25rem;
            font-weight: 850;
            margin-bottom: 8px;
            letter-spacing: -0.04em;
        }
        .hero-subtitle {
            font-size: 1.02rem;
            opacity: 0.95;
            line-height: 1.65;
        }
        .glass-card {
            padding: 18px 20px;
            border-radius: 22px;
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid rgba(148, 163, 184, 0.22);
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.07);
            min-height: 132px;
            margin-bottom: 16px;
        }
        .event-title {
            font-size: 1.07rem;
            font-weight: 800;
            color: #0f172a;
            margin-bottom: 12px;
            word-break: keep-all;
        }
        .event-line {
            color: #334155;
            font-size: 0.92rem;
            margin: 5px 0;
        }
        .badge {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 800;
            margin-right: 6px;
        }
        .priority-high {
            color: #991b1b;
            background: #fee2e2;
        }
        .priority-medium {
            color: #1e3a8a;
            background: #dbeafe;
        }
        .priority-low {
            color: #166534;
            background: #dcfce7;
        }
        .mini-card {
            padding: 15px 16px;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(148, 163, 184, 0.20);
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
        }
        .mini-label {
            color: #64748b;
            font-size: 0.82rem;
            font-weight: 700;
        }
        .mini-value {
            color: #0f172a;
            font-size: 1.18rem;
            font-weight: 850;
            margin-top: 3px;
        }
        .weather-card {
            padding: 20px 22px;
            border-radius: 24px;
            background: linear-gradient(135deg, rgba(255,255,255,0.94), rgba(239,246,255,0.90));
            border: 1px solid rgba(96, 165, 250, 0.26);
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
            margin-bottom: 16px;
        }
        .weather-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
        }
        .weather-title {
            color: #0f172a;
            font-size: 1.02rem;
            font-weight: 850;
        }
        .weather-location {
            color: #64748b;
            font-size: 0.86rem;
            margin-top: 4px;
        }
        .weather-temp {
            color: #0f172a;
            font-size: 2.1rem;
            font-weight: 900;
            letter-spacing: -0.04em;
            text-align: right;
        }
        .weather-desc {
            color: #2563eb;
            font-size: 0.9rem;
            font-weight: 850;
            text-align: right;
        }
        .weather-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin-top: 14px;
        }
        .weather-item {
            padding: 10px 12px;
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.80);
            border: 1px solid rgba(148, 163, 184, 0.18);
        }
        .weather-item-label {
            color: #64748b;
            font-size: 0.76rem;
            font-weight: 750;
        }
        .weather-item-value {
            color: #0f172a;
            font-size: 0.98rem;
            font-weight: 850;
            margin-top: 3px;
        }
        div[data-testid="stChatMessage"] {
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.70);
            border: 1px solid rgba(148, 163, 184, 0.14);
        }
        .stButton>button {
            border-radius: 14px;
            border: 1px solid rgba(99, 102, 241, 0.28);
            background: rgba(255, 255, 255, 0.86);
            transition: 0.15s ease;
        }
        .stButton>button:hover {
            transform: translateY(-1px);
            border-color: rgba(37, 99, 235, 0.65);
            box-shadow: 0 8px 18px rgba(37, 99, 235, 0.13);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def safe_text(value: Any, default: str = "-") -> str:
    text = str(value) if value not in [None, ""] else default
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_hero():
    ctx = current_date_context()
    st.markdown(
        f"""
        <div class="hero-card">
            <div class="hero-title">📅 일정 비서 AI</div>
            <div class="hero-subtitle">
                오늘 기준 날짜는 <b>{ctx['today']} ({ctx['weekday']})</b>입니다.<br>
                “오늘 약속”, “내일 시험”, “월요일 회의”처럼 적으면 현재 날짜 기준으로 자동 계산해드려요.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_live_clock():
    components.html(
        """
        <div class="clock-card">
            <div class="clock-header">
                <div>
                    <div class="clock-label">LIVE KST DIGITAL CLOCK</div>
                    <div id="clock-date" class="clock-date">----.--.--</div>
                </div>
                <div class="clock-dot"></div>
            </div>
            <div id="clock-time" class="clock-time">--:--:--</div>
            <div class="clock-caption">초 단위로 실시간 갱신됩니다.</div>
        </div>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;800&display=swap');
            body { margin: 0; }
            .clock-card {
                box-sizing: border-box;
                width: 100%;
                min-height: 180px;
                padding: 22px 24px;
                border-radius: 26px;
                background:
                    radial-gradient(circle at 20% 20%, rgba(34,211,238,0.25), transparent 32%),
                    linear-gradient(135deg, #020617 0%, #111827 52%, #1e1b4b 100%);
                color: #e0f2fe;
                border: 1px solid rgba(125, 211, 252, 0.22);
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.26);
                overflow: hidden;
                position: relative;
            }
            .clock-card:before {
                content: "";
                position: absolute;
                inset: 0;
                background-image: linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px);
                background-size: 100% 9px;
                pointer-events: none;
            }
            .clock-header {
                position: relative;
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                z-index: 1;
            }
            .clock-label {
                font-family: Arial, sans-serif;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.16em;
                color: rgba(186, 230, 253, 0.72);
            }
            .clock-date {
                margin-top: 6px;
                font-family: Arial, sans-serif;
                font-size: 15px;
                font-weight: 700;
                color: rgba(224, 242, 254, 0.86);
            }
            .clock-dot {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: #22c55e;
                box-shadow: 0 0 18px #22c55e;
                animation: pulse 1s infinite;
            }
            .clock-time {
                position: relative;
                z-index: 1;
                margin-top: 14px;
                font-family: 'Orbitron', 'Courier New', monospace;
                font-size: clamp(42px, 10vw, 82px);
                font-weight: 800;
                letter-spacing: 0.06em;
                color: #67e8f9;
                text-shadow: 0 0 12px rgba(103, 232, 249, 0.95), 0 0 30px rgba(59, 130, 246, 0.50);
                line-height: 1;
            }
            .clock-caption {
                position: relative;
                z-index: 1;
                margin-top: 12px;
                font-family: Arial, sans-serif;
                font-size: 13px;
                color: rgba(224, 242, 254, 0.68);
            }
            @keyframes pulse {
                0%, 100% { opacity: 0.45; transform: scale(0.86); }
                50% { opacity: 1; transform: scale(1.08); }
            }
        </style>
        <script>
            function updateClock() {
                const now = new Date();
                const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
                    timeZone: 'Asia/Seoul',
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    weekday: 'long'
                });
                const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
                    timeZone: 'Asia/Seoul',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false
                });
                document.getElementById('clock-date').textContent = dateFormatter.format(now);
                document.getElementById('clock-time').textContent = timeFormatter.format(now);
            }
            updateClock();
            setInterval(updateClock, 1000);
        </script>
        """,
        height=196,
    )


def _fetch_json(url: str, params: Dict[str, Any], timeout: int = 7) -> Dict[str, Any]:
    query = urlencode(params, doseq=True)
    request = Request(f"{url}?{query}", headers={"User-Agent": "schedule-assistant-streamlit/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@st.cache_data(ttl=600, show_spinner=False)
def fetch_current_weather(latitude: float, longitude: float) -> Dict[str, Any]:
    return _fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": round(float(latitude), 4),
            "longitude": round(float(longitude), 4),
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "timezone": "auto",
        },
    )


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_city_name(city_name: str) -> Optional[Dict[str, Any]]:
    city_name = (city_name or "").strip()
    if not city_name:
        return None
    data = _fetch_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city_name, "count": 1, "language": "ko", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        return None
    item = results[0]
    parts = [item.get("name"), item.get("admin1"), item.get("country")]
    label = ", ".join(str(p) for p in parts if p)
    return {
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "label": label or city_name,
    }


@st.cache_data(ttl=86400, show_spinner=False)
def reverse_geocode_coords(latitude: float, longitude: float) -> str:
    try:
        data = _fetch_json(
            "https://geocoding-api.open-meteo.com/v1/reverse",
            {
                "latitude": round(float(latitude), 4),
                "longitude": round(float(longitude), 4),
                "count": 1,
                "language": "ko",
                "format": "json",
            },
        )
        results = data.get("results") or []
        if results:
            item = results[0]
            parts = [item.get("name"), item.get("admin1"), item.get("country")]
            return ", ".join(str(p) for p in parts if p) or "현재 위치"
    except Exception:
        pass
    return "현재 위치"


def get_browser_location() -> Optional[Dict[str, Any]]:
    try:
        from streamlit_geolocation import streamlit_geolocation
    except Exception:
        return None

    try:
        return streamlit_geolocation()
    except Exception:
        return None


def weather_code_to_meta(code: Any):
    try:
        return WEATHER_CODE_META.get(int(code), ("🌡️", "날씨 정보"))
    except Exception:
        return ("🌡️", "날씨 정보")


def render_weather_card(weather: Dict[str, Any], location_label: str):
    current = weather.get("current") or {}
    units = weather.get("current_units") or {}
    emoji, description = weather_code_to_meta(current.get("weather_code"))

    temp = current.get("temperature_2m")
    feels_like = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")
    rain = current.get("precipitation")
    observed_time = current.get("time") or "-"

    temp_unit = units.get("temperature_2m", "°C")
    feels_unit = units.get("apparent_temperature", "°C")
    humidity_unit = units.get("relative_humidity_2m", "%")
    wind_unit = units.get("wind_speed_10m", "km/h")
    rain_unit = units.get("precipitation", "mm")

    st.markdown(
        f"""
        <div class="weather-card">
            <div class="weather-top">
                <div>
                    <div class="weather-title">🌦️ 현재 지역 날씨</div>
                    <div class="weather-location">📍 {safe_text(location_label)} · 관측 {safe_text(observed_time)}</div>
                </div>
                <div>
                    <div class="weather-temp">{safe_text(temp)}{safe_text(temp_unit)}</div>
                    <div class="weather-desc">{emoji} {safe_text(description)}</div>
                </div>
            </div>
            <div class="weather-grid">
                <div class="weather-item">
                    <div class="weather-item-label">체감</div>
                    <div class="weather-item-value">{safe_text(feels_like)}{safe_text(feels_unit)}</div>
                </div>
                <div class="weather-item">
                    <div class="weather-item-label">습도</div>
                    <div class="weather-item-value">{safe_text(humidity)}{safe_text(humidity_unit)}</div>
                </div>
                <div class="weather-item">
                    <div class="weather-item-label">바람</div>
                    <div class="weather-item-value">{safe_text(wind)} {safe_text(wind_unit)}</div>
                </div>
                <div class="weather-item">
                    <div class="weather-item-label">강수량</div>
                    <div class="weather-item-value">{safe_text(rain)}{safe_text(rain_unit)}</div>
                </div>
                <div class="weather-item">
                    <div class="weather-item-label">기준</div>
                    <div class="weather-item-value">Open-Meteo</div>
                </div>
                <div class="weather-item">
                    <div class="weather-item-label">갱신</div>
                    <div class="weather-item-value">10분 캐시</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_weather_panel():
    st.markdown("#### 🌦️ 현재 지역 날씨")
    st.caption("브라우저 위치 권한을 허용하면 현재 위치 기준으로 표시됩니다. 권한이 안 뜨면 지역명을 직접 입력하세요.")

    manual_city = st.text_input(
        "날씨 지역 직접 입력",
        value="",
        placeholder="예: 안성, 서울, 수원",
        label_visibility="collapsed",
    )

    latitude = longitude = None
    location_label = "현재 위치"

    if manual_city.strip():
        try:
            place = geocode_city_name(manual_city)
        except Exception as exc:
            st.warning(f"지역 검색 중 오류가 발생했어요: {exc}")
            place = None
        if not place:
            st.info("해당 지역을 찾지 못했어요. 예: 안성, 서울, 수원처럼 입력해보세요.")
            return
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        location_label = place.get("label") or manual_city
    else:
        location = get_browser_location()
        if location and location.get("latitude") is not None and location.get("longitude") is not None:
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            location_label = reverse_geocode_coords(float(latitude), float(longitude))
        else:
            st.info("현재 위치 날씨를 보려면 위치 권한을 허용하거나 위 입력창에 지역명을 입력하세요.")
            return

    try:
        weather = fetch_current_weather(float(latitude), float(longitude))
        render_weather_card(weather, location_label)
    except Exception as exc:
        st.warning(f"날씨 정보를 불러오지 못했어요. 인터넷 연결이나 지역명을 확인해 주세요. ({exc})")


def render_realtime_widgets():
    render_live_clock()
    render_weather_panel()
    st.divider()


def render_stat_cards(plan: Dict[str, Any], errors: List[str]):
    events = plan.get("events", [])
    first_event = events[0] if events else {}
    first_label = "없음"
    if first_event:
        first_label = f"{first_event.get('date') or '날짜 미정'} {first_event.get('start_time') or '시간 미정'}"

    c1, c2, c3 = st.columns(3)
    cards = [
        ("총 일정", f"{len(events)}개"),
        ("확인 필요", f"{len(errors)}개"),
        ("가장 빠른 일정", first_label),
    ]
    for col, (label, value) in zip([c1, c2, c3], cards):
        with col:
            st.markdown(
                f"""
                <div class="mini-card">
                    <div class="mini-label">{safe_text(label)}</div>
                    <div class="mini-value">{safe_text(value)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_event_cards(events: List[Dict[str, Any]]):
    if not events:
        st.info("아직 정리된 일정이 없어요. 채팅창에 일정을 입력해 주세요.")
        return

    st.subheader("📌 일정 카드")
    cols = st.columns(3)
    for idx, event in enumerate(events):
        priority = PRIORITY_META.get(event.get("priority"), PRIORITY_META["medium"])
        category = CATEGORY_META.get(event.get("category"), "📌 기타")
        time_text = event.get("start_time") or "시간 미정"
        if event.get("end_time"):
            time_text += f" ~ {event['end_time']}"
        location = event.get("location") or "장소 미정"
        notes = event.get("notes") or "메모 없음"

        with cols[idx % 3]:
            st.markdown(
                f"""
                <div class="glass-card">
                    <div>
                        <span class="badge {priority['class']}">{priority['emoji']} {priority['label']}</span>
                        <span class="badge priority-medium">{safe_text(category)}</span>
                    </div>
                    <div class="event-title">{safe_text(event.get('title', '제목 미정'))}</div>
                    <div class="event-line">🗓️ {safe_text(event.get('date') or '날짜 미정')}</div>
                    <div class="event-line">⏰ {safe_text(time_text)}</div>
                    <div class="event-line">📍 {safe_text(location)}</div>
                    <div class="event-line">📝 {safe_text(notes)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_result(result: Dict[str, Any]):
    if result.get("schedule_error"):
        st.error(result["schedule_error"])
        return

    plan = result.get("plan_result", {})
    events = plan.get("events", [])
    errors = result.get("validation_errors") or plan.get("validation_errors", [])

    render_stat_cards(plan, errors)
    render_event_cards(events)

    if errors:
        st.warning("\n".join(f"- {msg}" for msg in errors))

    with st.expander("구조화 JSON 보기"):
        st.json(result.get("schedule_json", {}))
    with st.expander("일정 정리 결과 데이터 보기"):
        st.json(result.get("plan_result", {}))
    with st.expander("LangGraph 실행 Trace"):
        st.write(" → ".join(result.get("trace", [])))


def run_schedule_assistant(raw_input: str, graph) -> Dict[str, Any]:
    initial_state: ScheduleState = {
        "raw_input": raw_input,
        "trace": [],
        "feedback_history": st.session_state.get("feedback_history", []),
    }
    if st.session_state.get("schedule_json"):
        initial_state["schedule_json"] = st.session_state["schedule_json"]

    result = graph.invoke(initial_state)
    if result.get("schedule_json") and not result.get("schedule_error"):
        st.session_state["schedule_json"] = result["schedule_json"]
    if result.get("plan_result"):
        st.session_state["last_plan"] = result["plan_result"]
    st.session_state["last_trace"] = result.get("trace", [])
    st.session_state["feedback_history"] = result.get("feedback_history", st.session_state.get("feedback_history", []))
    return result


def main():
    st.set_page_config(page_title="일정 비서 AI", page_icon="📅", layout="wide")
    inject_css()
    ensure_state()
    render_hero()
    render_realtime_widgets()

    with st.sidebar:
        st.header("⚙️ 설정")
        env_key = get_api_key_from_env_or_secrets()
        api_key_input = st.text_input(
            "OPENAI_API_KEY",
            value="",
            type="password",
            help="비워두면 환경변수/Secrets의 OPENAI_API_KEY를 사용합니다.",
        )
        api_key = api_key_input or env_key
        model = st.text_input("Model", value=get_default_model())
        st.caption("API Key가 있으면 GPT가 자연어를 더 정확히 파싱하고, 없으면 규칙 기반 fallback으로 동작합니다.")

        st.divider()
        st.header("📋 예시 입력")
        for i, example in enumerate(EXAMPLES, start=1):
            if st.button(f"예시 {i}", key=f"example_{i}", use_container_width=True):
                st.session_state["pending_example"] = example

        st.divider()
        st.markdown(
            """
            **지원 기능**
            - 전자시계 실시간 표시
            - 현재 위치 날씨 표시
            - 지역명 직접 입력 날씨 조회
            - 오늘/내일/모레 날짜 자동 계산
            - 요일 기반 날짜 계산
            - 자연어 일정 파싱
            - 일정 추가/수정 요청 반영
            - 시간순 정렬
            - 일정 충돌 확인
            - 체크리스트 생성
            """
        )
        if st.button("🗑️ 대화 초기화", use_container_width=True):
            reset_session()
            st.rerun()

    client = make_client(api_key)
    graph = build_graph(client, model)

    if not api_key:
        st.warning("OPENAI_API_KEY가 없어 규칙 기반 fallback으로 동작합니다. API Key를 넣으면 자연어 파싱 성능이 더 좋아집니다.")

    if "pending_example" in st.session_state:
        example = st.session_state.pop("pending_example")
        st.session_state.messages.append({"role": "user", "content": example})
        result = run_schedule_assistant(example, graph)
        st.session_state.messages.append({"role": "assistant", "content": result.get("final_message", "결과를 생성하지 못했어요.")})
        st.session_state["latest_result"] = result
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("예: 내일 오후 2시 팀플 회의, 5시 알바 있어. 일정 정리해줘.")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        result = run_schedule_assistant(prompt, graph)
        answer = result.get("final_message", "결과를 생성하지 못했어요.")
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.session_state["latest_result"] = result
        with st.chat_message("assistant"):
            st.markdown(answer)
        render_result(result)
    elif st.session_state.get("latest_result"):
        render_result(st.session_state["latest_result"])


if __name__ == "__main__":
    main()
