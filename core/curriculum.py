"""진로 로드맵 · 선수 과목 가이드.

자격증 ↔ 학과 과목, 진로 ↔ 자격증 연결은 AI가 만들지 않는다.
data/curriculum/의 CSV를 읽어서 계산만 하고, 사람이 확인하지 않은(verified=FALSE) 연결은
화면에서 '검토 전'으로 따로 표시한다. (출제기준 매핑 core/mapping.py와 같은 원칙)

- courses.csv       학과 과목과 개설 학기 (term: "2-1" = 2학년 1학기, "4" = 4학년·학기 미정)
- cert_courses.csv  자격증별로 먼저 들으면 좋은 과목
- career_certs.csv  진로별 핵심 · 함께 준비할 자격증
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "curriculum"
ROLE_ORDER = {"핵심": 0, "함께": 1}


# ---------------- 데이터 읽기 ----------------

def _read(name: str) -> list[dict]:
    path = DATA / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _true(v) -> bool:
    return str(v).strip().upper() == "TRUE"


def courses() -> dict[str, dict]:
    return {r["course_id"]: r for r in _read("courses.csv")}


def cert_course_rows() -> list[dict]:
    return _read("cert_courses.csv")


def career_rows() -> list[dict]:
    return _read("career_certs.csv")


# ---------------- 학기 ----------------

def term_key(term: str) -> tuple[int, int]:
    """'2-1' → (2, 1), '4' → (4, 0). 정렬과 비교에 쓴다."""
    term = str(term).strip()
    if "-" in term:
        y, s = term.split("-", 1)
        return int(y), int(s)
    return int(term), 0


def term_label(term: str) -> str:
    y, s = term_key(term)
    return f"{y}학년 {s}학기" if s else f"{y}학년"


TERMS = ["1-1", "1-2", "2-1", "2-2", "3-1", "3-2", "4-1", "4-2"]


def is_before(term: str, current: str) -> bool:
    """term 학기가 current 학기보다 앞이면 True. '4'(학기 미정)는 4-1보다 앞으로 보지 않는다."""
    return term_key(term) < term_key(current) and not (term_key(term)[1] == 0 and term_key(term)[0] == term_key(current)[0])


# ---------------- 선수 과목 가이드 ----------------

def certs() -> list[str]:
    seen: list[str] = []
    for r in cert_course_rows():
        if r["cert"] not in seen:
            seen.append(r["cert"])
    return seen


def cert_plan(cert: str, rows: list[dict] | None = None, catalog: dict[str, dict] | None = None) -> list[dict]:
    """자격증에 필요한 과목을 개설 학기 순으로 돌려준다."""
    rows = cert_course_rows() if rows is None else rows
    catalog = courses() if catalog is None else catalog
    out = []
    for r in rows:
        if r["cert"] != cert or r["course_id"] not in catalog:
            continue
        c = catalog[r["course_id"]]
        out.append({
            "course_id": c["course_id"], "course": c["course"], "term": c["term"],
            "note": c.get("note", ""), "verified": _true(r.get("verified")),
        })
    return sorted(out, key=lambda c: (term_key(c["term"]), c["course"]))


def ready_term(plan: list[dict]) -> str | None:
    """필요한 과목을 모두 들을 수 있는 가장 이른 학기 (= 마지막 과목의 개설 학기)."""
    return max((c["term"] for c in plan), key=term_key) if plan else None


def readiness(plan: list[dict], taken: set[str], current: str | None = None) -> dict:
    """이수 체크 결과로 준비도와 다음에 들을 과목을 계산한다.

    - missed: 현재 학기보다 앞에 개설됐는데 아직 안 들은 과목 (재수강·별도 학습 필요)
    - upcoming: 앞으로 개설되는 과목. 가장 가까운 학기의 과목이 next_courses
    """
    done = [c for c in plan if c["course_id"] in taken]
    left = [c for c in plan if c["course_id"] not in taken]
    missed = [c for c in left if current and is_before(c["term"], current)]
    upcoming = [c for c in left if c not in missed]
    next_term = min((c["term"] for c in upcoming), key=term_key) if upcoming else None
    return {
        "done": done,
        "left": left,
        "missed": missed,
        "upcoming": upcoming,
        "next_term": next_term,
        "next_courses": [c for c in upcoming if c["term"] == next_term],
        "ratio": len(done) / len(plan) if plan else 0.0,
        "all_verified": all(c["verified"] for c in plan),
    }


# ---------------- 진로 로드맵 ----------------

def careers(rows: list[dict] | None = None) -> list[str]:
    rows = career_rows() if rows is None else rows
    seen: list[str] = []
    for r in rows:
        if r["career"] not in seen:
            seen.append(r["career"])
    return seen


def roadmap(career: str, rows: list[dict] | None = None, plans: dict[str, list[dict]] | None = None) -> list[dict]:
    """진로에 맞는 자격증을 취득 순서대로 돌려준다.

    순서: 선수 과목을 모두 들을 수 있는 학기(ready_term)가 이른 것부터, 같으면 '핵심' 먼저.
    순서 자체는 과목 데이터로 계산하므로 AI가 정하지 않는다.
    """
    rows = career_rows() if rows is None else rows
    out = []
    for r in rows:
        if r["career"] != career:
            continue
        plan = plans.get(r["cert"], []) if plans is not None else cert_plan(r["cert"])
        out.append({
            "cert": r["cert"], "role": r["role"], "rationale": r.get("rationale", ""),
            "verified": _true(r.get("verified")), "source": r.get("source", ""),
            "ready_term": ready_term(plan), "n_courses": len(plan),
        })
    far = (9, 9)
    return sorted(out, key=lambda x: (term_key(x["ready_term"]) if x["ready_term"] else far,
                                      ROLE_ORDER.get(x["role"], 9), x["cert"]))


# ---------------- 이수 과목 상태 (여러 페이지 공용) ----------------

def taken() -> set[str]:
    import streamlit as st
    return st.session_state.setdefault("taken_courses", set())
