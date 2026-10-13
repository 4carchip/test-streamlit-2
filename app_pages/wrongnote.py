"""스마트 오답노트: 오늘 복습 · 전체 오답(원인 확정·유사 유형) · 약점 분석 · 백업."""

import pandas as pd
import streamlit as st

from core import llm, mapping, wrongnote as wn
from core.sidebar import selected_models

st.title("📒 오답노트")
st.caption("틀린 문제는 자동으로 모입니다. 틀린 이유를 확정하고, 정해진 날짜에 다시 풀고, 같은 유형 문제로 약점을 메웁니다.")

notes = wn.notes()
due_today = wn.due_items()
c1, c2, c3, c4 = st.columns(4)
c1.metric("모은 오답", f"{len(notes)}개")
c2.metric("오늘 복습", f"{len(due_today)}개")
c3.metric("익힘", f"{sum(n['mastered'] for n in notes)}개")
c4.metric("문제 은행", f"{len(wn.bank())}개")

if not notes:
    st.info("아직 오답이 없습니다. '이해도 확인'에서 문제를 풀면 틀린 문제가 여기에 쌓입니다.")
    st.page_link("app_pages/quiz.py", label="이해도 확인으로 가기", icon="📝")


def solve_form(q: dict, key: str) -> None:
    """문제 하나를 풀고 채점한다. 틀리면 오답노트에 넣는다."""
    with st.form(key):
        st.markdown(f"**{q['question']}**")
        pick = st.radio("보기", [1, 2, 3, 4], index=None, key=f"{key}-pick",
                        format_func=lambda j: f"{j}. {q['choices'][j-1]}", label_visibility="collapsed")
        done = st.form_submit_button("채점")
    if done:
        if pick is None:
            st.warning("보기를 골라 주세요.")
            return
        st.session_state.setdefault("bank_solved", set()).add(q["id"])
        if pick == q["answer"]:
            st.success(f"정답입니다. {q.get('explanation', '')} (근거 p.{q.get('page', '?')})")
        else:
            main, _ = selected_models()
            wn.record_quiz([q], [pick], q.get("doc", ""), main)
            st.error(f"오답입니다. 정답은 {q['answer']}번. {q.get('explanation', '')} — 오답노트에 추가했어요.")


tab_today, tab_all, tab_stat, tab_backup = st.tabs(["오늘 복습", "전체 오답", "약점 분석", "백업"])

# ---------------- 오늘 복습 (간격 반복) ----------------
with tab_today:
    st.caption("복습 간격: 1일 → 3일 → 7일 → 14일 → 30일. 맞히면 다음 칸으로, 틀리면 1일 칸으로 돌아갑니다.")
    show_all = st.toggle("날짜와 상관없이 모두 복습 (시연·몰아보기용)", key="review_all")
    queue = wn.due_items(include_future=show_all)
    if msg := st.session_state.pop("review_msg", None):
        (st.success if msg[0] else st.error)(msg[1])
    if not queue:
        upcoming = sorted(n["due"] for n in notes if not n["mastered"])
        st.info(f"오늘 복습할 문제가 없어요.{' 다음 복습일: ' + upcoming[0] if upcoming else ''}")
    else:
        n = queue[0]
        st.markdown(f"남은 복습 {len(queue)}개 · 상자 **{n['box'] + 1}/5** · 개념 **{n['concept'] or '-'}** · 원인 **{n.get('cause') or '미분류'}**")
        with st.form(f"rv-{n['id']}"):
            st.markdown(f"**{n['question']}**")
            pick = st.radio("보기", [1, 2, 3, 4], index=None, key=f"rv-pick-{n['id']}-{len(n['history'])}",
                            format_func=lambda j: f"{j}. {n['choices'][j-1]}", label_visibility="collapsed")
            ok = st.form_submit_button("확인", type="primary")
        if ok and pick is not None:
            correct = pick == n["answer"]
            note = wn.review(n, correct)
            exp = f" 해설: {n['explanation']} (근거 p.{n.get('page', '?')})" if not correct else ""
            st.session_state["review_msg"] = (correct, note + exp)
            st.rerun()
        elif ok:
            st.warning("보기를 골라 주세요.")

