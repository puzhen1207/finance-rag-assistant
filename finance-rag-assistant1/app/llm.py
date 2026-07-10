from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings
from .schemas import Source


SYSTEM_PROMPT = """你是金融知识库问答助手。必须严格遵守：
1. 只能根据提供的“知识库片段”回答，不得引入片段之外的知识、数字、判断或投资建议。
2. 你的任务不是复制片段，而是结合用户问题，对检索片段进行归纳、组织和改写，形成完整、自然、连贯的中文回答。
3. 如果片段中有断句、半句话、页眉页脚或列表残片，需要在不添加外部信息的前提下整理成通顺表达。
4. 只要片段中包含与问题直接相关的信息，即使不是完整定义，也必须基于片段回答，不要拒答。
5. 只有所有片段都与问题明显无关时，才说“知识库中没有检索到足够相关的内容，无法回答该问题。”
6. 回答中的关键结论要标注来源编号，例如 [1]、[2]。
7. 不提供个性化投资建议。"""


@dataclass
class LLMResult:
    answer: str | None
    status: str
    error: str | None = None


REFUSAL_MARKERS = (
    "没有检索到足够相关",
    "无法回答该问题",
    "片段不足以回答",
    "没有足够相关的内容",
)


def is_refusal_answer(answer: str | None) -> bool:
    if not answer:
        return False
    return any(marker in answer for marker in REFUSAL_MARKERS)


async def generate_grounded_answer(question: str, sources: list[Source]) -> LLMResult:
    if not sources:
        return LLMResult(answer=None, status="no_sources")
    if not settings.llm_api_key:
        return LLMResult(answer=None, status="not_configured")

    context = "\n\n".join(
        f"[{i}] 标题：{s.title}\n来源：{s.source}\n页码：{s.page or 'n/a'}\n片段内容：{s.text}"
        for i, s in enumerate(sources, start=1)
    )
    payload = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"用户问题：{question}\n\n"
                    f"知识库片段：\n{context}\n\n"
                    "请基于以上片段回答用户问题。要求：\n"
                    "- 先直接回答问题；\n"
                    "- 不要逐字复制片段；\n"
                    "- 不要输出不完整句子；\n"
                    "- 如果片段中出现了问题关键词或同义表述，请认为这是可用证据，并基于它整理答案；\n"
                    "- 不要在已有相关片段时说没有检索到相关内容；\n"
                    "- 能分点时用清晰的中文分点；\n"
                    "- 每个关键结论后标注对应来源编号。"
                ),
            },
        ],
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    url = settings.llm_base_url.rstrip("/") + "/v1/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()
        return LLMResult(answer=answer, status="generated")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        return LLMResult(answer=None, status="failed", error=f"HTTP {exc.response.status_code}: {detail}")
    except Exception as exc:
        return LLMResult(answer=None, status="failed", error=str(exc))
