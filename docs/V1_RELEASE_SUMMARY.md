# v1.0 Release Summary

## Release identity

- Version: `1.0.0`
- Suggested Git tag: `v1.0.0`
- Suggested release title: `Enterprise AI Agent Platform v1.0.0`

## One-line description

Enterprise-style multi-agent supply-chain copilot platform with Azure AI Foundry, Azure Container Apps, Azure AI Search, RAG, contextual memory, source citations and trace observability.

## Demo script

1. Start Inventory, Supplier and Supervisor locally.
2. Open Streamlit.
3. Ask: `Quem fornece o PARAFUSO-M20?`
4. Ask: `Qual é o risco dele?`
5. Ask: `O que a política diz sobre estoque crítico?`
6. Open the Observability page.
7. Inspect the latest trace in Trace Explorer.

## Architecture message

This project is not only a chatbot. It is a platform-style architecture where:

- Azure AI Foundry can be the enterprise agent gateway;
- Supervisor owns business orchestration;
- specialist agents expose stable API capabilities;
- Azure AI Search provides structured and document retrieval;
- observability reconstructs the full execution path.
