"""출제기준 기반 교차 출제 안내.

"이 개념은 정보처리기사 필기에도 나온다"는 문장을 AI가 지어내지 않도록,
data/exam_standards/의 CSV를 '검색'해서 사람이 확인한(verified) 연결만 단정해서 보여 준다.
"""

from __future__ import annotations

import csv
from pathlib import Path

import streamlit as st

DATA = Path(__file__).resolve().parent.parent / "data" / "exam_standards"

RELATION_TEXT = {
    "동일개념": "에서도 같은 개념으로 출제됩니다",
    "부분겹침": "에서 다른 관점(설계·구현)으로 다룹니다",
    "용어공유": "와 용어만 공유합니다 (출제 범위는 다름)",
}


def _true(v) -> bool:
    return str(v).strip().upper() == "TRUE"


@st.cache_data(show_spinner=False)
def _load(name: str) -> list[dict]:
    path = DATA / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find(text: str) -> dict:
    """텍스트에 나온 개념을 찾고, 연결된 출제기준 항목을 돌려준다."""
    concepts = _load("concepts.csv")
    links = _load("links.csv")
    items = {i["item_id"]: i for i in _load("exam_items.csv")}

    confirmed, pending = [], []
    seen = set()
    for c in concepts:
        names = [c["concept"]] + [a.strip() for a in c.get("aliases", "").split(",") if a.strip()]
        if not any(n and n in text for n in names):
            continue
        for link in links:
            if link["concept_id"] != c["concept_id"] or link["link_id"] in seen:
                continue
            item = items.get(link["to_item_id"])
            if not item:
                continue
            seen.add(link["link_id"])
            row = {
                "concept": c["concept"],
                "exam": item["exam"],
                "subject": f"{item['subject_no']}과목 {item['subject']}",
                "topic": item.get("main_topic", ""),
                "relation": link["relation"],
                "phrase": RELATION_TEXT.get(link["relation"], " 참고"),
                "rationale": link.get("rationale", ""),
            }
            if _true(link["verified"]) and _true(item["verified"]):
                confirmed.append(row)
            else:
                pending.append(row)
    return {"confirmed": confirmed, "pending": pending}
