"""PDF 텍스트 추출과 검색.

임베딩 API 없이 동작하도록 BM25(키워드 기반 검색)를 직접 구현했다.
한국어는 띄어쓰기·조사 때문에 단어가 잘 안 맞으므로 글자 2개씩(바이그램)으로 쪼갠다.
멀티AI 플랫폼이 임베딩 API를 제공하면 search()만 교체하면 된다.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import streamlit as st

CHUNK_CHARS = 900


@dataclass
class Chunk:
    doc: str
    page: int
    text: str

    def as_dict(self) -> dict:
        return {"doc": self.doc, "page": self.page, "text": self.text}


@dataclass
class Document:
    name: str
    digest: str
    pages: int
    chunks: list[Chunk] = field(default_factory=list)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@st.cache_data(show_spinner=False, max_entries=20)
def _extract(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [_clean(p.extract_text() or "") for p in reader.pages]


def load_pdf(name: str, data: bytes) -> Document:
    pages = _extract(data)
    doc = Document(name=name, digest=hashlib.sha1(data).hexdigest()[:12], pages=len(pages))
    for i, text in enumerate(pages, start=1):
        # 한 쪽이 길면 문단 경계를 살려서 나눈다. 쪽 번호는 그대로 유지.
        buf = ""
        for para in text.split("\n"):
            if len(buf) + len(para) > CHUNK_CHARS and buf:
                doc.chunks.append(Chunk(name, i, buf.strip()))
                buf = ""
            buf += para + "\n"
        if buf.strip():
            doc.chunks.append(Chunk(name, i, buf.strip()))
    return doc


# ---------------- 검색 (BM25) ----------------

def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in re.findall(r"[a-zA-Z0-9]+|[가-힣]+", text.lower()):
        if "가" <= word[0] <= "힣":
            tokens.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        else:
            tokens.append(word)
    return tokens


class Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(c.text)) for c in chunks]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[float, Chunk]]:
        q = tokenize(query)
        scored = []
        for tf, length, chunk in zip(self.tfs, self.lens, self.chunks):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            if s > 0:
                scored.append((s, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]


def build_index(docs: list[Document]) -> Index:
    return Index([c for d in docs for c in d.chunks])


def format_context(hits: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.doc} p.{c.page}]\n{c.text}" for c in hits)
