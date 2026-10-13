"""스마트 오답노트: 오인 원인 분류 + 간격 반복 복습 + 유사 유형 문제 추천.

- 오인 원인: AI가 먼저 추정하고(suggested), 학생이 확정한다(cause).
- 간격 반복: 라이트너 상자 5칸(1·3·7·14·30일). 맞히면 다음 칸, 틀리면 첫 칸.
  마지막 칸에서 또 맞히면 '익힘'으로 졸업.
- 유사 유형: 같은 개념(출제기준 항목) + 같은 오인 원인으로 새 문제를 만든다.
  교차검증이 켜져 있으면 두 모델의 정답이 일치한 문제만 문제 은행에 넣는다.

저장: 세션 메모리. 새로고침해도 이어 가려면 JSON으로 내려받았다가 다시 올린다.
(학생 개인 기록이라 서버 파일에 모으지 않는다)
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from core import llm, quizgen, state
from core.config import ModelCfg

KST = ZoneInfo("Asia/Seoul")
INTERVALS = [1, 3, 7, 14, 30]          # 상자별 복습 간격(일)
CAUSES = ["개념 미숙지", "유사 개념 혼동", "용어·정의 혼동", "문제 오독", "적용 실수"]
CAUSE_HELP = {
    "개념 미숙지": "개념 자체를 아직 모름 → 교재 근거 설명부터 다시",
    "유사 개념 혼동": "비슷한 두 개념을 헷갈림 → 두 개념을 구분해야 풀 수 있는 문제",
    "용어·정의 혼동": "용어와 뜻을 엇갈려 기억함 → 용어-정의 짝짓기 문제",
    "문제 오독": "'옳지 않은 것' 같은 조건을 놓침 → 부정형 문제",
    "적용 실수": "개념은 알지만 적용·계산에서 틀림 → 같은 구조, 다른 값의 문제",
}
SIMILAR_RULE = {
    "개념 미숙지": "핵심 개념의 정의와 특징을 묻는 기본 문제로 만든다.",
    "유사 개념 혼동": "학생이 고른 오답 개념과 정답 개념을 구분해야만 풀 수 있게 만든다.",
    "용어·정의 혼동": "용어와 정의를 짝짓거나, 정의를 보고 용어를 고르는 문제로 만든다.",
    "문제 오독": "'옳지 않은 것', '아닌 것'처럼 부정형으로 묻는 문제를 섞는다.",
    "적용 실수": "같은 구조이지만 조건이나 값이 다른 적용 문제로 만든다.",
}

CAUSE_PROMPT = """학생이 틀린 4지선다 문제들이다. 각 문제에서 학생이 틀린 원인을 아래 다섯 가지 중 하나로 추정하라.
원인: 개념 미숙지 / 유사 개념 혼동 / 용어·정의 혼동 / 문제 오독 / 적용 실수
학생이 고른 보기와 정답 보기의 차이를 근거로 판단하고, 이유를 한 문장으로 쓴다.
JSON만 출력하라. {{"items": [{{"cause": "유사 개념 혼동", "reason": "..."}}]}}

