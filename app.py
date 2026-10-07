"""ClassMate — 근거 기반 학습 보조 AI (Streamlit 진입점).

실행: streamlit run app.py
"""

import streamlit as st

st.set_page_config(page_title="ClassMate", page_icon="📚", layout="wide")

from core import sidebar  # noqa: E402  (set_page_config가 가장 먼저 와야 함)

pages = [
    st.Page("app_pages/study.py", title="공부하기", icon="📖", default=True),
    st.Page("app_pages/quiz.py", title="이해도 확인", icon="📝"),
    st.Page("app_pages/api_guide.py", title="멀티AI API 연결 안내", icon="🔑"),
]
pg = st.navigation(pages)
sidebar.render_controls()   # 모델 선택·교차검증 (모든 페이지 공통)
pg.run()
sidebar.render_usage()      # 페이지에서 호출한 사용량까지 반영해서 표시
