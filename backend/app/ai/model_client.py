from __future__ import annotations

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from ..config import settings


def get_model() -> ChatOpenAI | None:
    if not settings.dashscope_api_key:
        return None
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.dashscope_api_key,
        base_url=settings.bailian_base_url,
        temperature=0.2,
        timeout=45,
        max_retries=1,
    )


def invoke(messages: list[BaseMessage]) -> str | None:
    model = get_model()
    if model is None:
        return None
    try:
        response = model.invoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)
    except Exception:
        return None
