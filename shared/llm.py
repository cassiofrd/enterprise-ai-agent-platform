from __future__ import annotations

from functools import lru_cache

from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)

from shared.config import (
    ACTIVE_CHAT_MODEL,
    ACTIVE_EMBEDDING_MODEL,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    LLM_PROVIDER,
    MODEL_MAX_RETRIES,
    MODEL_TEMPERATURE,
    OPENAI_CHAT_MODEL,
    OPENAI_EMBEDDING_MODEL,
    REQUEST_TIMEOUT,
)


def _require(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}. "
            "Check your .env file or Azure Container Apps environment variables."
        )
    return value


@lru_cache(maxsize=8)
def get_chat_llm(
    *,
    temperature: float | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
):
    """Return the configured chat LLM without coupling app code to one provider."""
    resolved_temperature = MODEL_TEMPERATURE if temperature is None else temperature
    resolved_timeout = REQUEST_TIMEOUT if timeout is None else timeout
    resolved_retries = MODEL_MAX_RETRIES if max_retries is None else max_retries

    if LLM_PROVIDER == "azure_openai":
        return AzureChatOpenAI(
            azure_endpoint=_require(AZURE_OPENAI_ENDPOINT, "AZURE_OPENAI_ENDPOINT"),
            api_key=_require(AZURE_OPENAI_API_KEY, "AZURE_OPENAI_API_KEY"),
            azure_deployment=_require(
                AZURE_OPENAI_CHAT_DEPLOYMENT,
                "AZURE_OPENAI_CHAT_DEPLOYMENT",
            ),
            api_version=AZURE_OPENAI_API_VERSION,
            temperature=resolved_temperature,
            timeout=resolved_timeout,
            max_retries=resolved_retries,
        )

    if LLM_PROVIDER == "openai":
        return ChatOpenAI(
            model=OPENAI_CHAT_MODEL,
            temperature=resolved_temperature,
            timeout=resolved_timeout,
            max_retries=resolved_retries,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER={LLM_PROVIDER!r}. "
        "Supported values: openai, azure_openai."
    )


@lru_cache(maxsize=4)
def get_embeddings():
    """Return embeddings for the configured provider."""
    if LLM_PROVIDER == "azure_openai":
        return AzureOpenAIEmbeddings(
            azure_endpoint=_require(AZURE_OPENAI_ENDPOINT, "AZURE_OPENAI_ENDPOINT"),
            api_key=_require(AZURE_OPENAI_API_KEY, "AZURE_OPENAI_API_KEY"),
            azure_deployment=_require(
                AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
            ),
            api_version=AZURE_OPENAI_API_VERSION,
        )

    if LLM_PROVIDER == "openai":
        return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)

    raise ValueError(
        f"Unsupported LLM_PROVIDER={LLM_PROVIDER!r}. "
        "Supported values: openai, azure_openai."
    )


def get_active_chat_model_name() -> str:
    return ACTIVE_CHAT_MODEL or "unknown-chat-model"


def get_active_embedding_model_name() -> str:
    return ACTIVE_EMBEDDING_MODEL or "unknown-embedding-model"
