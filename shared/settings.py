from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from shared.security import security

load_dotenv()


@dataclass(frozen=True)
class AppSettings:
    # Runtime
    llm_provider: str | None
    active_chat_model: str | None
    active_embedding_model: str | None

    # OpenAI / Azure OpenAI
    openai_api_key: str | None

    # Azure AI Search
    azure_search_endpoint: str | None
    azure_search_api_key: str | None
    azure_search_admin_key: str | None
    azure_search_index: str | None
    azure_search_index_name: str | None

    # Security
    api_token: str | None

    # Azure AI Foundry
    azure_ai_project_endpoint: str | None
    foundry_agent_id: str | None
    foundry_agent_key: str | None
    foundry_agent_deployment_file: str | None

    # Agent URLs
    inventory_agent_url: str | None
    supplier_agent_url: str | None
    supervisor_url: str | None

    # Cache / Redis
    redis_url: str | None
    cache_default_ttl_seconds: int
    cache_key_prefix: str
    redis_socket_connect_timeout_seconds: float
    redis_socket_timeout_seconds: float

    # Distributed memory
    memory_backend: str
    memory_key_prefix: str
    conversation_memory_ttl_seconds: int
    long_term_memory_ttl_seconds: int

    # Resilience
    a2a_max_attempts: int
    a2a_retry_backoff_seconds: float
    circuit_breaker_failure_threshold: int
    circuit_breaker_recovery_timeout_seconds: float

    # Observability
    streamlit_observability_log: str | None


def _get_int(name: str, default: int, minimum: int = 1) -> int:
    raw_value = security.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc

    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}.")
    return value


def _get_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw_value = security.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc

    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}.")
    return value


def _get_choice(name: str, default: str, allowed: set[str]) -> str:
    raw_value = security.get(name)
    value = (raw_value or default).strip().lower()
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_values}.")
    return value


def load_settings() -> AppSettings:
    return AppSettings(
        llm_provider=security.get("LLM_PROVIDER"),
        active_chat_model=security.get("ACTIVE_CHAT_MODEL") or security.get("OPENAI_CHAT_MODEL"),
        active_embedding_model=security.get("ACTIVE_EMBEDDING_MODEL") or security.get("OPENAI_EMBEDDING_MODEL"),

        openai_api_key=security.get("OPENAI_API_KEY"),

        azure_search_endpoint=security.get("AZURE_SEARCH_ENDPOINT"),
        azure_search_api_key=security.get("AZURE_SEARCH_API_KEY"),
        azure_search_admin_key=security.get("AZURE_SEARCH_ADMIN_KEY"),
        azure_search_index=security.get("AZURE_SEARCH_INDEX"),
        azure_search_index_name=security.get("AZURE_SEARCH_INDEX_NAME"),

        api_token=security.get("API_TOKEN"),

        azure_ai_project_endpoint=security.get("AZURE_AI_PROJECT_ENDPOINT"),
        foundry_agent_id=security.get("FOUNDRY_AGENT_ID"),
        foundry_agent_key=security.get("FOUNDRY_AGENT_KEY"),
        foundry_agent_deployment_file=security.get("FOUNDRY_AGENT_DEPLOYMENT_FILE"),

        inventory_agent_url=security.get("INVENTORY_AGENT_URL"),
        supplier_agent_url=security.get("SUPPLIER_AGENT_URL"),
        supervisor_url=security.get("SUPERVISOR_URL"),

        redis_url=security.get("REDIS_URL"),
        cache_default_ttl_seconds=_get_int(
            "CACHE_DEFAULT_TTL_SECONDS",
            default=3600,
        ),
        cache_key_prefix=security.get("CACHE_KEY_PREFIX", "enterprise-ai-agent") or "enterprise-ai-agent",
        redis_socket_connect_timeout_seconds=_get_float(
            "REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS",
            default=1.0,
            minimum=0.1,
        ),
        redis_socket_timeout_seconds=_get_float(
            "REDIS_SOCKET_TIMEOUT_SECONDS",
            default=1.0,
            minimum=0.1,
        ),

        memory_backend=_get_choice(
            "MEMORY_BACKEND",
            default="auto",
            allowed={"auto", "redis", "sqlite"},
        ),
        memory_key_prefix=security.get(
            "MEMORY_KEY_PREFIX",
            "enterprise-ai-agent-memory",
        ) or "enterprise-ai-agent-memory",
        conversation_memory_ttl_seconds=_get_int(
            "CONVERSATION_MEMORY_TTL_SECONDS",
            default=604800,
            minimum=0,
        ),
        long_term_memory_ttl_seconds=_get_int(
            "LONG_TERM_MEMORY_TTL_SECONDS",
            default=0,
            minimum=0,
        ),

        a2a_max_attempts=_get_int("A2A_MAX_ATTEMPTS", default=2),
        a2a_retry_backoff_seconds=_get_float(
            "A2A_RETRY_BACKOFF_SECONDS",
            default=1.0,
            minimum=0.0,
        ),
        circuit_breaker_failure_threshold=_get_int(
            "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
            default=3,
        ),
        circuit_breaker_recovery_timeout_seconds=_get_float(
            "CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
            default=30.0,
            minimum=0.1,
        ),

        streamlit_observability_log=security.get("STREAMLIT_OBSERVABILITY_LOG"),
    )


settings = load_settings()