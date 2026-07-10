from __future__ import annotations

import httpx

from .config import settings
from .schemas import Source


SYSTEM_PROMPT = """你是金融知识库问答助手。必须严格遵守：
1. 只能根据提供的“知识库片段”回答。
2. 如果片段不足以回答，必须说“知识库中没有检索到足够相关的内容，无法回答该问题。”
3. 不得引入片段之外的金融常识、市场判断、投资建议或数字。
4. 回答要在相关句子后标注来源编号，例如 [1]、[2]。
5. 不提供个性化投资建议。"""


async def generate_grounded_answer(question: str, sources: list[Source]) -> str | None:
    if not settings.llm_api_key or not sources:
        return None

    context = "\n\n".join(
        f"[{i}] title={s.title}; source={s.source}; page={s.page or 'n/a'}\n{s.text}"
        for i, s in enumerate(sources, start=1)
    )
    payload = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"问题：{question}\n\n知识库片段：\n{context}"},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    url = settings.llm_base_url.rstrip("/") + "/v1/chat/completions"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"].strip()