{items}
"""


# ---------------- 저장소 ----------------

def today() -> date:
    return datetime.now(KST).date()


def notes() -> list[dict]:
    return st.session_state.setdefault("wrongnote", [])


def bank() -> list[dict]:
    """유사 유형 문제 은행 (검증을 통과한 문제만)."""
    return st.session_state.setdefault("question_bank", [])


def _qid(q: dict) -> str:
    return hashlib.sha1((q["question"] + "|".join(q["choices"])).encode()).hexdigest()[:10]


def find(qid: str) -> dict | None:
    return next((n for n in notes() if n["id"] == qid), None)


# ---------------- 오인 원인 추정 ----------------

def _heuristic_cause(q: dict, picked: int | None) -> tuple[str, str]:
    """AI를 쓸 수 없을 때의 간단한 규칙 (데모 모드·오류 대비)."""
    text = q["question"]
    if any(w in text for w in ["않은", "아닌", "틀린", "옳지"]):
        return "문제 오독", "부정형 문제에서 조건을 놓쳤을 가능성이 큽니다."
    if picked:
        mine, right = q["choices"][picked - 1], q["choices"][q["answer"] - 1]
        shared = set(mine.split()) & set(right.split())
        if shared:
            return "유사 개념 혼동", "고른 보기와 정답이 비슷한 표현을 공유합니다."
    return "개념 미숙지", "정답 개념에 대한 설명이 고른 보기와 크게 다릅니다."


def suggest_causes(model: ModelCfg, wrongs: list[tuple[dict, int | None]]) -> list[tuple[str, str]]:
    """틀린 문제들의 원인을 한 번의 호출로 추정한다."""
    if not wrongs:
        return []
    if model.is_demo:
        return [_heuristic_cause(q, p) for q, p in wrongs]
    lines = []
    for i, (q, p) in enumerate(wrongs, 1):
        mine = q["choices"][p - 1] if p else "선택 안 함"
        lines.append(f"{i}. 문제: {q['question']}\n   보기: " + " / ".join(f"({j+1}) {c}" for j, c in enumerate(q["choices"]))
                     + f"\n   정답: ({q['answer']}) {q['choices'][q['answer'] - 1]}\n   학생 답: {mine}")
    try:
        raw = llm.chat(model, [{"role": "user", "content": CAUSE_PROMPT.format(items="\n".join(lines))}],
                       purpose="cause", max_tokens=800, temperature=0)
        items = llm.parse_json(raw).get("items", [])
    except Exception:
        items = []
    out = []
    for i, (q, p) in enumerate(wrongs):
        it = items[i] if i < len(items) else {}
        cause = it.get("cause") if it.get("cause") in CAUSES else None
        out.append((cause, it.get("reason", "")) if cause else _heuristic_cause(q, p))
    return out


# ---------------- 기록 ----------------

def record_quiz(questions: list[dict], picks: list[int | None], doc: str, model: ModelCfg) -> int:
    """채점 결과를 오답노트에 반영한다. 새로 들어간 오답 수를 돌려준다."""
    wrongs = [(q, p) for q, p in zip(questions, picks) if p != q["answer"]]
    fresh = [(q, p) for q, p in wrongs if not find(_qid(q))]
    causes = suggest_causes(model, fresh)
    for (q, p), (cause, reason) in zip(fresh, causes):
        notes().append({
            "id": _qid(q), "doc": doc, "question": q["question"], "choices": q["choices"],
            "answer": q["answer"], "my_answer": p, "explanation": q.get("explanation", ""),
            "page": q.get("page"), "concept": q.get("concept", ""),
            "suggested": cause, "reason": reason, "cause": cause, "confirmed": False,
            "box": 0, "due": (today() + timedelta(days=INTERVALS[0])).isoformat(),
            "added": today().isoformat(), "history": [], "mastered": False,
        })
    # 이미 오답노트에 있던 문제를 또 틀리면 첫 칸으로
    for q, p in wrongs:
        n = find(_qid(q))
        if n and (q, p) not in fresh:
            review(n, correct=False)
    return len(fresh)


def review(n: dict, correct: bool) -> str:
    """복습 결과로 상자를 옮기고 다음 복습일을 정한다. 안내 문장을 돌려준다."""
    n["history"].append({"date": today().isoformat(), "correct": correct})
    if correct:
        if n["box"] >= len(INTERVALS) - 1:
            n["mastered"] = True
            return "마지막 칸까지 맞혔어요. 이 문제는 '익힘'으로 옮깁니다."
        n["box"] += 1
    else:
        n["box"] = 0
    days = INTERVALS[n["box"]]
    n["due"] = (today() + timedelta(days=days)).isoformat()
    return f"{'정답' if correct else '오답'} → {days}일 뒤({n['due']})에 다시 냅니다."


def due_items(include_future: bool = False) -> list[dict]:
    t = today().isoformat()
    items = [n for n in notes() if not n["mastered"] and (include_future or n["due"] <= t)]
    return sorted(items, key=lambda n: (n["due"], n["box"]))


# ---------------- 유사 유형 추천 ----------------

def recommend(n: dict) -> list[dict]:
    """문제 은행에서 같은 개념·같은 원인·아직 안 푼 문제를 먼저 고른다."""
    solved = st.session_state.setdefault("bank_solved", set())
    def score(q):
        return (q.get("concept") == n["concept"]) * 2 + (q.get("cause") == n["cause"]) * 1
    cands = [q for q in bank() if q["id"] not in solved and q.get("source") == n["id"]] or \
            [q for q in bank() if q["id"] not in solved and score(q) >= 2]
    return sorted(cands, key=score, reverse=True)[:2]


def make_similar(n: dict, main: ModelCfg, verifier: ModelCfg | None, k: int = 2) -> tuple[list[dict], int]:
    """같은 개념·같은 원인의 새 문제를 만든다. (추가된 문제, 검증에서 걸러진 수)"""
    idx = state.index()
    if not idx:
        raise ValueError("교재 PDF를 올려야 유사 문제를 만들 수 있어요.")
    query = " ".join(x for x in [n["concept"], n["question"], n["choices"][n["answer"] - 1]] if x)
    chunks = [c for _, c in idx.search(query, 4)]
    if not chunks:
        raise ValueError("교재에서 이 개념의 근거를 찾지 못했어요.")
    cause = n.get("cause") or "개념 미숙지"
    mine = n["choices"][n["my_answer"] - 1] if n.get("my_answer") else ""
    extra = (f"- 이번 문제의 핵심 개념: {n['concept'] or '원래 문제와 같은 개념'}\n"
             f"- 학생의 오인 원인: {cause}. {SIMILAR_RULE[cause]}\n"
             + (f"- 학생이 헷갈린 보기: {mine}\n" if mine else "")
             + "- 원래 문제와 문장이 같으면 안 된다.")
    qs = quizgen.generate(chunks, k, "원래 문제보다 한 단계 높게", main, verifier, extra=extra)
    kept = [q for q in qs if q.get("check", "ok") == "ok"]
    for q in kept:
        q.update({"id": _qid(q), "concept": q.get("concept") or n["concept"], "cause": cause, "source": n["id"],
                  "verified": "check" in q})
        if not any(b["id"] == q["id"] for b in bank()):
            bank().append(q)
    return kept, len(qs) - len(kept)


# ---------------- 약점 분석 ----------------

def stats() -> dict:
    by_cause = {c: 0 for c in CAUSES}
    by_concept: dict[str, int] = {}
    for n in notes():
        if n.get("cause"):
            by_cause[n["cause"]] += 1
        key = n.get("concept") or "(개념 미지정)"
        by_concept[key] = by_concept.get(key, 0) + 1
    return {"cause": by_cause, "concept": dict(sorted(by_concept.items(), key=lambda x: -x[1]))}


# ---------------- 내보내기 / 불러오기 ----------------

def export_json() -> str:
    return json.dumps({"version": 1, "notes": notes(), "bank": bank()}, ensure_ascii=False, indent=2)


def import_json(text: str) -> int:
    data = json.loads(text)
    existing = {n["id"] for n in notes()}
    added = [n for n in data.get("notes", []) if n.get("id") not in existing]
    notes().extend(added)
    have = {q["id"] for q in bank()}
    bank().extend(q for q in data.get("bank", []) if q.get("id") not in have)
    return len(added)
