"""여러 페이지가 함께 쓰는 학습 자료 상태."""

from __future__ import annotations

import streamlit as st

from core.documents import Document, Index, build_index, load_pdf


def docs() -> dict[str, Document]:
    return st.session_state.setdefault("docs", {})


def index() -> Index | None:
    return st.session_state.get("index")


def add_uploads(files) -> list[str]:
    """업로드된 PDF를 읽어 상태에 넣는다. 새로 추가된 파일 이름 목록을 돌려준다."""
    added = []
    store = docs()
    removed = st.session_state.setdefault("removed_uploads", set())
    owners = st.session_state.setdefault("upload_owner", {})
    for f in files or []:
        fid = getattr(f, "file_id", f.name)
        if fid in removed:          # 삭제 버튼으로 지운 업로드는 다시 넣지 않는다
            continue
        data = f.getvalue()
        doc = load_pdf(f.name, data)
        key = f"{f.name}:{doc.digest}"
        owners.setdefault(key, set()).add(fid)
        if key not in store:
            store[key] = doc
            added.append(f.name)
    if added or "index" not in st.session_state:
        st.session_state["index"] = build_index(list(store.values())) if store else None
    return added


def remove(key: str) -> None:
    docs().pop(key, None)
    owners = st.session_state.setdefault("upload_owner", {})
    st.session_state.setdefault("removed_uploads", set()).update(owners.pop(key, set()))
    store = docs()
    st.session_state["index"] = build_index(list(store.values())) if store else None
