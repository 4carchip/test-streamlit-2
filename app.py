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
    base = base or datetime.now()
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
        candidate = datetime(y, m, d)
        if candidate.date() < base.date() - timedelta(days=1):
            candidate = datetime(y + 1, m, d)
        return candidate.strftime("%Y-%m-%d")

    match = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if match:
        m, d = map(int, match.groups())
        y = base.year
        candidate = datetime(y, m, d)
        if candidate.date() < base.date() - timedelta(days=1):
            candidate = datetime(y + 1, m, d)
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
            parsed = call_llm_json(client, model, PARSE_SYSTEM_PROMPT, raw)
            parsed = ensure_schedule_schema(parsed)
            trace = "input_parsing:llm"
        except Exception:
            parsed = fallback_parse_schedule(raw)
            trace = "input_parsing:fallback"
        return {"schedule_json": parsed, "trace": [trace]}

    return input_parsing_node


def feedback_parsing_node_factory(client: Optional[OpenAI], model: str):
    def feedback_parsing_node(state: ScheduleState) -> Dict[str, Any]:
        raw = state.get("raw_input", "")
        previous = ensure_schedule_schema(state.get("schedule_json", {}))
        try:
            user_prompt = "기존 일정 JSON:\n" + json.dumps(previous, ensure_ascii=False) + "\n\n수정 요청:\n" + raw
            parsed = call_llm_json(client, model, FEEDBACK_SYSTEM_PROMPT, user_prompt)
            parsed = ensure_schedule_schema(parsed)
            trace = "feedback_parsing:llm"
        except Exception:
            # fallback에서는 삭제/변경까지 완벽히 처리하기 어렵기 때문에 새 일정은 추가하고, 수정 문장은 기록합니다.
            parsed = previous
            additional = fallback_parse_schedule(raw).get("events", [])
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
EXAMPLES = [
    "내일 오전 10시 자료구조 수업, 오후 2시 SQLD 공부, 저녁 7시 친구 약속 있어. SQLD 공부가 제일 중요해.",
    "7월 10일 오후 3시 팀플 회의, 오후 4시 발표 준비, 오후 4시 30분 알바 가야 돼.",
    "오늘 1시 병원, 3시 과제 제출, 6시 헬스장 일정 정리해줘.",
    "월요일 오전 9시 수업, 오후 1시 점심 약속, 오후 2시부터 4시까지 공기업 IT 프로젝트 공부.",
]


def reset_session():
    for key in ["messages", "schedule_json", "last_plan", "last_trace", "feedback_history"]:
        st.session_state.pop(key, None)


def ensure_state():
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "안녕하세요! 자연어로 일정을 입력하면 시간순으로 정리하고 충돌도 확인해드릴게요."}
        ]
    st.session_state.setdefault("feedback_history", [])


def render_result(result: Dict[str, Any]):
    if result.get("schedule_error"):
        st.error(result["schedule_error"])
        return

    plan = result.get("plan_result", {})
    events = plan.get("events", [])
    if events:
        st.subheader("📌 일정 카드")
        cols = st.columns(min(3, max(1, len(events))))
        for idx, event in enumerate(events):
            with cols[idx % len(cols)]:
                st.metric(event.get("title", "제목 미정"), event.get("start_time") or "시간 미정")
                st.caption(f"{event.get('date') or '날짜 미정'} · 우선순위 {event.get('priority', 'medium')}")

    if result.get("validation_errors"):
        st.warning("\n".join(f"- {msg}" for msg in result["validation_errors"]))

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
    st.set_page_config(page_title="📅 일정 비서 AI", page_icon="📅", layout="wide")
    ensure_state()

    st.title("📅 일정 비서 AI")
    st.caption("LangGraph + GPT API + 규칙 기반 검증으로 만드는 일정 정리 챗봇")

    with st.sidebar:
        st.header("⚙️ 설정")
        env_key = get_api_key_from_env_or_secrets()
        api_key_input = st.text_input("OPENAI_API_KEY", value="", type="password", help="비워두면 환경변수/Secrets의 OPENAI_API_KEY를 사용합니다.")
        api_key = api_key_input or env_key
        model = st.text_input("Model", value=get_default_model())
        st.divider()
        st.header("📋 예시 입력")
        for i, example in enumerate(EXAMPLES, start=1):
            if st.button(f"예시 {i} 채우기", key=f"example_{i}"):
                st.session_state["pending_example"] = example
        st.divider()
        st.markdown("""
        **기능**
        - 자연어 일정 파싱
        - 일정 추가/수정 요청 반영
        - 시간순 정렬
        - 일정 충돌 확인
        - 체크리스트 생성
        """)
        if st.button("🗑️ 대화 초기화"):
            reset_session()
            st.rerun()

    client = make_client(api_key)
    graph = build_graph(client, model)

    if not api_key:
        st.warning("OPENAI_API_KEY가 없어 규칙 기반 fallback으로 동작합니다. API Key를 넣으면 자연어 파싱이 더 좋아집니다.")

    if "pending_example" in st.session_state:
        example = st.session_state.pop("pending_example")
        st.session_state.messages.append({"role": "user", "content": example})
        result = run_schedule_assistant(example, graph)
        st.session_state.messages.append({"role": "assistant", "content": result.get("final_message", "결과를 생성하지 못했어요.")})

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
        with st.chat_message("assistant"):
            st.markdown(answer)
        render_result(result)


if __name__ == "__main__":
    main()
