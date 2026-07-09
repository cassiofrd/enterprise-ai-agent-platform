"""
Centralized security and secrets provider.

Sprint v2.1.1

Future roadmap:
- Azure Key Vault
- Managed Identity
- Entra ID integration
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

class MissingSecretError(RuntimeError):
    """Raised when a required secret is missing."""


@dataclass
class SecurityValidationResult:
    valid: bool
    missing: List[str]


class SecurityProvider:

    REQUIRED_SECRETS = [
        "OPENAI_API_KEY",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_API_KEY",
    ]

    OPTIONAL_SECRETS = [
        "API_TOKEN",
        "FOUNDRY_AGENT_ID",
        "FOUNDRY_AGENT_KEY",
        "AZURE_SEARCH_INDEX",
        "AZURE_SEARCH_ADMIN_KEY",
    ]

    def get(self, name: str, default: str | None = None) -> str | None:
        return os.getenv(name, default)

    @property
    def openai_key(self):
        return self.get("OPENAI_API_KEY")

    @property
    def azure_search_endpoint(self):
        return self.get("AZURE_SEARCH_ENDPOINT")

    @property
    def azure_search_key(self):
        return self.get("AZURE_SEARCH_API_KEY")

    @property
    def api_token(self):
        return self.get("API_TOKEN")

    @property
    def foundry_agent_id(self):
        return self.get("FOUNDRY_AGENT_ID")

    @property
    def foundry_agent_key(self):
        return self.get("FOUNDRY_AGENT_KEY")

    def validate_required_secrets(self) -> SecurityValidationResult:

        missing = []

        for secret in self.REQUIRED_SECRETS:
            if not self.get(secret):
                missing.append(secret)

        return SecurityValidationResult(
            valid=len(missing) == 0,
            missing=missing,
        )

    def ensure_required_secrets(self):

        result = self.validate_required_secrets()

        if not result.valid:
            raise MissingSecretError(
                "Missing required secrets: "
                + ", ".join(result.missing)
            )

    def dump_configuration(self) -> Dict:

        return {
            "required": {
                s: bool(self.get(s))
                for s in self.REQUIRED_SECRETS
            },
            "optional": {
                s: bool(self.get(s))
                for s in self.OPTIONAL_SECRETS
            },
        }


security = SecurityProvider()