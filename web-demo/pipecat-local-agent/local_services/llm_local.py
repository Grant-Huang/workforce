"""Local LLM backends: llama-server (llama.cpp) / OpenAI-compatible HTTP."""

from __future__ import annotations

from loguru import logger

from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.qwen.llm import QwenLLMService

from .config import LocalAgentConfig


def build_llm_service(config: LocalAgentConfig):
    backend = config.llm_backend

    if backend in ("llamacpp", "ollama", "openai_compat", "local"):
        logger.info(
            f"LLM backend={backend} model={config.llm_model} base_url={config.llm_base_url}"
        )
        return OpenAILLMService(
            api_key=config.llm_api_key or "local",
            base_url=config.llm_base_url,
            settings=OpenAILLMService.Settings(
                model=config.llm_model,
                system_instruction=config.system_instruction,
                temperature=config.llm_temperature,
                max_completion_tokens=config.llm_max_tokens,
            ),
        )

    if backend == "dashscope":
        if not config.dashscope_api_key:
            raise ValueError("LOCAL_LLM_BACKEND=dashscope requires QWEN_API_KEY in .env")
        base_url = _dashscope_compatible_base()
        logger.info(f"LLM backend=dashscope model={config.llm_model}")
        return QwenLLMService(
            api_key=config.dashscope_api_key,
            base_url=base_url,
            settings=QwenLLMService.Settings(
                model=config.llm_model or "qwen-plus",
                system_instruction=config.system_instruction,
                temperature=config.llm_temperature,
                max_completion_tokens=config.llm_max_tokens,
            ),
        )

    raise ValueError(
        f"Unknown LOCAL_LLM_BACKEND={backend!r}. "
        "Use llamacpp, openai_compat, or dashscope."
    )


def _dashscope_compatible_base() -> str:
    import os

    workspace_id = os.environ.get("QWEN_WORKSPACE_ID", "")
    if workspace_id:
        return f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    return "https://dashscope.aliyuncs.com/compatible-mode/v1"
