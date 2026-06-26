# GitHub Release Guide

## 1. Local quality gate

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

## 2. Manual functional validation

Run the Inventory Agent:

```cmd
uvicorn apps.inventory_agent.main:app --reload --port 8001
```

Test:

- `GET /health`
- Save memory: `Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.`
- Retrieve memory: `Qual o fornecedor do PARAFUSO-M20?`

## 3. Azure validation

Build in ACR:

```powershell
az acr build `
  --registry registrodecontainercassiofrd `
  --image inventory-agent:v1-0 `
  --file Dockerfile.inventory `
  .
```

Update Container Apps:

```powershell
az containerapp update `
  --name inventory-agent `
  --resource-group grupoderecursos_cassiofrd `
  --image registrodecontainercassiofrd.azurecr.io/inventory-agent:v1-0
```

## 4. Security checklist

Before pushing:

- Do not commit `.env`.
- Do not commit API keys.
- Do not commit local databases under `data/`.
- Do not commit FAISS indexes under `indexes/`.
- Do not commit logs under `logs/`.

## 5. Recommended first commit

```bash
git init
git add .
git commit -m "Release 1.0: multi-agent supply chain copilot"
```
