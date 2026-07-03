import json
import os
from pathlib import Path

import jsonref
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import OpenApiTool, OpenApiAnonymousAuthDetails

load_dotenv()

PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
MODEL_DEPLOYMENT = os.getenv("FOUNDRY_AGENT_MODEL", "gpt-4.1")

# Supported profiles:
# - supervisor_agent: Foundry agent calls the Supervisor /copilot endpoint.
# - inventory_agent: Foundry agent calls Inventory OpenAPI tools directly.
# - supplier_agent: Foundry agent calls Supplier OpenAPI tools directly.
AGENT_KEY = os.getenv("FOUNDRY_AGENT_KEY", "supervisor_agent")
DEPLOYMENT_FILE = Path(os.getenv("FOUNDRY_AGENT_DEPLOYMENT_FILE", "deployment/foundry_agents.json"))

AGENT_PROFILES = {
    "supervisor_agent": {
        "name": "supply-chain-supervisor-openapi",
        "tool_name": "supervisor_tools",
        "openapi_spec_path": "openapi/foundry_supervisor_tools.openapi.json",
        "description": "Supervisor tool for multi-agent supply chain orchestration through Inventory and Supplier specialists.",
        "instructions": (
            "Você é um assistente corporativo de supply chain. "
            "Use a ferramenta OpenAPI do Supervisor sempre que o usuário fizer perguntas sobre estoque, produtos, fornecedores, contratos, performance, risco ou perguntas híbridas. "
            "O Supervisor decide quando consultar Inventory, Supplier ou ambos. "
            "Responda em português, de forma objetiva, com base nos dados retornados pela ferramenta."
        ),
    },
    "inventory_agent": {
        "name": "inventory-agent-openapi",
        "tool_name": "inventory_tools",
        "openapi_spec_path": "openapi/foundry_inventory_tools.openapi.json",
        "description": "Inventory API tools for product, supplier and purchasing policy lookup.",
        "instructions": (
            "Você é um agente especialista em estoque e suprimentos. "
            "Use as ferramentas OpenAPI de inventário sempre que o usuário perguntar "
            "sobre produtos, fornecedores, políticas de estoque ou políticas de compra. "
            "Responda em português, de forma objetiva e baseada nos dados retornados pelas ferramentas."
        ),
    },
    "supplier_agent": {
        "name": "supplier-agent-openapi",
        "tool_name": "supplier_tools",
        "openapi_spec_path": "openapi/foundry_supplier_tools.openapi.json",
        "description": "Supplier API tools for supplier profile, products, contracts and performance lookup.",
        "instructions": (
            "Você é um agente especialista em fornecedores e compras. "
            "Use as ferramentas OpenAPI de supplier sempre que o usuário perguntar sobre fornecedores, contratos, performance, risco, buyer, localização, SLA, lead time ou produtos fornecidos. "
            "Responda em português, de forma objetiva e baseada nos dados retornados pelas ferramentas."
        ),
    },
}

if not PROJECT_ENDPOINT:
    raise ValueError("AZURE_AI_PROJECT_ENDPOINT não foi definido no .env")

if AGENT_KEY not in AGENT_PROFILES:
    raise ValueError(
        f"FOUNDRY_AGENT_KEY inválido: {AGENT_KEY}. "
        f"Use um destes valores: {', '.join(AGENT_PROFILES)}"
    )

profile = AGENT_PROFILES[AGENT_KEY]
openapi_spec_path = profile["openapi_spec_path"]

print(f"Perfil selecionado: {AGENT_KEY}")
print(f"Carregando OpenAPI: {openapi_spec_path}")
with open(openapi_spec_path, "r", encoding="utf-8") as f:
    openapi_spec = jsonref.loads(f.read())

print("Conectando ao Azure AI Foundry...")
client = AgentsClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
)

openapi_tool = OpenApiTool(
    name=profile["tool_name"],
    spec=openapi_spec,
    description=profile["description"],
    auth=OpenApiAnonymousAuthDetails(),
)

print("Criando agente...")
agent = client.create_agent(
    model=MODEL_DEPLOYMENT,
    name=profile["name"],
    instructions=profile["instructions"],
    tools=openapi_tool.definitions,
)

DEPLOYMENT_FILE.parent.mkdir(parents=True, exist_ok=True)

data = {}
if DEPLOYMENT_FILE.exists():
    data = json.loads(DEPLOYMENT_FILE.read_text(encoding="utf-8"))

data[AGENT_KEY] = agent.id

DEPLOYMENT_FILE.write_text(
    json.dumps(data, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

print("Agente criado com sucesso!")
print(f"Agent key: {AGENT_KEY}")
print(f"Agent ID: {agent.id}")
print(f"ID salvo em: {DEPLOYMENT_FILE}")
