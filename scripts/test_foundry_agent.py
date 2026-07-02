import json
import os
from pathlib import Path

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient

load_dotenv()

PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
DEPLOYMENT_FILE = Path(os.getenv("FOUNDRY_AGENT_DEPLOYMENT_FILE", "deployment/foundry_agents.json"))
AGENT_KEY = os.getenv("FOUNDRY_AGENT_KEY", "inventory_agent")
DEFAULT_QUESTION = "Qual é o fornecedor do PARAFUSO-M20?"

if not PROJECT_ENDPOINT:
    raise ValueError("AZURE_AI_PROJECT_ENDPOINT não foi definido no .env")

if not DEPLOYMENT_FILE.exists():
    raise FileNotFoundError(
        f"Arquivo de deployment não encontrado: {DEPLOYMENT_FILE}. "
        "Execute scripts/create_foundry_agent.py primeiro ou defina FOUNDRY_AGENT_ID."
    )

agents = json.loads(DEPLOYMENT_FILE.read_text(encoding="utf-8"))
agent_id = os.getenv("FOUNDRY_AGENT_ID") or agents[AGENT_KEY]
question = os.getenv("FOUNDRY_TEST_QUESTION", DEFAULT_QUESTION)

client = AgentsClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
)

print(f"Agent ID: {agent_id}")
print(f"Pergunta: {question}")

run = client.create_thread_and_process_run(
    agent_id=agent_id,
    thread={
        "messages": [
            {
                "role": "user",
                "content": question,
            }
        ]
    },
)

print(f"Run status: {run.status}")

messages = client.messages.list(thread_id=run.thread_id)

print("\nResposta:")
for message in messages:
    if message.role == "assistant":
        for content in message.content:
            if content.type == "text":
                print(content.text.value)
