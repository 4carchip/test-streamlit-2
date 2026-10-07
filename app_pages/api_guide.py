"""멀티AI API 연결 안내 + 연결 테스트."""

import streamlit as st

from core import llm
from core.config import is_demo_mode, load_models, multiai_settings

PLATFORM_URL = "https://ai.hknu.ac.kr"

SECRETS_EXAMPLE = '''# .streamlit/secrets.toml  ← 이 파일은 절대 GitHub에 올리지 마세요 (.gitignore에 포함됨)

[multiai]
base_url = "https://여기에_플랫폼_API_주소/v1"   # 예시 코드에 나오는 주소
api_key  = "여기에_발급받은_API_키"
credit_limit = 0          # 사이드바 예산 막대 (선택, 0이면 숨김)
cost_unit = "크레딧"       # 비용 단위 표시 (선택)

# 사이드바 '설명 모델' 목록. id는 플랫폼에 표시된 모델 ID를 그대로 적습니다.
[[models]]
label = "Claude Opus"
id = "플랫폼에_표시된_Claude_Opus_모델ID"
input_price = 0           # 100만 토큰당 비용 (선택, 알면 사용량에 비용 추정 표시)
output_price = 0

[[models]]
label = "GPT-6 Astra"
id = "플랫폼에_표시된_GPT-6_Astra_모델ID"

# 모델마다 주소나 키가 다르면 해당 [[models]] 아래에 base_url / api_key를 따로 적으면 됩니다.
'''

st.title("🔑 멀티AI API 연결 안내")
st.markdown(
    "ClassMate는 학교 **멀티AI 플랫폼**의 크레딧으로 AI를 호출합니다. "
    "연결에 필요한 값은 **주소(base_url) · API 키 · 모델 ID** 세 가지이고, "
    "코드에 적지 않고 `secrets`에만 넣습니다."
)

# ---------------- 현재 상태 ----------------
st.subheader("현재 연결 상태")
s = multiai_settings()
models = load_models()
if is_demo_mode():
    st.warning("API 설정이 없어 **데모 모드**로 동작 중입니다. 아래 단계를 따라 설정하세요.")
key = str(s.get("api_key", ""))
st.table({
    "항목": ["API 주소 (base_url)", "API 키", "등록된 모델"],
    "상태": [
        s.get("base_url") or "❌ 미설정",
        (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("✅ 설정됨" if key else "❌ 미설정"),
        ", ".join(f"{m.label} ({m.id})" for m in models),
    ],
})

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

st.subheader("3. secrets 설정하기")
tab_local, tab_cloud = st.tabs(["내 컴퓨터에서 실행할 때", "Streamlit Cloud에 배포할 때"])
with tab_local:
    st.markdown("프로젝트 폴더에 `.streamlit/secrets.toml` 파일을 만들고 아래 내용을 채웁니다. (`secrets.toml.example`을 복사해서 시작하세요)")
    st.code(SECRETS_EXAMPLE, language="toml")
with tab_cloud:
    st.markdown(
        "1. [share.streamlit.io](https://share.streamlit.io)에서 이 앱의 **⋮ → Settings → Secrets**를 엽니다.\n"
        "2. 왼쪽 탭의 내용을 그대로 붙여 넣고 **Save**를 누릅니다.\n"
        "3. 앱이 자동으로 다시 시작되면 사이드바의 '데모 모드' 경고가 사라집니다."
    )

st.subheader("4. 연결 테스트")
if is_demo_mode():
    st.caption("지금은 데모 모델로 테스트됩니다. 설정 후에는 실제 모델로 짧은 요청을 보냅니다.")
labels = [m.label for m in models]
pick = st.selectbox("테스트할 모델", labels, key="ping_model")
if st.button("연결 테스트 실행", type="primary"):
    model = next(m for m in models if m.label == pick)
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
