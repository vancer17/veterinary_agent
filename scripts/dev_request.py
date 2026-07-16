from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_DIR = ROOT / "scripts" / "dev_payloads"
PAYLOADS = {
    "followup-first": PAYLOAD_DIR / "followup_first.json",
    "followup-second": PAYLOAD_DIR / "followup_second.json",
    "multitask": PAYLOAD_DIR / "multitask.json",
    "safety-toxic": PAYLOAD_DIR / "safety_toxic.json",
    "idempotency": PAYLOAD_DIR / "idempotency.json",
    "profile-memory": PAYLOAD_DIR / "profile_memory.json",
    "report-parse": PAYLOAD_DIR / "report_lab.json",
    "business-followup-first": PAYLOAD_DIR / "business_followup_first.json",
    "business-followup-second": PAYLOAD_DIR / "business_followup_second.json",
    "business-multitask": PAYLOAD_DIR / "business_multitask.json",
    "business-memory": PAYLOAD_DIR / "business_memory.json",
    "business-safety-semantic": PAYLOAD_DIR / "business_safety_semantic.json",
    "business-stream": PAYLOAD_DIR / "business_stream.json",
}

BUSINESS_SCENARIOS = (
    "business-followup-first",
    "business-followup-second",
    "business-multitask",
    "business-memory",
    "business-safety-semantic",
    "business-stream",
)


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
            "report-parse",
            "rag-stats",
            "rag-chunks",
            *BUSINESS_SCENARIOS,
            "business-all",
            "all",
            "print-curl",
        ],
    )
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--full", action="store_true", help="Print full JSON responses.")
    parser.add_argument(
        "--run-id",
        default=os.getenv("BUSINESS_RUN_ID"),
        help="Suffix business user/session/pet IDs so scenarios can be rerun without old state.",
    )
    args = parser.parse_args()

    if args.scenario == "print-curl":
        print_curl_examples(args.base_url)
        return

    import httpx

    with httpx.Client(base_url=args.base_url, timeout=60.0, headers=headers()) as client:
        if args.scenario == "business-all":
            business_run_id = args.run_id or uuid4().hex[:10]
            print(f"business_run_id: {business_run_id}")
            for scenario in BUSINESS_SCENARIOS:
                run_scenario(client, scenario, full=args.full, business_run_id=business_run_id)
            return
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
                "report-parse",
                "rag-stats",
                "rag-chunks",
            ):
                run_scenario(client, scenario, full=args.full)
            return
        run_scenario(client, args.scenario, full=args.full, business_run_id=args.run_id)


def run_scenario(
    client: httpx.Client,
    scenario: str,
    *,
    full: bool,
    business_run_id: str | None = None,
) -> None:
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
    if scenario == "business-memory":
        payload = scenario_payload(scenario, business_run_id)
        response = client.post("/agent/turns", json=payload)
        print_response(response, full=full)
        business_memory_read(client, payload)
        return
    if scenario == "business-stream":
        stream_response(client, scenario_payload(scenario, business_run_id), full=full)
        return
    if scenario == "report-parse":
        response = client.post("/reports/parse", json=load_payload(PAYLOADS[scenario]))
        print_response(response, full=full)
        return
    if scenario == "memory-read":
        memory_read(client, full=full)
        return
    if scenario == "rag-stats":
        print_response(client.get("/admin/rag/stats"), full=full)
        return
    if scenario == "rag-chunks":
        print_response(client.get("/admin/rag/chunks", params={"limit": 5}), full=full)
        return

    payload_path = PAYLOADS[scenario]
    print(f"payload: {payload_path.relative_to(ROOT)}")
    payload = scenario_payload(scenario, business_run_id)
    print_response(client.post("/agent/turns", json=payload), full=full)


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


