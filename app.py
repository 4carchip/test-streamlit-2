"""
5차시 RAG QA 챗봇 Streamlit 앱
- 4차시 Streamlit 챗봇 구조(session_state, sidebar, persona, reset)를 확장
- RAG PDF 실습 흐름(Loader → Splitter → Storage → Retriever → Generator)을 웹앱으로 연결

실행:
  streamlit run apps/rag_chatbot_app.py
"""

import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple

import streamlit as st
from openai import OpenAI

from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


st.set_page_config(
    page_title="5차시 RAG QA 챗봇",
    page_icon="📚",
    layout="wide",
)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

PERSONAS = {
    "친절한 법률 문서 조교": """
너는 문서 기반 질의응답을 돕는 친절한 조교입니다.
답변은 한국어로 작성하고, 어려운 표현은 쉬운 말로 풀어 설명하세요.
제공된 문서 근거를 우선 사용하고, 법률 판단이 필요한 경우 전문가 확인을 안내하세요.
""".strip(),
    "엄격한 RAG 검증관": """
너는 문서 근거를 매우 엄격하게 확인하는 RAG 검증관입니다.
제공된 문서에 없는 내용은 추측하지 않습니다.
근거가 부족하면 '업로드된 문서에서 근거를 찾기 어렵습니다'라고 답하세요.
""".strip(),
    "요약 중심 문서봇": """
너는 긴 문서를 짧고 구조적으로 요약해주는 문서봇입니다.
답변은 핵심 결론 → 근거 → 주의사항 순서로 작성하세요.
""".strip(),
}

SENSITIVE_PATTERNS = {
    "api_key": r"sk-[A-Za-z0-9_\-]{10,}",
    "korean_rrn": r"\b\d{6}-\d{7}\b",
    "phone": r"\b01[016789]-?\d{3,4}-?\d{4}\b",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "password_hint": r"(?i)(password|passwd|비밀번호|암호)\s*[:=]",
}


def get_api_key_from_env_or_secrets() -> str:
    try:
        if "OPENAI_API_KEY" in st.secrets:
            return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    return os.getenv("OPENAI_API_KEY", "")


def detect_sensitive_info(text: str) -> List[str]:
    hits = []
    for name, pattern in SENSITIVE_PATTERNS.items():
        if re.search(pattern, text):
            hits.append(name)
    return hits


@st.cache_resource(show_spinner=False)
def get_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def load_documents_from_path(path: str) -> List[Document]:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return PyPDFLoader(path).load()
    if suffix in [".txt", ".md"]:
        return TextLoader(path, encoding="utf-8").load()
    raise ValueError("지원하지 않는 파일 형식입니다. PDF, TXT, MD만 사용하세요.")


