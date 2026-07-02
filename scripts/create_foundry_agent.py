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

OPENAPI_SPEC_PATH = "openapi/foundry_inventory_tools.openapi.json"
DEPLOYMENT_FILE = Path("deployment/foundry_agents.json")

AGENT_KEY = "inventory_agent"
AGENT_NAME = "inventory-agent-openapi"

if not PROJECT_ENDPOINT:
    raise ValueError("AZURE_AI_PROJECT_ENDPOINT não foi definido no .env")

print("Carregando OpenAPI enxuto...")
with open(OPENAPI_SPEC_PATH, "r", encoding="utf-8") as f:
    openapi_spec = jsonref.loads(f.read())

print("Conectando ao Azure AI Foundry...")
client = AgentsClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
)

openapi_tool = OpenApiTool(
    name="inventory_tools",
    spec=openapi_spec,
    description="Inventory API tools for product, supplier and purchasing policy lookup.",
    auth=OpenApiAnonymousAuthDetails(),
)

print("Criando agente...")
agent = client.create_agent(
    model=MODEL_DEPLOYMENT,
    name=AGENT_NAME,
    instructions=(
        "Você é um agente especialista em estoque e suprimentos. "
        "Use as ferramentas OpenAPI de inventário sempre que o usuário perguntar "
        "sobre produtos, fornecedores, políticas de estoque ou políticas de compra. "
        "Responda em português, de forma objetiva e baseada nos dados retornados pelas ferramentas."
    ),
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
print(f"Agent ID: {agent.id}")
print(f"ID salvo em: {DEPLOYMENT_FILE}")