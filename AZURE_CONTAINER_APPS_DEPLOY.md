# Deploy to Azure Container Apps

This project has two containerized services:

```text
supervisor-api
↓ HTTP / A2A
inventory-agent-api
↓ stdio MCP
mcp_servers.inventory.server
↓ vector RAG
FAISS + OpenAI embeddings
```

For the first Azure deployment, the MCP server remains inside the Inventory Agent container and is launched by the Inventory Agent through stdio. This keeps the deployment simpler.

## Files added for Azure

```text
Dockerfile.inventory
Dockerfile.supervisor
docker-compose.yml
.dockerignore
.env.azure.example
AZURE_CONTAINER_APPS_DEPLOY.md
```

## 1. Test with Docker locally

Create `.env` from `.env.azure.example`:

```cmd
copy .env.azure.example .env
```

Edit `.env` and set:

```text
OPENAI_API_KEY=your_key_without_quotes
```

Build and run:

```cmd
docker compose up --build
```

Open:

```text
http://localhost:8000/docs
```

Test:

```text
POST /chat
```

## 2. Azure deployment architecture

Recommended first version:

```text
Azure Container App: inventory-agent
Azure Container App: supervisor
Azure Container Registry: stores images
```

The Supervisor calls the Inventory Agent through the `INVENTORY_AGENT_URL` environment variable.

## 3. Build images locally

Variables used below:

```cmd
set RESOURCE_GROUP=rg-agent-demo
set LOCATION=eastus
set ACR_NAME=<uniqueacrname>
set ACA_ENV=aca-agent-env
set INVENTORY_APP=inventory-agent
set SUPERVISOR_APP=supervisor-agent
```

Create resource group:

```cmd
az group create --name %RESOURCE_GROUP% --location %LOCATION%
```

Create Azure Container Registry:

```cmd
az acr create --resource-group %RESOURCE_GROUP% --name %ACR_NAME% --sku Basic
```

Login:

```cmd
az acr login --name %ACR_NAME%
```

Get registry server:

```cmd
for /f "tokens=*" %i in ('az acr show --name %ACR_NAME% --query loginServer -o tsv') do set ACR_LOGIN_SERVER=%i
```

Build and push Inventory image:

```cmd
docker build -f Dockerfile.inventory -t %ACR_LOGIN_SERVER%/inventory-agent:latest .
docker push %ACR_LOGIN_SERVER%/inventory-agent:latest
```

Build and push Supervisor image:

```cmd
docker build -f Dockerfile.supervisor -t %ACR_LOGIN_SERVER%/supervisor-agent:latest .
docker push %ACR_LOGIN_SERVER%/supervisor-agent:latest
```

## 4. Create Container Apps environment

```cmd
az containerapp env create ^
  --name %ACA_ENV% ^
  --resource-group %RESOURCE_GROUP% ^
  --location %LOCATION%
```

## 5. Deploy Inventory Agent

For a first test, enable external ingress so you can easily get the URL.

```cmd
az containerapp create ^
  --name %INVENTORY_APP% ^
  --resource-group %RESOURCE_GROUP% ^
  --environment %ACA_ENV% ^
  --image %ACR_LOGIN_SERVER%/inventory-agent:latest ^
  --registry-server %ACR_LOGIN_SERVER% ^
  --target-port 8001 ^
  --ingress external ^
  --env-vars OPENAI_API_KEY=your_key_without_quotes OPENAI_CHAT_MODEL=gpt-4o OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

Get Inventory URL:

```cmd
for /f "tokens=*" %i in ('az containerapp show --name %INVENTORY_APP% --resource-group %RESOURCE_GROUP% --query properties.configuration.ingress.fqdn -o tsv') do set INVENTORY_FQDN=%i
set INVENTORY_AGENT_URL=https://%INVENTORY_FQDN%/invoke
```

## 6. Deploy Supervisor API

```cmd
az containerapp create ^
  --name %SUPERVISOR_APP% ^
  --resource-group %RESOURCE_GROUP% ^
  --environment %ACA_ENV% ^
  --image %ACR_LOGIN_SERVER%/supervisor-agent:latest ^
  --registry-server %ACR_LOGIN_SERVER% ^
  --target-port 8000 ^
  --ingress external ^
  --env-vars OPENAI_API_KEY=your_key_without_quotes OPENAI_CHAT_MODEL=gpt-4o OPENAI_EMBEDDING_MODEL=text-embedding-3-small INVENTORY_AGENT_URL=%INVENTORY_AGENT_URL%
```

Get Supervisor URL:

```cmd
az containerapp show --name %SUPERVISOR_APP% --resource-group %RESOURCE_GROUP% --query properties.configuration.ingress.fqdn -o tsv
```

Open:

```text
https://<supervisor-fqdn>/docs
```

## 7. Important notes

### Secrets

For learning, this guide passes `OPENAI_API_KEY` as an environment variable. For a more realistic setup, move secrets to Azure Key Vault or Container Apps secrets.

### FAISS index

The FAISS index is generated inside the Inventory Agent container filesystem. This is OK for a demo. For production, use Azure AI Search or persistent storage.

### MCP

For this first deployment, the MCP server is not a separate Container App. It is executed internally by the Inventory Agent via:

```text
python -m mcp_servers.inventory.server
```

Later you can expose MCP as its own service.

### Internal communication

For production, consider making the Inventory Agent internal-only and exposing only the Supervisor API publicly.
