"""멀티AI API 연결 안내 + 연결 테스트."""

import streamlit as st

from core import llm
from core.config import CATALOG, MODE_LABEL, VENDOR_NAME, current_mode, resolve

PLATFORM_URL = "https://ai.hknu.ac.kr"

SECRETS_EXAMPLE = '''# .streamlit/secrets.toml  ← 이 파일은 절대 GitHub에 올리지 마세요 (.gitignore에 포함됨)
# 사이드바에 키를 직접 입력해도 되지만, 배포한 앱에서 매번 입력하기 번거로우면 여기에 넣어 둡니다.

# ① 학교 멀티AI (주소 + 키 하나로 GPT·Claude 모두 사용)
[multiai]
base_url = "https://여기에_플랫폼_API_주소/v1"   # 예시 코드에 나오는 주소
api_key  = "여기에_발급받은_API_키"
credit_limit = 0          # 사이드바 예산 막대 (선택, 0이면 숨김)
cost_unit = "USD"         # 비용 단위 표시 (선택)

# ② 회사별 키로 직접 호출할 때 (선택)
# OPENAI_API_KEY = "sk-..."
# ANTHROPIC_API_KEY = "sk-ant-..."

# ③ 플랫폼 모델 ID가 공식 ID와 다를 때만 (선택)
# [model_ids]
# "GPT-6.1 Sol" = "플랫폼에_표시된_ID"
# "Claude Opus 5.5" = "플랫폼에_표시된_ID"
'''

st.title("🔑 멀티AI API 연결 안내")
st.markdown(
    "ClassMate는 **왼쪽 사이드바**에서 API 키를 넣고 모델을 고르면 바로 동작합니다. "
    "연결 방식은 두 가지입니다.\n"
    "- **학교 멀티AI:** 플랫폼 주소(base_url)와 키 하나로 GPT·Claude를 모두 호출\n"
    "- **개별 API 키:** GPT는 OpenAI 키, Claude는 Anthropic 키로 각 회사 API를 직접 호출"
)

# ---------------- 현재 상태 ----------------
st.subheader("현재 연결 상태")
st.markdown(f"연결 방식: **{MODE_LABEL[current_mode()]}**")
rows = {"모델": [], "회사": [], "모델 ID": [], "상태": []}
for label, vendor, *_ in CATALOG:
    m = resolve(label)
    rows["모델"].append(label)
    rows["회사"].append(VENDOR_NAME[vendor])
    rows["모델 ID"].append(m.id)
    rows["상태"].append("🟢 키 있음" if not m.is_demo else "⚪ 키 없음 (데모 응답)")
st.table(rows)

# ---------------- 단계별 안내 ----------------
st.subheader("1. 플랫폼에서 API 키 발급받기")
st.markdown(
    f"1. [{PLATFORM_URL}]({PLATFORM_URL})에 학교 계정으로 로그인합니다.\n"
    "2. 대시보드에서 **API 키 / 개발자 / API 사용** 같은 메뉴를 찾아 새 키를 발급합니다.\n"
    "3. 키는 발급 직후 한 번만 보이는 경우가 많으니 바로 안전한 곳에 복사해 둡니다."
)
st.caption("※ 플랫폼 화면의 정확한 메뉴 위치는 팀이 직접 확인한 뒤, 이 페이지에 스크린샷과 함께 보완하세요.")

st.subheader("2. 주소와 모델 ID 확인하기")
st.markdown(
    "플랫폼의 API 문서나 예시 코드에서 아래 두 가지를 찾습니다.\n"
    "- **주소(base_url):** 예시 코드에 나오는 요청 주소. 보통 `/v1`로 끝납니다.\n"
    "- **모델 ID:** 화면에 보이는 이름(예: Claude Opus)이 아니라 API에 넣는 정확한 문자열.\n\n"
    "예시 코드에 `from openai import OpenAI` 나 `/chat/completions` 가 보이면 **OpenAI 호환 방식**이라 이 앱을 그대로 쓸 수 있습니다."
)
with st.expander("OpenAI 호환 방식이 아니라면?"):
    st.markdown(
        "`core/llm.py`의 `_call()` 함수 하나만 플랫폼 형식에 맞게 고치면 됩니다. "
        "나머지 화면·사용량 기록·교차검증 코드는 그대로 동작합니다."
    )

