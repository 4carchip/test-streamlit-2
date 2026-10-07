"""공부하기: PDF 업로드 → 근거 기반 질의응답 → (선택) 교차검증 → 교차 출제 안내."""

import streamlit as st

from core import crosscheck, llm, mapping, state
from core.documents import format_context
from core.sidebar import selected_models

MIN_SCORE = 1.0   # 검색 점수가 이보다 낮으면 '자료에 없음'으로 처리
TOP_K = 5

SYSTEM = """너는 대학생의 자격증·전공 공부를 돕는 학습 조교 'ClassMate'다.
규칙:
1. 반드시 [근거]에 있는 내용으로만 답한다. 근거에 없으면 "업로드한 자료에서 찾을 수 없습니다"라고 말하고 추측하지 않는다.
2. 문장마다 근거 위치를 [자료명 p.쪽] 형식으로 붙인다.
3. 다음 순서로 쓴다: **핵심 설명** → **부연 설명**(쉬운 예시, 헷갈리는 개념과 비교) → **시험 포인트**.
4. 다른 자격증 시험에 나오는지 여부는 말하지 않는다. (앱이 공식 출제기준 데이터로 따로 안내한다)
5. 한국어로, 대학생이 이해하기 쉽게 쓴다."""

st.title("📖 공부하기")
st.caption("교재 PDF를 올리고 질문하세요. 모든 답변은 올린 자료에 근거하며, 근거 쪽 번호가 함께 표시됩니다.")

# ---------------- 자료 업로드 ----------------
with st.expander("📂 학습 자료 (PDF)", expanded=not state.docs()):
    files = st.file_uploader(
        "본인이 구매·소유한 교재나 강의자료 PDF를 올려 주세요.",
        type=["pdf"],
        accept_multiple_files=True,
        help="자료는 이 브라우저 세션에서만 쓰이고 서버나 레포에 저장되지 않습니다.",
    )
    if files:
        with st.spinner("PDF에서 텍스트를 읽는 중..."):
            added = state.add_uploads(files)
        if added:
            st.success(f"추가됨: {', '.join(added)}")
    for key, doc in list(state.docs().items()):
        c1, c2 = st.columns([5, 1])
        empty = sum(1 for c in doc.chunks if len(c.text) < 30)
        note = f" · 텍스트가 거의 없는 조각 {empty}개 (스캔본일 수 있음)" if empty else ""
        c1.markdown(f"**{doc.name}** — {doc.pages}쪽, 검색 조각 {len(doc.chunks)}개{note}")
        if c2.button("삭제", key=f"del-{key}"):
            state.remove(key)
            st.rerun()


# ---------------- 메시지 렌더링 ----------------
def render_extras(meta: dict) -> None:
    check = meta.get("check")
    if check:
        st.markdown(f"**{check['overall']} 교차검증** — {check['summary']} _(검증: {check.get('verifier', '')})_")
        if check.get("claims"):
            with st.expander("주장별 판정 보기"):
                for c in check["claims"]:
                    icon, label = crosscheck.VERDICT_LABEL.get(str(c.get("verdict", "")).upper(), ("⚠️", "판정 불가"))
                    page = f" (p.{c['page']})" if c.get("page") else ""
                    st.markdown(f"{icon} **{label}** — {c.get('claim', '')}  \n<small>{c.get('reason', '')}{page}</small>", unsafe_allow_html=True)

    mp = meta.get("mapping") or {}
    if mp.get("confirmed"):
        st.info(
            "📌 **교차 출제 안내** (공식 출제기준 기준)\n\n"
            + "\n".join(f"- **{r['concept']}**: {r['exam']} {r['subject']}{r['phrase']} — _{r['topic']}_" for r in mp["confirmed"])
        )
    if mp.get("pending"):
        with st.expander(f"🔎 검토 중인 교차 출제 후보 {len(mp['pending'])}건 (아직 확인되지 않음)"):
            st.caption("팀원이 출제기준 원문과 대조하기 전인 후보입니다. 시험 범위로 단정하지 마세요.")
            for r in mp["pending"]:
                st.markdown(f"- {r['concept']} → {r['exam']} {r['subject']} ({r['relation']}): {r['rationale']}")

    sources = meta.get("sources") or []
    if sources:
        with st.expander(f"📄 근거 {len(sources)}개"):
            for s in sources:
                st.markdown(f"**{s['doc']} p.{s['page']}**")
                st.caption(s["text"][:400] + ("…" if len(s["text"]) > 400 else ""))


messages = st.session_state.setdefault("chat", [])
for m in messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_extras(m.get("meta", {}))

# ---------------- 질문 처리 ----------------
# chat_input은 호출 위치와 상관없이 화면 맨 아래에 고정된다.
question = st.chat_input("공부하다 궁금한 것을 물어보세요", disabled=not state.docs())
if not messages and not question:
    st.info("예시 질문: \"데이터웨어하우스와 데이터마트의 차이가 뭐야?\", \"DIKW 피라미드를 예시로 설명해줘\"")
if not state.docs():
    st.caption("PDF를 먼저 올리면 질문할 수 있습니다.")

if question:
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    main_model, verifier = selected_models()
    with st.chat_message("assistant"):
        idx = state.index()
        hits = [(s, c) for s, c in (idx.search(question, TOP_K) if idx else []) if s >= MIN_SCORE]
        meta: dict = {"sources": [c.as_dict() for _, c in hits]}

        if not hits:
            answer = "업로드한 자료에서 관련 내용을 찾지 못했습니다. 다른 표현으로 물어보거나, 해당 내용이 있는 자료를 올려 주세요."
        else:
            context = format_context([c for _, c in hits])
            history = [{"role": m["role"], "content": m["content"]} for m in messages[-7:-1]]
            prompt = [{"role": "system", "content": SYSTEM}, *history,
                      {"role": "user", "content": f"[근거]\n{context}\n\n[질문]\n{question}"}]
            try:
                with st.spinner(f"{main_model.label}이(가) 자료를 읽고 설명하는 중..."):
                    answer = llm.chat(main_model, prompt, purpose="answer",
                                      demo_hint={"chunks": meta["sources"]})
                if verifier:
                    with st.spinner(f"{verifier.label}이(가) 근거와 대조하는 중..."):
                        meta["check"] = crosscheck.verify(verifier, context, answer)
            except llm.LLMError as e:
                answer = f"⚠️ AI 호출에 실패했습니다.\n\n{e}"

            # 교차 출제 안내는 AI가 아니라 출제기준 데이터를 검색해서 만든다.
            # (근거 문단 전체가 아니라 질문·답변에 나온 개념만 대상으로 한다)
            meta["mapping"] = mapping.find(question + "\n" + answer)

        st.markdown(answer)
        render_extras(meta)
    messages.append({"role": "assistant", "content": answer, "meta": meta})

if messages:
    if st.button("대화 지우기"):
        st.session_state["chat"] = []
        st.rerun()
