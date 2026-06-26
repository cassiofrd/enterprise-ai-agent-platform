from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "inventory_agent_eval.csv"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def contains_ground_truth(response: str, ground_truth: str) -> bool:
    """Simple deterministic check.

    This is intentionally conservative and is not a replacement for LLM-as-judge
    evaluation. It is useful as a quick smoke test before uploading results to
    Azure AI Foundry evaluation.
    """
    response_norm = normalize_text(response)
    truth_norm = normalize_text(ground_truth)

    if not truth_norm:
        return False

    if truth_norm in response_norm:
        return True

    # For "not found" cases, accept common refusal wording.
    not_found_markers = [
        "não encontrei",
        "não está disponível",
        "não posso fornecer",
        "não posso estimar",
        "não há informação",
    ]
    if any(marker in truth_norm for marker in not_found_markers):
        return any(marker in response_norm for marker in not_found_markers)

    return False


def build_payload(agent_type: str, query: str) -> dict[str, Any]:
    if agent_type == "supervisor_copilot":
        return {"question": query}

    if agent_type == "supervisor_chat":
        return {"message": query}

    if agent_type == "inventory_invoke":
        return {
            "messages": [
                {
                    "type": "human",
                    "content": query,
                }
            ]
        }

    raise ValueError(
        "Unsupported agent_type. Use: supervisor_copilot, supervisor_chat, inventory_invoke"
    )


def extract_response(agent_type: str, payload: dict[str, Any]) -> str:
    if agent_type == "supervisor_copilot":
        return str(payload.get("answer", ""))

    if agent_type in {"supervisor_chat", "inventory_invoke"}:
        return str(payload.get("response", ""))

    return str(payload)


def call_agent(
    url: str,
    agent_type: str,
    query: str,
    timeout_seconds: int,
) -> tuple[str, str | None, int | None, float]:
    started = time.perf_counter()
    try:
        response = requests.post(
            url,
            json=build_payload(agent_type=agent_type, query=query),
            timeout=timeout_seconds,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        if response.status_code >= 400:
            return "", f"HTTP {response.status_code}: {response.text[:500]}", response.status_code, elapsed_ms

        data = response.json()
        return extract_response(agent_type=agent_type, payload=data), None, response.status_code, elapsed_ms

    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        return "", f"{type(exc).__name__}: {exc}", None, elapsed_ms


def run_evaluation(
    input_csv: Path,
    output_csv: Path,
    agent_url: str,
    agent_type: str,
    timeout_seconds: int,
) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with input_csv.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)

        required = {"query", "ground_truth"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Dataset is missing required columns: {sorted(missing)}. "
                "Expected at least: query, ground_truth"
            )

        fieldnames = [
            "run_id",
            "agent_type",
            "agent_url",
            "query",
            "response",
            "ground_truth",
            "category",
            "contains_ground_truth",
            "latency_ms",
            "http_status",
            "error",
        ]

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        with output_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=fieldnames)
            writer.writeheader()

            for index, row in enumerate(reader, start=1):
                query = (row.get("query") or "").strip()
                ground_truth = (row.get("ground_truth") or "").strip()
                category = (row.get("category") or "").strip()

                if not query:
                    continue

                print(f"[{index}] {query}")
                response, error, http_status, latency_ms = call_agent(
                    url=agent_url,
                    agent_type=agent_type,
                    query=query,
                    timeout_seconds=timeout_seconds,
                )

                writer.writerow(
                    {
                        "run_id": run_id,
                        "agent_type": agent_type,
                        "agent_url": agent_url,
                        "query": query,
                        "response": response,
                        "ground_truth": ground_truth,
                        "category": category,
                        "contains_ground_truth": contains_ground_truth(response, ground_truth),
                        "latency_ms": round(latency_ms, 2),
                        "http_status": http_status if http_status is not None else "",
                        "error": error or "",
                    }
                )

    print(f"\nSaved results to: {output_csv}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an evaluation dataset against a local or deployed supply-chain agent."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_DATASET,
        help="Input CSV with columns: query, ground_truth, optional category.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path. Defaults to evaluation/results/<timestamp>_results.csv",
    )
    parser.add_argument(
        "--agent-url",
        default=os.getenv("EVAL_AGENT_URL", "http://localhost:8000/copilot"),
        help="Agent endpoint URL. Examples: http://localhost:8000/copilot or Container Apps URL.",
    )
    parser.add_argument(
        "--agent-type",
        default=os.getenv("EVAL_AGENT_TYPE", "supervisor_copilot"),
        choices=["supervisor_copilot", "supervisor_chat", "inventory_invoke"],
        help="Payload/response contract used by the endpoint.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("EVAL_TIMEOUT_SECONDS", "60")),
        help="HTTP timeout per question, in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output = args.output
    if output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = DEFAULT_RESULTS_DIR / f"inventory_agent_results_{timestamp}.csv"

    run_evaluation(
        input_csv=args.input,
        output_csv=output,
        agent_url=args.agent_url,
        agent_type=args.agent_type,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    main()
