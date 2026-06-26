# Azure Deployment Guide

This project supports deployment without local Docker. This is useful in corporate environments where Docker Desktop is blocked.

## Prerequisites

- Azure CLI
- Azure subscription
- Azure Container Registry
- Azure Container Apps environment
- Resource group

Example values used during development:

```text
Resource group: grupoderecursos_cassiofrd
ACR: registrodecontainercassiofrd
Inventory Container App: inventory-agent
```

## Build image in Azure Container Registry

From the project root in Azure Cloud Shell:

```powershell
az acr build `
  --registry registrodecontainercassiofrd `
  --image inventory-agent:v6-2 `
  --file Dockerfile.inventory `
  .
```

Verify the tag:

```powershell
az acr repository show-tags `
  --name registrodecontainercassiofrd `
  --repository inventory-agent `
  --output table
```

Expected tag:

```text
v6-2
```

## Update existing Container App

```powershell
az containerapp update `
  --name inventory-agent `
  --resource-group grupoderecursos_cassiofrd `
  --image registrodecontainercassiofrd.azurecr.io/inventory-agent:v6-2
```

## Test health endpoint

```cmd
curl https://inventory-agent.<your-domain>/health
```

Expected response includes:

```json
{
  "status": "ok",
  "agent": "inventory"
}
```

## Test memory endpoint behavior

Save memory:

```cmd
curl -X POST "https://inventory-agent.<your-domain>/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"operation\":{},\"messages\":[{\"type\":\"human\",\"content\":\"Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.\"}],\"trace_id\":\"azure-memoria-001\"}"
```

Retrieve memory:

```cmd
curl -X POST "https://inventory-agent.<your-domain>/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"operation\":{},\"messages\":[{\"type\":\"human\",\"content\":\"Qual o fornecedor do PARAFUSO-M20?\"}],\"trace_id\":\"azure-memoria-002\"}"
```

Expected response:

```text
O fornecedor do PARAFUSO-M20 é XYZ Metais.
```
