# demo/run_demo.py
"""
End-to-end demo runner.

What it demonstrates:
1) main workflow runs from INTAKE -> MATCH_TWO_WAY
2) if match fails -> CHECKPOINT_HITL persists state to SQLite (app.db) and pauses
3) human sets ACCEPT/REJECT via FastAPI
4) resume workflow starts at HITL_DECISION and completes

Run:
- Start API in another terminal:
    uvicorn src.api.server:app --reload --port 8000

- Then run:
    python demo/run_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.graph_builder import build_graphs
from src.runner import make_runtime
from datetime import datetime



def pretty_print_logs(state: dict) -> None:
    logs = state.get("logs", [])
    print("\n================= LOGS =================")
    for i, e in enumerate(logs, start=1):
        stage = e.get("stage")
        event = e.get("event")
        msg = e.get("message")
        print(f"{i:03d}. [{stage}] {event} - {msg}")
    print("=======================================\n")

def save_demo_artifacts(prefix: str, state: dict) -> None:
    """
    Save final payload + logs to demo/ as JSON files so the demo is reviewable.
    """
    demo_dir = Path("demo")
    demo_dir.mkdir(exist_ok=True)

    final_payload = state.get("final_payload") or {}
    logs = state.get("logs") or []

    (demo_dir / f"{prefix}_output_final.json").write_text(
        json.dumps(final_payload, indent=2),
        encoding="utf-8",
    )
    (demo_dir / f"{prefix}_output_logs.json").write_text(
        json.dumps(logs, indent=2),
        encoding="utf-8",
    )

    print(f"Saved: demo/{prefix}_output_final.json")
    print(f"Saved: demo/{prefix}_output_logs.json")


def main() -> None:
    # --- Load sample input ---
    sample_path = Path("demo/sample_invoice.json")
    payload = json.loads(sample_path.read_text(encoding="utf-8"))

    # --- Build graphs (main + resume) ---
    main_app, resume_app, workflow = build_graphs(
        workflow_path="configs/workflow.json",
        checkpoint_db_path="checkpoints.sqlite",
    )

    # --- Create runtime (Bigtool + MCP client + Review DB) ---
    runtime = make_runtime(workflow, db_path="app.db")

    # IMPORTANT:
    # LangGraph checkpointers generally expect a stable thread_id.
    # We'll use invoice_id (or a fallback) as the thread_id.
    thread_id = payload.get("invoice_id", "THREAD-DEMO-001")

    # --- Initial state ---
    state = {
        "raw_payload": payload,
        "attachments": payload.get("attachments", []),
        "status": "NEW",
        "logs": [],
    }

    print("\n=== RUN #1: MAIN WORKFLOW (INTAKE -> ...) ===")
    out = main_app.invoke(
        state,
        config={
            "configurable": {
                "runtime": runtime,
                "thread_id": thread_id,   # helps checkpointing
            }
        },
    )

    print(f"\nMain workflow finished with status: {out.get('status')}")
    pretty_print_logs(out)

    # If paused, simulate HITL decision via API and resume
    if out.get("status") == "PAUSED":
        hitl_checkpoint_id = out.get("hitl_checkpoint_id")
        review_url = out.get("review_url")

        print("Workflow PAUSED for HITL review.")
        print(f"hitl_checkpoint_id: {hitl_checkpoint_id}")
        print(f"review_url: {review_url}")
        # Save paused-state artifacts (useful evidence of checkpoint persistence)
        save_demo_artifacts("paused", out)

        # --- Human decision (change to REJECT to test reject path) ---
        decision = "ACCEPT"

        print(f"\nSubmitting human decision via API: {decision}")
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"http://localhost:8000/review/{hitl_checkpoint_id}/decision",
                json={"decision": decision},
            )
            resp.raise_for_status()
            print("Decision saved:", resp.json())

        # --- Resume run ---
        # For resume, we can simply pass the paused state forward.
        # HITL_DECISION stage will reload decision from SQLite and route accordingly.
        print("\n=== RUN #2: RESUME WORKFLOW (HITL_DECISION -> ...) ===")
        resumed = resume_app.invoke(
            out,
            config={
                "configurable": {
                    "runtime": runtime,
                    "thread_id": f"{thread_id}-resume",
                }
            },
        )

        print(f"\nResume workflow finished with status: {resumed.get('status')}")
        pretty_print_logs(resumed)
        save_demo_artifacts("final", resumed)


        # Final payload check
        final_payload = resumed.get("final_payload")
        print("Final payload keys:", list(final_payload.keys()) if isinstance(final_payload, dict) else type(final_payload))

    else:
        print("Workflow did not pause (no HITL required).")
        final_payload = out.get("final_payload")
        print("Final payload keys:", list(final_payload.keys()) if isinstance(final_payload, dict) else type(final_payload))
        save_demo_artifacts("final", out)


if __name__ == "__main__":
    main()
