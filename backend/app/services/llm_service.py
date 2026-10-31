"""创建并复用旅游助手的大模型客户端。"""

from typing import Optional

from hello_agents import HelloAgentsLLM

from app.config import get_settings


_llm_instance: Optional[HelloAgentsLLM] = None


def get_llm() -> HelloAgentsLLM:
    """首次调用时创建客户端，后续调用复用同一个实例。"""
    global _llm_instance

    if _llm_instance is None:
        settings = get_settings()
        api_key = settings.llm_api_key.get_secret_value()

        if not all((settings.llm_model_id, settings.llm_base_url, api_key)):
            raise ValueError(
                "请在 backend/.env 中填写 LLM_MODEL_ID、LLM_BASE_URL 和 LLM_API_KEY。"
            )

        _llm_instance = HelloAgentsLLM(
            model=settings.llm_model_id,
            base_url=settings.llm_base_url,
            api_key=api_key,
            provider="auto",
        )

    return _llm_instance