@st.cache_resource(show_spinner="문서를 읽고 벡터 DB를 만드는 중입니다...")
def build_vectorstore_from_bytes(
    file_bytes: bytes,
    file_name: str,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
    api_key: str,
) -> Tuple[FAISS, str, int]:
    os.environ["OPENAI_API_KEY"] = api_key

    suffix = Path(file_name).suffix.lower() or ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        docs = load_documents_from_path(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    for doc in docs:
        doc.metadata["source"] = file_name

    raw_text = "\n\n".join(doc.page_content for doc in docs)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    splits = splitter.split_documents(docs)

    embeddings = OpenAIEmbeddings(model=embedding_model)
    vectorstore = FAISS.from_documents(splits, embeddings)
    return vectorstore, raw_text, len(splits)


def format_docs(docs: List[Document]) -> str:
    blocks = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", None)
        page_text = f", page={page + 1}" if isinstance(page, int) else ""
        blocks.append(
            f"[문서 {i}] source={source}{page_text}\n{doc.page_content}"
        )
    return "\n\n---\n\n".join(blocks)


def call_openai_text(
    client: OpenAI,
    model: str,
    instructions: str,
    input_text: str,
    max_output_tokens: int = 900,
    temperature: float | None = None,
) -> str:
    kwargs = {
        "model": model,
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature

    try:
        response = client.responses.create(**kwargs)
    except Exception as exc:
        # 일부 추론 모델은 temperature를 지원하지 않을 수 있어 한 번 재시도합니다.
        if "temperature" in str(exc).lower() and "temperature" in kwargs:
            kwargs.pop("temperature", None)
            response = client.responses.create(**kwargs)
        else:
            raise
    return response.output_text


def generate_rag_answer(
    client: OpenAI,
    model: str,
    query: str,
    docs: List[Document],
    persona: str,
    strict_mode: bool,
    temperature: float,
) -> str:
    context = format_docs(docs)
    strict_rule = (
        "문서에 직접 근거가 없으면 추측하지 말고 '업로드된 문서에서 근거를 찾기 어렵습니다'라고 답하세요."
        if strict_mode
        else "문서 근거를 우선 사용하되, 문서 밖 일반 상식이 필요하면 반드시 '문서 밖 일반 설명'이라고 표시하세요."
    )

    instructions = f"""
{persona}

[공통 규칙]
- 답변은 한국어로 작성합니다.
- 아래 제공된 문서 근거를 우선 사용합니다.
- {strict_rule}
- 답변 마지막에 '참고한 문서'를 문서 번호로 표시합니다.
- 법률, 의료, 금융 등 고위험 판단은 전문가 확인이 필요하다고 안내합니다.
""".strip()

    input_text = f"""
[사용자 질문]
{query}

[검색된 문서 근거]
{context}

[답변 형식]
1. 핵심 답변
2. 근거 요약
3. 주의사항
4. 참고한 문서
""".strip()

    return call_openai_text(
        client=client,
        model=model,
        instructions=instructions,
        input_text=input_text,
        max_output_tokens=1100,
        temperature=temperature,
    )


def generate_summary(client: OpenAI, model: str, raw_text: str, temperature: float = 0.0) -> str:
    clipped = raw_text[:14000]
    instructions = """
너는 문서 요약 전문가입니다. 한국어로 Notion 스타일 요약을 만듭니다.
중요 내용만 구조화하고, 문서에 없는 내용은 추가하지 않습니다.
""".strip()
    input_text = f"""
아래 문서를 요약하세요.

[출력 형식]
# 한 문장 요약
# 핵심 항목 5개
# 질문해볼 만한 내용 5개
# 주의사항

[문서]
{clipped}
""".strip()
    return call_openai_text(client, model, instructions, input_text, 1200, temperature)


def ensure_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "안녕하세요! 문서를 업로드하거나 샘플 문서를 선택한 뒤 질문해 주세요. '요약'이라고 입력하면 문서 요약을 생성합니다.",
            }
        ]
    if "vectorstore" not in st.session_state:
        st.session_state.vectorstore = None
    if "raw_text" not in st.session_state:
        st.session_state.raw_text = ""
    if "last_sources" not in st.session_state:
        st.session_state.last_sources = []


ensure_session_state()

st.markdown(
    """
<style>
.block-container {padding-top: 2rem;}
.small-caption {font-size: 0.85rem; color: #666;}
</style>
""",
    unsafe_allow_html=True,
)

st.title("📚 5차시 GPT API 기반 RAG QA 챗봇")
st.caption("Loader → Splitter → Storage → Retriever → Generator 흐름을 Streamlit으로 확인합니다.")

