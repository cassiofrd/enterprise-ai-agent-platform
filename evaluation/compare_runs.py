from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import mean


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def truthy(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def summarize(path: Path) -> dict[str, object]:
    rows = load_rows(path)
    total = len(rows)
    passed = sum(1 for row in rows if truthy(row.get("contains_ground_truth", "")))
    errors = sum(1 for row in rows if row.get("error"))

    latencies = []
    for row in rows:
        try:
            latencies.append(float(row.get("latency_ms") or 0))
        except ValueError:
            pass

    by_category: dict[str, dict[str, int]] = {}
    for row in rows:
        category = row.get("category") or "uncategorized"
        stats = by_category.setdefault(category, {"total": 0, "passed": 0})
        stats["total"] += 1
        if truthy(row.get("contains_ground_truth", "")):
            stats["passed"] += 1

    return {
        "path": str(path),
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0,
        "errors": errors,
        "avg_latency_ms": mean(latencies) if latencies else 0,
        "by_category": by_category,
    }


def print_summary(summary: dict[str, object]) -> None:
    print(f"\nFile: {summary['path']}")
    print(f"Total: {summary['total']}")
    print(f"Passed: {summary['passed']}")
    print(f"Pass rate: {summary['pass_rate']:.1%}")
    print(f"Errors: {summary['errors']}")
    print(f"Avg latency: {summary['avg_latency_ms']:.2f} ms")

    print("\nBy category:")
    for category, stats in sorted(summary["by_category"].items()):
        total = stats["total"]
        passed = stats["passed"]
        rate = passed / total if total else 0
        print(f"- {category}: {passed}/{total} ({rate:.1%})")


def print_failed_cases(path: Path) -> None:
    rows = load_rows(path)
    failed = [
        row for row in rows
        if not truthy(row.get("contains_ground_truth", "")) or row.get("error")
    ]

    if not failed:
        print("\nNo failed cases found.")
        return

    print("\nFailed cases:")
    for index, row in enumerate(failed, start=1):
        print(f"\n[{index}] {row.get('query')}")
        print(f"Expected: {row.get('ground_truth')}")
        print(f"Actual: {row.get('response')}")
        if row.get("error"):
            print(f"Error: {row.get('error')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize one or more agent evaluation result CSV files.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--show-failures", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    for file in args.files:
        summary = summarize(file)
        print_summary(summary)
        if args.show_failures:
            print_failed_cases(file)


if __name__ == "__main__":
    main()
