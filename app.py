import streamlit as st

st.title("🚀 Streamlit Cloud 테스트")

name = st.text_input("이름을 입력하세요:")
if name:
    st.write(f"안녕하세요, {name}님!")
else:
    st.write("이름을 입력해 주세요.")
