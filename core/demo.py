"""데모 모드 응답.

API 키가 없어도 화면 흐름(질문 → 근거 → 교차검증 → 퀴즈)을 확인할 수 있게
업로드한 자료의 문장을 이용해 그럴듯한 가짜 응답을 만든다.
실제 AI 응답이 아니므로 화면에 '데모 모드'를 항상 표시한다.
"""

from __future__ import annotations

import json
import random
import re


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?다])\s+|\n+", text)
    return [p.strip() for p in parts if 15 <= len(p.strip()) <= 160]


def respond(purpose: str, messages: list[dict], hint: dict) -> str:
    if purpose == "ping":
        return "OK (데모 모드)"

    if purpose == "answer":
        chunks = hint.get("chunks", [])
        if not chunks:
            return "업로드한 자료에서 관련 내용을 찾지 못했습니다."
        lines = ["(데모 모드 — 실제 AI 답변이 아니라 자료 문장을 그대로 보여 줍니다)", "", "**핵심 설명**"]
        for c in chunks[:2]:
            sents = _sentences(c["text"]) or [c["text"][:120]]
            lines.append(f"- {sents[0]} [{c['doc']} p.{c['page']}]")
        lines += ["", "**시험 포인트**", "- 위 정의와 특징을 구분해서 기억하세요."]
        return "\n".join(lines)

    if purpose == "verify":
        answer = hint.get("answer", "")
        claims = [l.lstrip("- ").strip() for l in answer.splitlines() if l.startswith("- ")][:4]
        out = []
        for i, c in enumerate(claims):
            verdict = "SUPPORTED" if i < len(claims) - 1 else "NOT_FOUND"
            out.append({"claim": c[:120], "verdict": verdict, "reason": "데모 판정입니다.", "page": None})
        return json.dumps({"claims": out}, ensure_ascii=False)

    if purpose == "quiz":
        chunks = hint.get("chunks", [])
        n = int(hint.get("n", 3))
        pool = [(s, c) for c in chunks for s in _sentences(c["text"])]
        random.seed(len(pool))
        random.shuffle(pool)
        qs = []
        for sent, c in pool[:n]:
            wrong = [s for s, _ in pool if s != sent][:3]
            while len(wrong) < 3:
                wrong.append("자료에 나오지 않는 설명입니다.")
            choices = [sent] + wrong
            random.shuffle(choices)
            qs.append({
                "question": f"다음 중 교재 {c['page']}쪽 내용과 일치하는 것은? (데모)",
                "choices": choices,
                "answer": choices.index(sent) + 1,
                "explanation": f"{c['page']}쪽에 이 문장이 나옵니다.",
                "page": c["page"],
                "concept": "",
            })
        return json.dumps({"questions": qs}, ensure_ascii=False)

    if purpose == "quiz_check":
        keys = hint.get("keys", [])
        return json.dumps({"answers": keys}, ensure_ascii=False)

    return "(데모 모드)"
