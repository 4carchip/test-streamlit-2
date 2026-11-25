"""진로 로드맵: 진로를 고르면 핵심 · 함께 준비할 자격증과 취득 순서를 보여 준다."""

import streamlit as st

from core import curriculum as cur

st.title("🧭 진로 로드맵")
st.caption("진로를 고르면 핵심 자격증, 함께 준비하면 좋은 자격증, 취득 순서를 보여 줍니다. "
           "취득 순서는 각 자격증의 선수 과목을 모두 들을 수 있는 학기 순으로 계산합니다.")

career_list = cur.careers()
if not career_list:
    st.error("data/curriculum/career_certs.csv를 찾을 수 없습니다.")
    st.stop()

career = st.radio("진로", career_list, horizontal=True, key="roadmap_career")
items = cur.roadmap(career)
taken = cur.taken()

if any(not x["verified"] for x in items):
    st.warning("이 진로의 자격증 연결은 아직 **검토 전 후보**입니다. 팀 · 교수님 · 선배 검토를 거쳐 확정할 예정이며, "
               "확정 전에는 참고용으로만 보세요.")

for i, x in enumerate(items, 1):
    plan = cur.cert_plan(x["cert"])
    r = cur.readiness(plan, taken, st.session_state.get("_current_term"))
    with st.container(border=True):
        c1, c2, c3 = st.columns([4, 2, 2])
        badge = "핵심" if x["role"] == "핵심" else "함께 준비"
        status = "" if x["verified"] else " · 검토 전"
        c1.markdown(f"#### {i}. {x['cert']}")
        c1.caption(f"{badge}{status} — {x['rationale']}")
        c2.metric("선수 과목을 모두 듣는 시기", cur.term_label(x["ready_term"]) if x["ready_term"] else "-")
        c3.metric("준비도", f"{round(r['ratio'] * 100)}%")
        c3.caption(f"{len(plan)}과목 중 {len(r['done'])}과목 이수")
        if st.button("선수 과목 보기", key=f"go-{x['cert']}"):
            st.session_state["_guide_cert"] = x["cert"]
            st.switch_page("app_pages/guide.py")

st.caption("준비도는 '선수 과목 가이드'에서 체크한 과목으로 계산합니다.")
