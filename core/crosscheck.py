"""두 모델 교차검증 (선택 기능).

A 모델이 만든 답변을, B 모델이 '근거 문단만 보고' 주장 단위로 판정한다.
B는 새로 설명하지 않고 판정만 하므로 비용이 적게 든다.
한계: 두 모델이 같은 오개념을 공유하면 함께 틀릴 수 있다. 1차 방어선은 근거 검색이다.
"""

from __future__ import annotations

from core import llm
from core.config import ModelCfg

VERDICT_LABEL = {
    "SUPPORTED": ("✅", "근거 확인"),
    "CONTRADICTED": ("❌", "근거와 다름"),
    "NOT_FOUND": ("⚠️", "근거 없음"),
}

_PROMPT = """너는 검증자다. 아래 [근거]만 사용해서 [답변]을 검증하라.
너의 배경지식으로 판단하지 말고, 근거에 적힌 내용만 기준으로 삼아라.

1) 답변에서 사실 주장을 최대 8개 뽑아라. (인사말·학습 조언은 제외)
2) 각 주장을 다음 중 하나로 판정하라.
   - SUPPORTED: 근거가 그 주장을 뒷받침한다
   - CONTRADICTED: 근거와 모순된다
   - NOT_FOUND: 근거에서 확인할 수 없다
3) 이유를 한 문장으로 쓰고, 관련 쪽 번호가 있으면 page에 숫자로 적어라.

JSON만 출력하라.
{{"claims": [{{"claim": "...", "verdict": "SUPPORTED", "reason": "...", "page": 12}}]}}

[근거]
{context}

[답변]
{answer}
"""


def verify(model: ModelCfg, context: str, answer: str) -> dict:
    text = llm.chat(
        model,
        [{"role": "user", "content": _PROMPT.format(context=context, answer=answer)}],
        purpose="verify",
        max_tokens=1200,
        temperature=0,
        demo_hint={"answer": answer},
    )
    try:
        claims = llm.parse_json(text).get("claims", [])
    except Exception:
        return {"overall": "⚠️", "summary": "검증 결과를 해석하지 못했습니다.", "claims": [], "raw": text}

    verdicts = [str(c.get("verdict", "NOT_FOUND")).upper() for c in claims]
    if "CONTRADICTED" in verdicts:
        overall, summary = "❌", "근거와 다른 내용이 있습니다. 표시된 문장을 원문과 비교하세요."
    elif "NOT_FOUND" in verdicts or not verdicts:
        overall, summary = "⚠️", "일부 내용은 자료에서 확인되지 않았습니다."
    else:
        overall, summary = "✅", "모든 주장이 자료로 확인되었습니다."
    return {"overall": overall, "summary": summary, "claims": claims, "verifier": model.label}