# ---------------- 전체 오답 ----------------
with tab_all:
    main, verifier = selected_models()
    for n in sorted(notes, key=lambda x: (x["mastered"], x["due"])):
        state_txt = "익힘" if n["mastered"] else f"상자 {n['box'] + 1}/5 · 다음 복습 {n['due']}"
        with st.expander(f"{'✅' if n['mastered'] else '📌'} {n['question'][:40]} · {n.get('cause') or '원인 미분류'} · {state_txt}"):
            mine = f"{n['my_answer']}. {n['choices'][n['my_answer']-1]}" if n.get("my_answer") else "선택 안 함"
            st.markdown(f"**{n['question']}**  \n내 답: {mine}  \n정답: **{n['answer']}. {n['choices'][n['answer']-1]}**")
            st.caption(f"해설: {n['explanation']} · 근거 {n['doc']} p.{n.get('page', '?')} · 개념: {n['concept'] or '-'}")
            for r in mapping.find(n["concept"] + " " + n["question"])["confirmed"]:
                st.caption(f"📌 {r['concept']}: {r['exam']} {r['subject']}{r['phrase']}")

            # 오인 원인: AI 추정 → 학생 확정
            st.markdown("**틀린 이유**")
            if n.get("suggested"):
                st.caption(f"AI 추정: {n['suggested']} — {n.get('reason', '')}")
            cur = n.get("cause") or n.get("suggested") or wn.CAUSES[0]
            choice = st.radio("원인", wn.CAUSES, index=wn.CAUSES.index(cur), key=f"cause-{n['id']}",
                              horizontal=True, label_visibility="collapsed")
            st.caption(wn.CAUSE_HELP[choice])
            if choice != n.get("cause") or not n.get("confirmed"):
                if st.button("이 원인으로 확정", key=f"confirm-{n['id']}"):
                    n["cause"], n["confirmed"] = choice, True
                    st.rerun()
            else:
                st.caption("✔ 확정됨")

            # 유사 유형 문제
            st.markdown("**유사 유형 문제**")
            recs = wn.recommend(n)
            if not recs:
                if st.button("같은 개념·같은 원인 문제 만들기", key=f"sim-{n['id']}"):
                    try:
                        with st.spinner("교재에서 근거를 찾아 문제를 만드는 중..."):
                            kept, dropped = wn.make_similar(n, main, verifier)
                        note = f" (검증 모델과 정답이 달라 {dropped}개는 뺐어요)" if dropped else ""
                        st.session_state[f"sim-msg-{n['id']}"] = f"{len(kept)}문제를 문제 은행에 넣었어요.{note}"
                        st.rerun()
                    except (ValueError, llm.LLMError) as e:
                        st.warning(str(e))
            if m := st.session_state.pop(f"sim-msg-{n['id']}", None):
                st.caption(m)
            for i, q in enumerate(recs):
                q.setdefault("doc", n["doc"])
                solve_form(q, f"bank-{n['id']}-{q['id']}")

# ---------------- 약점 분석 ----------------
with tab_stat:
    if not notes:
        st.caption("오답이 쌓이면 약점이 보입니다.")
    else:
        s = wn.stats()
        left, right = st.columns(2)
        with left:
            st.markdown("**틀린 이유별**")
            st.bar_chart(pd.DataFrame({"문제 수": s["cause"]}), horizontal=True, color="#6F7590")
        with right:
            st.markdown("**개념별 (많이 틀린 순)**")
            top = dict(list(s["concept"].items())[:6])
            st.bar_chart(pd.DataFrame({"문제 수": top}), horizontal=True, color="#6F7590")
        worst = max(s["cause"], key=s["cause"].get)
        if s["cause"][worst]:
            st.info(f"가장 많은 원인은 **{worst}**입니다. {wn.CAUSE_HELP[worst]}")

# ---------------- 백업 ----------------
with tab_backup:
    st.caption("오답노트는 이 브라우저 세션에만 있습니다. 공부를 마칠 때 내려받고, 다음에 다시 올리면 이어서 복습할 수 있어요.")
    st.download_button("오답노트 내려받기 (JSON)", wn.export_json(), file_name="certmate_오답노트.json",
                       mime="application/json", disabled=not notes)
    up = st.file_uploader("오답노트 불러오기", type=["json"], key="wn-import")
    if up and st.button("불러오기"):
        try:
            k = wn.import_json(up.getvalue().decode("utf-8"))
            st.success(f"{k}개를 불러왔어요.")
            st.rerun()
        except Exception as e:
            st.error(f"파일을 읽지 못했어요: {e}")
