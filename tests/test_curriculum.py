from core import curriculum as cur


# ---------------- 학기 ----------------

def test_term_key_orders_terms():
    assert sorted(["3-2", "1-1", "4", "2-2"], key=cur.term_key) == ["1-1", "2-2", "3-2", "4"]


def test_term_label():
    assert cur.term_label("3-2") == "3학년 2학기"
    assert cur.term_label("4") == "4학년"


def test_is_before():
    assert cur.is_before("2-1", "3-1")
    assert not cur.is_before("3-2", "3-2")
    assert not cur.is_before("4", "4-1")      # 4학년(학기 미정)은 4학년 1학기보다 앞으로 보지 않음
    assert not cur.is_before("4", "4-2")


# ---------------- 데이터 무결성 ----------------

def test_every_cert_course_exists():
    catalog = cur.courses()
    missing = [r["course_id"] for r in cur.cert_course_rows() if r["course_id"] not in catalog]
    assert missing == []


def test_every_career_cert_has_plan():
    known = set(cur.certs())
    assert {r["cert"] for r in cur.career_rows()} <= known


def test_career_roles_are_known():
    assert {r["role"] for r in cur.career_rows()} <= set(cur.ROLE_ORDER)


# ---------------- 선수 과목 가이드 (계획서 2.2 표) ----------------

def test_gigi_plan_ends_in_3_2():
    plan = cur.cert_plan("정보처리기사")
    assert len(plan) == 10
    assert plan[0]["course"] == "컴퓨터공학입문과파이썬"
    assert cur.ready_term(plan) == "3-2"
    assert {c["course"] for c in plan if c["term"] == "3-2"} == {"데이터베이스", "소프트웨어공학", "운영체제"}


def test_network_plan_ends_in_4th_year():
    assert cur.ready_term(cur.cert_plan("네트워크관리사 2급")) == "4"


def test_unknown_cert_is_empty():
    assert cur.cert_plan("없는 자격증") == []
    assert cur.ready_term([]) is None


def _plan():
    return [
        {"course_id": "A", "course": "가", "term": "1-1", "note": "", "verified": True},
        {"course_id": "B", "course": "나", "term": "2-1", "note": "", "verified": True},
        {"course_id": "C", "course": "다", "term": "3-2", "note": "", "verified": False},
        {"course_id": "D", "course": "라", "term": "3-2", "note": "", "verified": True},
    ]


def test_readiness_ratio_and_next():
    r = cur.readiness(_plan(), {"A"}, current="2-1")
    assert r["ratio"] == 0.25
    assert [c["course_id"] for c in r["missed"]] == []
    assert r["next_term"] == "2-1"
    assert [c["course_id"] for c in r["next_courses"]] == ["B"]


def test_readiness_marks_missed_courses():
    r = cur.readiness(_plan(), {"A"}, current="3-1")
    assert [c["course_id"] for c in r["missed"]] == ["B"]          # 2-1에 열렸지만 안 들음
    assert r["next_term"] == "3-2"
    assert {c["course_id"] for c in r["next_courses"]} == {"C", "D"}


def test_readiness_without_current_term():
    r = cur.readiness(_plan(), set())
    assert r["missed"] == [] and r["next_term"] == "1-1"


def test_readiness_all_done():
    r = cur.readiness(_plan(), {"A", "B", "C", "D"}, current="4-1")
    assert r["ratio"] == 1.0 and r["left"] == [] and r["next_term"] is None


def test_readiness_flags_unverified():
    assert cur.readiness(_plan(), set())["all_verified"] is False


# ---------------- 진로 로드맵 ----------------

ROWS = [
    {"career": "X", "cert": "늦은핵심", "role": "핵심", "rationale": "", "verified": "TRUE", "source": ""},
    {"career": "X", "cert": "이른함께", "role": "함께", "rationale": "", "verified": "FALSE", "source": ""},
    {"career": "X", "cert": "이른핵심", "role": "핵심", "rationale": "", "verified": "FALSE", "source": ""},
    {"career": "Y", "cert": "다른진로", "role": "핵심", "rationale": "", "verified": "TRUE", "source": ""},
]
PLANS = {
    "늦은핵심": [{"term": "4"}],
    "이른함께": [{"term": "3-1"}],
    "이른핵심": [{"term": "3-1"}],
    "다른진로": [{"term": "1-1"}],
}


def test_roadmap_orders_by_ready_term_then_role():
    order = [x["cert"] for x in cur.roadmap("X", rows=ROWS, plans=PLANS)]
    assert order == ["이른핵심", "이른함께", "늦은핵심"]


def test_roadmap_keeps_verified_flag():
    items = {x["cert"]: x for x in cur.roadmap("X", rows=ROWS, plans=PLANS)}
    assert items["늦은핵심"]["verified"] is True
    assert items["이른함께"]["verified"] is False


def test_roadmap_cert_without_plan_goes_last():
    rows = ROWS + [{"career": "X", "cert": "과목없음", "role": "핵심", "rationale": "", "verified": "TRUE", "source": ""}]
    order = [x["cert"] for x in cur.roadmap("X", rows=rows, plans=PLANS)]
    assert order[-1] == "과목없음"


def test_careers_from_data():
    assert cur.careers() == ["백엔드 · SW 개발", "데이터 · DB", "서버 · 클라우드", "네트워크 · 인프라"]


def test_taken_shared_state():
    cur.taken().add("CS101")
    assert "CS101" in cur.taken()
