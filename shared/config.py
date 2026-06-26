from pathlib import Path
import os
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

INVENTORY_AGENT_URL = os.getenv(
    "INVENTORY_AGENT_URL",
    "http://localhost:8001/invoke",
)

SUPPLIER_AGENT_URL = os.getenv(
    "SUPPLIER_AGENT_URL",
    "https://supplier-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/invoke",
)

KNOWLEDGE_BASE_PATH = PROJECT_ROOT / "knowledge_base" / "inventory_knowledge_base_vector.txt"
VECTOR_INDEX_PATH = PROJECT_ROOT / "indexes" / "inventory_faiss_index"

LOG_DIR = PROJECT_ROOT / "logs"
EVENT_LOG_PATH = LOG_DIR / "agent_events.jsonl"

# LLM provider abstraction
# Supported values: openai, azure_openai
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.0"))
MODEL_MAX_RETRIES = int(os.getenv("MODEL_MAX_RETRIES", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))

# OpenAI public API settings
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# Azure OpenAI settings
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# Human-readable active model/deployment labels for logs and metrics.
ACTIVE_CHAT_MODEL = (
    AZURE_OPENAI_CHAT_DEPLOYMENT if LLM_PROVIDER == "azure_openai" else OPENAI_CHAT_MODEL
)
ACTIVE_EMBEDDING_MODEL = (
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT if LLM_PROVIDER == "azure_openai" else OPENAI_EMBEDDING_MODEL
)

MCP_INVENTORY_SERVER_MODULE = "mcp_servers.inventory.server"

DATA_DIR = PROJECT_ROOT / "data"
MEMORY_DB_PATH = DATA_DIR / "agent_memory.db"

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "supply-chain-docs")