def business_memory_read(client: httpx.Client, payload: dict[str, Any]) -> None:
    vet_context = payload["vet_context"]
    response = client.get(
        "/memories",
        params={
            "user_id": vet_context["user_id"],
            "session_id": vet_context["session_id"],
            "pet_id": vet_context["pet_id"],
        },
    )
    body = safe_json(response)
    pet = body.get("pet") or {}
    facts = [
        {
            "fact_type": item.get("fact_type"),
            "fact_key": item.get("fact_key"),
            "fact_value": item.get("fact_value"),
            "confidence": item.get("confidence"),
        }
        for item in pet.get("facts") or []
    ]
    semantic_memories = [
        {
            "memory": truncate(str(item.get("memory") or ""), 240),
            "score": item.get("score"),
        }
        for item in pet.get("semantic_memories") or []
    ]
    print(
        json.dumps(
            {
                "memory_http_status": response.status_code,
                "fact_count": len(facts),
                "facts": facts,
                "semantic_memory_count": len(semantic_memories),
                "semantic_memories": semantic_memories,
                "semantic_memory_error": pet.get("semantic_memory_error"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def stream_response(client: httpx.Client, payload: dict[str, Any], *, full: bool) -> None:
    with client.stream("POST", "/agent/turns", json=payload) as response:
        body = response.read().decode("utf-8", errors="replace")
    event_names = [
        line.removeprefix("event:").strip()
        for line in body.splitlines()
        if line.startswith("event:")
    ]
    event_counts = {event: event_names.count(event) for event in dict.fromkeys(event_names)}
    result = {
        "http_status": response.status_code,
        "event_counts": event_counts,
        "has_reasoning_display": "reasoning_display.completed" in event_counts,
        "has_segment_output": "segment.delta" in event_counts,
        "completed": "turn.completed" in event_counts,
    }
    if full:
        result["raw_sse"] = body
    print(json.dumps(result, ensure_ascii=False, indent=2))


def print_response(response: httpx.Response, *, full: bool) -> None:
    body = safe_json(response)
    print(f"HTTP {response.status_code}")
    if full:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return

    metadata = body.get("metadata") or {}
    reasoning = body.get("reasoning_display") or {}
    summary: dict[str, Any] = {
        "id": body.get("id"),
        "status": body.get("status") or body.get("code") or body.get("checks") or body.get("status_code"),
        "route": (body.get("vet_result") or {}).get("route"),
        "segment_count": len(body.get("segments") or []),
        "task_count": metadata.get("task_count"),
        "consultation_phase": metadata.get("consultation_phase"),
        "missing_slots": metadata.get("missing_slots"),
        "multi_agent_path": metadata.get("multi_agent_path"),
        "stored_fact_count": (metadata.get("memory_extraction") or {}).get("stored_fact_count"),
        "safety_signals": [item.get("code") for item in body.get("safety_signals") or []],
        "reasoning_display": truncate(str(reasoning.get("text") or ""), 500),
        "report_id": body.get("report_id"),
        "item_count": len(body.get("items") or []),
        "backend": body.get("backend"),
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


def scenario_payload(scenario: str, business_run_id: str | None) -> dict[str, Any]:
    payload = load_payload(PAYLOADS[scenario])
    if not scenario.startswith("business-") or not business_run_id:
        return payload

    safe_run_id = "".join(character for character in business_run_id if character.isalnum() or character in "-_")
    if not safe_run_id:
        raise ValueError("business run id must contain letters, digits, '-' or '_'")
    vet_context = payload["vet_context"]
    for field in ("user_id", "session_id", "pet_id"):
        vet_context[field] = f"{vet_context[field]}_{safe_run_id}"
    payload.setdefault("metadata", {})["business_run_id"] = safe_run_id
    return payload


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
    print(f"curl {base_url}/health")
    print()
    print("# First follow-up turn")
    print(
        f"curl -X POST {base_url}/agent/turns \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/followup_first.json\""
    )
    print()
    print("# Second turn with the same session_id + pet_id")
    print(
        f"curl -X POST {base_url}/agent/turns \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/followup_second.json\""
    )
    print()
    print("# Multi-task routing")
    print(
        f"curl -X POST {base_url}/agent/turns \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/multitask.json\""
    )
    print()
    print("# Safety escalation")
    print(
        f"curl -X POST {base_url}/agent/turns \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/safety_toxic.json\""
    )
    print()
    print("# Idempotency check, run it twice")
    print(
        f"curl -X POST {base_url}/agent/turns \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/idempotency.json\""
    )
    print()
    print("# Report parsing")
    print(
        f"curl -X POST {base_url}/reports/parse \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  --data-binary \"@scripts/dev_payloads/report_lab.json\""
    )
    print()
    print("# RAG governance stats")
    print(f"curl {base_url}/admin/rag/stats")


if __name__ == "__main__":
    main()
