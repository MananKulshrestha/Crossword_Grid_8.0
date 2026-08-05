"""DeepInfra backend for QUERY-TIME LLM/embedding calls only (query.py).

DeepInfra exposes an OpenAI-compatible API at /v1/openai, so this reuses
LightRAG's own lightrag.llm.openai wrappers (openai_complete_if_cache,
openai_embed) pointed at DeepInfra's base_url/api_key instead of
reimplementing the HTTP/retry logic -- just swap the endpoint.

Never imported by ingest.py -- ingestion always goes through the Ollama
cluster (multi_ollama.py/servers.txt), regardless of INFERENCE_BACKEND.
See config.py's INFERENCE_BACKEND comment.
"""

from lightrag.llm.openai import openai_complete_if_cache, openai_embed

from config import DEEPINFRA_API_KEY, DEEPINFRA_BASE_URL


class DeepInfraLLM:
    """Matches LightRAG's llm_model_func contract:
    ``async def (prompt, system_prompt=None, history_messages=[], **kwargs)``,
    same shape as multi_ollama.MultiOllamaLoadBalancer.__call__."""

    def __init__(self, model):
        self.model = model

    async def __call__(self, prompt, system_prompt=None, history_messages=None, **kwargs):
        # "host" is an Ollama-only kwarg (see multi_ollama.py) -- strip it if
        # present so it doesn't get forwarded to the OpenAI-style client as
        # an unexpected keyword argument.
        kwargs.pop("host", None)
        return await openai_complete_if_cache(
            self.model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            base_url=DEEPINFRA_BASE_URL,
            api_key=DEEPINFRA_API_KEY,
            **kwargs,
        )


async def deepinfra_embed(texts, model):
    """Matches LightRAG's EmbeddingFunc contract: ``async def (texts) -> np.ndarray``."""
    return await openai_embed(
        texts,
        model=model,
        base_url=DEEPINFRA_BASE_URL,
        api_key=DEEPINFRA_API_KEY,
    )
