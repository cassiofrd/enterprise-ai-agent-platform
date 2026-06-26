# GitHub setup

Use this guide before pushing the project to GitHub.

## 1. Confirm no secrets are in the project

Do not commit real API keys, `.env` files, FAISS indexes, logs, or virtual environments.

The project is designed to use environment variables locally and Azure Container Apps secrets in Azure.

## 2. Recommended first commit

From the project root:

```bash
git init
git status
git add .
git commit -m "Initial Azure Container Apps multi-agent project"
```

## 3. Create a GitHub repository

Create an empty repository in GitHub. Then connect it:

```bash
git branch -M main
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

## 4. Files that should not be committed

The `.gitignore` excludes:

```text
.venv/
__pycache__/
*.pyc
.env
indexes/
logs/
```

## 5. Azure deployment reminder

The current deployed Azure resources use:

```text
Azure Container Registry
Azure Container Apps Environment
inventory-agent Container App
supervisor-agent Container App
```

For future deploys, rebuild and push only the images that changed. If only `apps/supervisor/main.py` changed, rebuild and push only `supervisor-agent`.
