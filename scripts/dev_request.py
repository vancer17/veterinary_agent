from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_DIR = ROOT / "scripts" / "dev_payloads"
PAYLOADS = {
    "followup-first": PAYLOAD_DIR / "followup_first.json",
    "followup-second": PAYLOAD_DIR / "followup_second.json",
    "multitask": PAYLOAD_DIR / "multitask.json",
    "safety-toxic": PAYLOAD_DIR / "safety_toxic.json",
    "idempotency": PAYLOAD_DIR / "idempotency.json",
    "profile-memory": PAYLOAD_DIR / "profile_memory.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Send dev requests to the Vet Agent API.")
    parser.add_argument(
        "scenario",
        choices=[
            "health",
            "ready",
            "followup-first",
            "followup-second",
            "multitask",
            "safety-toxic",
            "idempotency",
            "profile-memory",
            "memory-read",
            "all",
            "print-curl",
        ],
    )
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--full", action="store_true", help="Print full JSON responses.")
    args = parser.parse_args()

    if args.scenario == "print-curl":
        print_curl_examples(args.base_url)
        return

    import httpx

    with httpx.Client(base_url=args.base_url, timeout=60.0, headers=headers()) as client:
        if args.scenario == "all":
            for scenario in (
                "health",
                "ready",
                "followup-first",
                "followup-second",
                "multitask",
                "safety-toxic",
                "idempotency",
                "profile-memory",
                "memory-read",
            ):
                run_scenario(client, scenario, full=args.full)
            return
        run_scenario(client, args.scenario, full=args.full)


def run_scenario(client: httpx.Client, scenario: str, *, full: bool) -> None:
    print(f"\n=== {scenario} ===")
    if scenario == "health":
        print_response(client.get("/health"), full=full)
        return
    if scenario == "ready":
        print_response(client.get("/ready"), full=full)
        return
    if scenario == "idempotency":
        payload = load_payload(PAYLOADS[scenario])
        first = client.post("/agent/turns", json=payload)
        second = client.post("/agent/turns", json=payload)
        print_response(first, full=full)
        first_json = safe_json(first)
        second_json = safe_json(second)
        print(
            json.dumps(
                {
                    "second_status_code": second.status_code,
                    "first_turn_id": first_json.get("id"),
                    "second_turn_id": second_json.get("id"),
                    "same_turn_id": first_json.get("id") == second_json.get("id"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if scenario == "profile-memory":
        response = client.post("/agent/turns", json=load_payload(PAYLOADS[scenario]))
        print_response(response, full=full)
        memory_read(client, full=full)
        return
    if scenario == "memory-read":
        memory_read(client, full=full)
        return

    payload_path = PAYLOADS[scenario]
    print(f"payload: {payload_path.relative_to(ROOT)}")
    print_response(client.post("/agent/turns", json=load_payload(payload_path)), full=full)


def memory_read(client: httpx.Client, *, full: bool) -> None:
    response = client.get(
        "/memories",
        params={
            "user_id": "dev_user_memory",
            "session_id": "dev_session_memory",
            "pet_id": "dev_pet_memory",
        },
    )
    print_response(response, full=full)


def print_response(response: httpx.Response, *, full: bool) -> None:
    body = safe_json(response)
    print(f"HTTP {response.status_code}")
    if full:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return

    summary: dict[str, Any] = {
        "id": body.get("id"),
        "status": body.get("status") or body.get("code") or body.get("checks") or body.get("status_code"),
        "route": (body.get("vet_result") or {}).get("route"),
        "segment_count": len(body.get("segments") or []),
        "task_count": (body.get("metadata") or {}).get("task_count"),
        "stored_fact_count": ((body.get("metadata") or {}).get("memory_extraction") or {}).get("stored_fact_count"),
        "safety_signals": [item.get("code") for item in body.get("safety_signals") or []],
        "output_text": truncate(str(body.get("output_text") or body.get("message") or body), 700),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def headers() -> dict[str, str]:
    result = {"Content-Type": "application/json"}
    api_key = (
        os.getenv("VET_AGENT_DEV_API_KEY")
        or os.getenv("API_KEY")
        or first_csv_value(os.getenv("VET_AGENT_API_KEYS", ""))
    )
    if api_key:
        result["Authorization"] = f"Bearer {api_key}"
    return result


def first_csv_value(value: str) -> str | None:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values[0] if values else None


def load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return {"status_code": response.status_code, "text": response.text}
    return data if isinstance(data, dict) else {"value": data}


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def print_curl_examples(base_url: str) -> None:
    print("# Health")
    print(f"curl.exe {base_url}/health")
    print()
    print("# First follow-up turn")
    print(
        f"curl.exe -X POST {base_url}/agent/turns "
        "-H \"Content-Type: application/json\" "
        "--data-binary \"@scripts/dev_payloads/followup_first.json\""
    )
    print()
    print("# Second turn with the same session_id + pet_id")
    print(
        f"curl.exe -X POST {base_url}/agent/turns "
        "-H \"Content-Type: application/json\" "
        "--data-binary \"@scripts/dev_payloads/followup_second.json\""
    )
    print()
    print("# Multi-task routing")
    print(
        f"curl.exe -X POST {base_url}/agent/turns "
        "-H \"Content-Type: application/json\" "
        "--data-binary \"@scripts/dev_payloads/multitask.json\""
    )
    print()
    print("# Safety escalation")
    print(
        f"curl.exe -X POST {base_url}/agent/turns "
        "-H \"Content-Type: application/json\" "
        "--data-binary \"@scripts/dev_payloads/safety_toxic.json\""
    )
    print()
    print("# Idempotency check, run it twice")
    print(
        f"curl.exe -X POST {base_url}/agent/turns "
        "-H \"Content-Type: application/json\" "
        "--data-binary \"@scripts/dev_payloads/idempotency.json\""
    )


if __name__ == "__main__":
    main()
