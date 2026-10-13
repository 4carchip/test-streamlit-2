"""이해도 확인: 공부한 자료로 문제 생성 → 채점 → 오답노트, 그리고 외부 기출 사이트 안내."""

import random

import streamlit as st

from core import llm, mapping, quizgen, state, wrongnote
from core.sidebar import selected_models

# 외부 기출 사이트: 문제를 가져오지 않고 '링크로 이동'만 한다. (사유는 화면 하단 안내 참고)
EXTERNAL_LINKS = [
    ("정보처리기사 필기 기출 (전자문제집 CBT)", "https://www.comcbt.com/xe/iz"),
    ("전자문제집 CBT 메인 (ADsP 등 검색)", "https://www.comcbt.com/xe/"),
    ("ADsP 공식 시험 안내 (데이터자격검정)", "https://www.dataq.or.kr/www/sub/a_06.do"),
    ("국가기술자격 출제기준 (큐넷)", "https://www.q-net.or.kr"),
]

st.title("📝 이해도 확인")
st.caption("공부한 자료에서 문제를 만들어 풀어 봅니다. 마지막으로 실제 기출 사이트에서 실전 감각을 확인하세요.")

docs = state.docs()
if not docs:
    st.info("먼저 '공부하기' 페이지에서 PDF를 올려 주세요.")
else:
    # ---------------- 출제 범위 ----------------
    with st.form("quiz_setup"):
        keys = list(docs.keys())
        key = st.selectbox("자료", keys, format_func=lambda k: docs[k].name)
        doc = docs[key]
        c1, c2, c3 = st.columns(3)
        pages = c1.slider("쪽 범위", 1, max(doc.pages, 1), (1, min(doc.pages, 20))) if doc.pages > 1 else (1, 1)
        n = c2.number_input("문항 수", 3, 10, 5)
        level = c3.selectbox("난이도", ["기본 개념 확인", "시험 수준", "응용·비교"])
        topic = st.text_input("집중할 주제 (선택)", placeholder="예: 데이터웨어하우스, OLAP")
        go = st.form_submit_button("문제 만들기", type="primary")

    if go:
        chunks = [c for c in doc.chunks if pages[0] <= c.page <= pages[1] and len(c.text) > 50]
        if topic and state.index():
            hits = [c for _, c in state.index().search(topic, 8) if c.doc == doc.name]
            chunks = hits or chunks
        random.shuffle(chunks)
        chunks = sorted(chunks[:8], key=lambda c: c.page)   # 비용을 줄이려고 최대 8조각만 사용

        if not chunks:
            st.warning("선택한 범위에서 텍스트를 찾지 못했습니다. 범위를 넓혀 보세요.")
        else:
            main_model, verifier = selected_models()
            try:
                with st.spinner(f"{main_model.label}이(가) 문제를 만들고{' ' + verifier.label + '이(가) 정답을 검증하는' if verifier else ''} 중..."):
                    qs = quizgen.generate(chunks, n, level, main_model, verifier)
                for k in [k for k in st.session_state if str(k).startswith("q") and str(k)[1:].isdigit()]:
                    del st.session_state[k]   # 이전 문제의 선택값 초기화
                st.session_state["quiz"] = {"doc": doc.name, "questions": qs, "graded": False}
                if not qs:
                    st.error("문제를 만들지 못했습니다. 다시 시도해 주세요.")
            except llm.LLMError as e:
                st.error(f"AI 호출에 실패했습니다. {e}")
            except (ValueError, TypeError) as e:
                st.error(f"문제 형식을 해석하지 못했습니다: {e}")

    # ---------------- 풀이·채점 ----------------
    quiz = st.session_state.get("quiz")
    if quiz and quiz["questions"]:
        st.subheader(f"문제 — {quiz['doc']}")
        with st.form("quiz_answers"):
            picks = []
            for i, q in enumerate(quiz["questions"]):
                flag = "  ⚠️ _정답 검토 필요 (검증 모델과 의견이 다름)_" if q.get("check") == "review" else ""
                st.markdown(f"**Q{i+1}. {q['question']}**{flag}")
                picks.append(st.radio("보기", [1, 2, 3, 4], index=None, key=f"q{i}",
                                      format_func=lambda j, q=q: f"{j}. {q['choices'][j-1]}",
                                      label_visibility="collapsed"))
            submitted = st.form_submit_button("채점하기", type="primary")

        if submitted:
            quiz["graded"] = True
            quiz["picks"] = picks
            main_model, _ = selected_models()
            with st.spinner("틀린 이유를 분석해 오답노트에 넣는 중..."):
                try:
                    quiz["added"] = wrongnote.record_quiz(quiz["questions"], picks, quiz["doc"], main_model)
                except llm.LLMError as e:
                    quiz["added"] = 0
                    st.warning(f"오답노트 저장 중 AI 호출에 실패했습니다. {e}")

        if quiz.get("graded"):
            picks = quiz["picks"]
            score = sum(1 for q, p in zip(quiz["questions"], picks) if p == q["answer"])
            st.metric("점수", f"{score} / {len(picks)}")
            if quiz.get("added"):
                st.info(f"틀린 {quiz['added']}문제를 오답노트에 넣었어요. 틀린 이유를 확인하고, 내일 첫 복습을 합니다.")
                st.page_link("app_pages/wrongnote.py", label="오답노트에서 확인하기", icon="📒")
            for i, (q, p) in enumerate(zip(quiz["questions"], picks)):
                ok = p == q["answer"]
                with st.expander(f"{'✅' if ok else '❌'} Q{i+1}. {q['question']}", expanded=not ok):
                    st.markdown(f"정답: **{q['answer']}. {q['choices'][q['answer']-1]}**  \n내 답: {p if p else '선택 안 함'}")
                    st.markdown(f"해설: {q.get('explanation', '')}  \n근거: p.{q.get('page', '?')}")
                    mp = mapping.find(q.get("concept", "") + " " + q["question"])
                    for r in mp["confirmed"]:
                        st.caption(f"📌 {r['concept']}: {r['exam']} {r['subject']}{r['phrase']}")

# ---------------- 외부 기출 사이트 ----------------
st.divider()
st.subheader("🎯 실전 기출로 확인하기")
st.markdown(
    "앱에서 만든 문제로 개념을 확인했다면, 실제 기출 사이트에서 실전 감각을 점검해 보세요. "
    "아래 버튼은 **해당 사이트로 이동만** 합니다."
)
cols = st.columns(2)
for i, (label, url) in enumerate(EXTERNAL_LINKS):
    cols[i % 2].link_button(label, url, use_container_width=True)
with st.expander("왜 기출문제를 앱으로 가져오지 않나요?"):
    st.markdown(
        "- 기출문제와 해설은 출제 기관과 해당 사이트에 권리가 있어, 자동으로 수집(크롤링)해 앱에 다시 보여 주면 "
        "저작권·이용약관 문제가 생길 수 있습니다.\n"
        "- 그래서 CertMate는 **본인 자료로 새 문제를 만들고**, 기출은 **원래 사이트로 연결**하는 방식을 씁니다.\n"
        "- 사이트 운영자의 허락이나 공식 API가 생기면 그때 연동을 검토합니다."
    )
