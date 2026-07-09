# Security Notes

This repository is designed as a reference implementation and portfolio project.

Do not commit:

- `.env` files;
- API keys;
- Azure Search admin keys;
- OpenAI or Azure OpenAI keys;
- local SQLite databases;
- runtime logs;
- generated indexes;
- personal or customer data.

Recommended production hardening:

- Use Azure Key Vault for secrets.
- Use Managed Identity where possible.
- Protect API endpoints with Microsoft Entra ID.
- Apply RBAC for user and tool permissions.
- Use private networking for internal services when appropriate.
- Export traces and metrics to Azure Monitor / Application Insights.
