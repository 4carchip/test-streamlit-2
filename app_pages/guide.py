"""선수 과목 가이드: 자격증 시험 범위를 학과 과목과 연결하고, 이수 체크로 준비도와 다음 과목을 보여 준다."""

import streamlit as st

from core import curriculum as cur

st.title("🗺️ 선수 과목 가이드")
st.caption("자격증을 고르고 들은 과목을 체크하면, 준비도와 다음에 들을 과목을 보여 줍니다. "
           "과목 연결은 AI가 만들지 않고 학과 교육과정 데이터에서 가져옵니다.")

cert_list = cur.certs()
if not cert_list:
    st.error("data/curriculum/cert_courses.csv를 찾을 수 없습니다.")
    st.stop()

# ---------------- 선택 ----------------
# 위젯 값은 페이지를 옮기면 지워지므로, 위젯과 별도로 보관한 값(_guide_cert, _current_term)으로 매번 다시 채운다.
def _keep(name: str) -> None:
    st.session_state["_" + name] = st.session_state[name]


c1, c2 = st.columns([3, 2])
saved_cert = st.session_state.get("_guide_cert")
st.session_state["guide_cert"] = saved_cert if saved_cert in cert_list else cert_list[0]
cert = c1.selectbox("자격증", cert_list, key="guide_cert", on_change=_keep, args=("guide_cert",))
st.session_state["current_term"] = st.session_state.get("_current_term", "3-2")
current = c2.selectbox("지금 학기", cur.TERMS, key="current_term", format_func=cur.term_label,
                       on_change=_keep, args=("current_term",),
                       help="이 학기보다 앞에 개설됐는데 안 들은 과목은 따로 공부해야 할 범위로 표시합니다.")

plan = cur.cert_plan(cert)
taken = cur.taken()


def _toggle(cid: str) -> None:
    (taken.add if st.session_state[f"taken-{cid}"] else taken.discard)(cid)


# ---------------- 이수 체크 ----------------
st.subheader("들은 과목 체크")
st.caption("체크는 모든 자격증에 같이 반영됩니다. (같은 과목이 여러 자격증에 걸쳐 있으면 한 번만 체크)")
by_term: dict[str, list[dict]] = {}
for c in plan:
    by_term.setdefault(c["term"], []).append(c)
cols = st.columns(len(by_term) or 1)
for col, (term, items) in zip(cols, by_term.items()):
    col.markdown(f"**{cur.term_label(term)}**")
    for c in items:
        key = f"taken-{c['course_id']}"
        st.session_state[key] = c["course_id"] in taken     # 페이지를 옮겨도 체크가 유지되게 매번 다시 채운다
        label = c["course"] + (f" ({c['note']})" if c["note"] else "")
        col.checkbox(label, key=key, on_change=_toggle, args=(c["course_id"],))

# ---------------- 결과 ----------------
r = cur.readiness(plan, taken, current)
st.divider()
m1, m2, m3 = st.columns(3)
m1.metric("준비도", f"{round(r['ratio'] * 100)}%", help=f"{len(plan)}과목 중 {len(r['done'])}과목 이수")
m2.metric("모든 과목을 듣는 시기", cur.term_label(cur.ready_term(plan)) if plan else "-")
m3.metric("남은 과목", f"{len(r['left'])}개")
st.progress(r["ratio"])

if not r["left"]:
    st.success(f"{cert} 시험 범위와 연결된 학과 과목을 모두 들었습니다. 이제 '이해도 확인'으로 실전 감각을 점검해 보세요.")
    st.page_link("app_pages/quiz.py", label="이해도 확인으로 가기", icon="📝")
else:
    if r["missed"]:
        names = ", ".join(f"{c['course']}({cur.term_label(c['term'])})" for c in r["missed"])
        st.warning(f"**지난 학기에 열렸지만 아직 안 들은 과목:** {names}  \n"
                   "수업으로 다시 듣기 어렵다면 해당 범위를 교재로 따로 공부하세요.")
    if r["next_courses"]:
        names = ", ".join(c["course"] for c in r["next_courses"])
        st.info(f"**다음에 들을 과목 ({cur.term_label(r['next_term'])}):** {names}")
    later = [c for c in r["upcoming"] if c not in r["next_courses"]]
    if later:
        st.caption("그 뒤: " + " → ".join(f"{c['course']}({c['term']})" for c in later))
    st.markdown("아직 듣지 않은 과목의 범위는 **공부하기**에서 교재 PDF를 올려 먼저 공부할 수 있습니다.")
    st.page_link("app_pages/study.py", label="공부하기로 가기", icon="📖")

# ---------------- 출처 ----------------
with st.expander("데이터 출처와 검토 상태"):
    st.markdown(
        "- 과목과 개설 학기: 2026학년도 학과 교육과정 (1·2학기)\n"
        "- 자격증 ↔ 과목 연결: 출제기준과 교육과정을 대조한 팀 초안 (계획서 2.2)\n"
        "- 수정할 때는 `data/curriculum/`의 CSV만 고치면 화면에 바로 반영됩니다."
    )
    if not r["all_verified"]:
        st.caption("⚠️ 이 자격증의 과목 연결은 아직 교수님 검토 전(verified=FALSE)입니다.")