st.subheader("3. 키 넣기")
st.markdown(
    "**가장 간단한 방법:** 왼쪽 사이드바 **🔐 API 설정**에서 연결 방식을 고르고 주소·키를 입력합니다. "
    "입력한 키는 이 브라우저 세션에만 있고, 새로고침하거나 창을 닫으면 사라집니다.\n\n"
    "**매번 입력하기 싫다면** secrets에 저장해 두세요. 사이드바 입력칸이 비어 있으면 secrets 값을 씁니다."
)
tab_local, tab_cloud = st.tabs(["secrets — 내 컴퓨터에서 실행할 때", "secrets — Streamlit Cloud에 배포할 때"])
with tab_local:
    st.markdown("프로젝트 폴더에 `.streamlit/secrets.toml` 파일을 만들고 필요한 부분만 채웁니다. (`secrets.toml.example`을 복사해서 시작하세요)")
    st.code(SECRETS_EXAMPLE, language="toml")
with tab_cloud:
    st.markdown(
        "1. [share.streamlit.io](https://share.streamlit.io)에서 이 앱의 **⋮ → Settings → Secrets**를 엽니다.\n"
        "2. 왼쪽 탭의 내용을 그대로 붙여 넣고 **Save**를 누릅니다.\n"
        "3. 앱이 자동으로 다시 시작되면 사이드바 모델 아래에 '🟢 연결 준비됨'이 표시됩니다.\n\n"
        "⚠️ 배포된 앱 주소를 다른 사람과 공유하면, secrets에 넣은 키의 크레딧을 그 사람도 쓰게 됩니다. "
        "공유할 앱이라면 secrets 대신 각자 사이드바에 키를 입력하게 하세요."
    )

st.subheader("4. 연결 테스트")
labels = [label for label, *_ in CATALOG]
pick = st.selectbox("테스트할 모델", labels, key="ping_model")
if resolve(pick).is_demo:
    st.caption("이 모델은 키가 없어 데모 응답으로 테스트됩니다. 사이드바에 키를 넣은 뒤 다시 눌러 보세요.")
if st.button("연결 테스트 실행", type="primary"):
    model = resolve(pick)
    with st.spinner("짧은 요청을 보내는 중..."):
        ok, msg, sec = llm.ping(model)
    if ok:
        st.success(f"연결 성공 ({sec:.1f}초) — 응답: {msg}")
    else:
        st.error(f"연결 실패 ({sec:.1f}초)\n\n{msg}")

st.subheader("자주 나는 오류")
st.table({
    "오류": ["401", "403", "404", "429", "시간 초과"],
    "의미": ["키가 틀림·만료", "모델 사용 권한 없음", "주소 또는 모델 ID 오타", "호출 한도·크레딧 초과", "네트워크 또는 서버 지연"],
    "해결": ["키 재발급 후 secrets 수정", "플랫폼에서 모델 권한 확인", "예시 코드의 값과 한 글자씩 비교", "대시보드에서 잔여 크레딧 확인", "잠시 후 재시도, 문항 수 줄이기"],
})

st.subheader("보안 수칙")
st.markdown(
    "- API 키를 **코드·레포·카톡·스크린샷**에 남기지 않습니다. `secrets.toml`은 `.gitignore`에 들어 있습니다.\n"
    "- 키가 노출되면 플랫폼에서 **즉시 폐기하고 재발급**합니다.\n"
    "- 크레딧은 계정 단위일 수 있으니, 시연·배포용 키를 누구 계정으로 쓸지 팀에서 정해 둡니다."
)
