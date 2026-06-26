# LLM Provider Configuration

This project now uses a provider abstraction instead of coupling app code directly to one LLM vendor.

## Option 1: OpenAI public API

Use this for maximum portability across local development, Docker, AWS, GCP, or any container runtime.

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_key
OPENAI_CHAT_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

## Option 2: Azure OpenAI

Use this when running in Azure Container Apps or when aligning the project with Azure AI Foundry.

```env
LLM_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-12-01-preview
```

## Architecture

Application code should not instantiate `ChatOpenAI` or `AzureChatOpenAI` directly.

Use:

```python
from shared.llm import get_chat_llm, get_embeddings

llm = get_chat_llm()
embeddings = get_embeddings()
```

This keeps the agent portable while still supporting Azure-native deployments.