with st.sidebar:
    st.header("🔐 API 설정")
    api_key_input = st.text_input("OPENAI_API_KEY", type="password", help="비워두면 st.secrets 또는 환경변수를 사용합니다.")
    api_key = api_key_input or get_api_key_from_env_or_secrets()
    model = st.text_input("생성 모델", value=DEFAULT_MODEL)
    embedding_model = st.text_input("임베딩 모델", value=DEFAULT_EMBEDDING_MODEL)

    st.header("📄 문서 설정")
    uploaded_file = st.file_uploader("문서를 업로드하세요", type=["pdf", "txt", "md"])
    use_sample = st.checkbox("샘플 문서 사용", value=uploaded_file is None)

    chunk_size = st.slider("Chunk size", min_value=300, max_value=1800, value=900, step=100)
    chunk_overlap = st.slider("Chunk overlap", min_value=0, max_value=500, value=150, step=50)
    top_k = st.slider("Retriever Top-k", min_value=1, max_value=8, value=3, step=1)

    st.header("🤖 답변 설정")
    persona_name = st.selectbox("페르소나", list(PERSONAS.keys()))
    temperature = st.slider("temperature", min_value=0.0, max_value=1.0, value=0.0, step=0.1)
    strict_mode = st.checkbox("문서 근거 엄격 모드", value=True)

    if st.button("문서 인덱싱", type="primary", use_container_width=True):
        if not api_key:
            st.error("OPENAI_API_KEY가 필요합니다.")
        else:
            if uploaded_file is not None:
                file_bytes = uploaded_file.getvalue()
                file_name = uploaded_file.name
            elif use_sample:
                sample_path = Path(__file__).resolve().parents[1] / "sample_docs" / "house_lease_law_sample.txt"
                file_bytes = sample_path.read_bytes()
                file_name = sample_path.name
            else:
                st.warning("업로드 파일을 선택하거나 샘플 문서를 사용하세요.")
                st.stop()

            vectorstore, raw_text, n_splits = build_vectorstore_from_bytes(
                file_bytes=file_bytes,
                file_name=file_name,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                embedding_model=embedding_model,
                api_key=api_key,
            )
            st.session_state.vectorstore = vectorstore
            st.session_state.raw_text = raw_text
            st.success(f"인덱싱 완료: {file_name} / {n_splits}개 chunk")

    if st.button("대화 초기화", use_container_width=True):
        st.session_state.messages = [
            {"role": "assistant", "content": "대화를 초기화했습니다. 새 질문을 입력하세요."}
        ]
        st.session_state.last_sources = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("질문을 입력하세요. 예: 확정일자는 어디서 받나요? / 요약")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})

    if prompt.strip().lower() in ["/reset", "reset"]:
        st.session_state.messages = [
            {"role": "assistant", "content": "대화를 초기화했습니다. 새 질문을 입력하세요."}
        ]
        st.rerun()

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            hits = detect_sensitive_info(prompt)
            if hits:
                answer = f"민감정보로 보이는 내용이 포함되어 답변을 중단했습니다. 감지 항목: {hits}"
            elif not api_key:
                answer = "OPENAI_API_KEY가 설정되어 있지 않습니다. 사이드바에서 입력하거나 환경변수/st.secrets를 설정하세요."
            elif st.session_state.vectorstore is None:
                answer = "먼저 사이드바에서 문서를 업로드하거나 샘플 문서를 선택한 뒤 '문서 인덱싱'을 눌러주세요."
            else:
                client = get_client(api_key)
                if prompt.strip() == "요약":
                    answer = generate_summary(client, model, st.session_state.raw_text, temperature)
                elif prompt.strip() == "/출처":
                    if st.session_state.last_sources:
                        answer = "\n\n".join(st.session_state.last_sources)
                    else:
                        answer = "아직 표시할 출처가 없습니다. 먼저 문서 질문을 해주세요."
                else:
                    docs = st.session_state.vectorstore.similarity_search(prompt, k=top_k)
                    st.session_state.last_sources = [
                        f"문서 {i+1}: {doc.metadata.get('source', 'unknown')}"
                        + (f" / page {doc.metadata.get('page') + 1}" if isinstance(doc.metadata.get('page'), int) else "")
                        + f"\n> {doc.page_content[:220].replace(chr(10), ' ')}..."
                        for i, doc in enumerate(docs)
                    ]
                    answer = generate_rag_answer(
                        client=client,
                        model=model,
                        query=prompt,
                        docs=docs,
                        persona=PERSONAS[persona_name],
                        strict_mode=strict_mode,
                        temperature=temperature,
                    )

            st.markdown(answer)
            with st.expander("마지막 검색 출처 보기", expanded=False):
                if st.session_state.last_sources:
                    for src in st.session_state.last_sources:
                        st.markdown(src)
                else:
                    st.caption("검색 출처가 없습니다.")

        except Exception as exc:
            answer = f"오류가 발생했습니다: {exc}"
            st.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
