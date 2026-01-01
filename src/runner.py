# src/runner.py
"""
Runtime stage executor for the Invoice Processing LangGraph agent.

MVP Goals:
- Read stages + abilities from configs/workflow.json (config-driven execution)
- Execute stage abilities sequentially and update shared state
- Use Bigtool pools for OCR/enrichment/ERP/DB selections (deterministic for MVP)
- Implement HITL pause:
    - persist full state to SQLite DB (human_review_queue table)
    - generate review_url
    - set state["status"] = "PAUSED"
- Implement resume:
    - load state from DB
    - read decision (ACCEPT/REJECT)
    - set state["decision"] and allow graph to route

Important:
- This file provides execute_stage(runtime, stage_id, state, config)
  which graph_builder.py should call.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig

from src.state import InvoiceWorkflowState, ensure_defaults, log_event, new_retrieval_artifacts


# -----------------------------
# SQLite persistence (Human Review Queue)
# -----------------------------
class ReviewQueueDB:
    """
    Lightweight SQLite persistence for HITL review queue.

    Why do this?
    - Your spec requires "persist full workflow state to DB so it appears under Human Review".
    - SQLite is perfect for MVP/demo without extra infra.
    """

    def __init__(self, db_path: str = "app.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # check_same_thread=False helps if you later access from FastAPI threads
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS human_review_queue (
                    checkpoint_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    review_url TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision TEXT
                );
                """
            )
            conn.commit()

    def enqueue(self, checkpoint_id: str, state: Dict[str, Any]) -> str:
        """
        Persist state into DB and return review URL.
        """
        review_url = f"http://localhost:8000/review/{checkpoint_id}"
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO human_review_queue
                (checkpoint_id, status, review_url, state_json, created_at, decision)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (checkpoint_id, "PAUSED", review_url, json.dumps(state), now, None),
            )
            conn.commit()

        return review_url

    def get(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT checkpoint_id, status, review_url, state_json, created_at, decision "
                "FROM human_review_queue WHERE checkpoint_id = ?;",
                (checkpoint_id,),
            )
            row = cur.fetchone()

        if not row:
            return None

        return {
            "checkpoint_id": row[0],
            "status": row[1],
            "review_url": row[2],
            "state": json.loads(row[3]),
            "created_at": row[4],
            "decision": row[5],
        }

    def set_decision(self, checkpoint_id: str, decision: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE human_review_queue SET decision = ? WHERE checkpoint_id = ?;",
                (decision, checkpoint_id),
            )
            conn.commit()

    def set_status(self, checkpoint_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE human_review_queue SET status = ? WHERE checkpoint_id = ?;",
                (status, checkpoint_id),
            )
            conn.commit()


# -----------------------------
# Bigtool selector (MVP deterministic)
# -----------------------------
def select_from_pool(pool: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal deterministic Bigtool selector.

    You can improve this later, but for MVP:
    - If context requests a specific provider, honor it.
    - Else pick the first candidate.
    """
    requested = context.get("preferred_tool")
    if requested:
        for candidate in pool:
            if candidate.get("name") == requested:
                return candidate
    return pool[0]


# -----------------------------
# Ability execution (MVP local mocks)
# -----------------------------
def _mock_common_tool(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mock COMMON server abilities.
    """
    if tool_name == "accept_invoice_payload":
        raw = payload["raw_payload"]
        invoice_id = raw.get("invoice_id") or f"INV-{uuid.uuid4().hex[:8]}"
        return {"invoice_id": invoice_id, "raw_persisted": True}

    if tool_name == "parse_line_items":
        # parsing mock: if raw_payload has "line_items", use it; else create one
        raw = payload["raw_payload"]
        items = raw.get("line_items") or [{"description": "Service", "quantity": 1, "unit_price": raw.get("amount", 0), "amount": raw.get("amount", 0)}]
        parsed_invoice = {
            "invoice_number": raw.get("invoice_number", raw.get("invoice_id")),
            "vendor_name": raw.get("vendor", {}).get("name", raw.get("vendor_name", "UNKNOWN")),
            "amount": raw.get("amount", 0),
            "currency": raw.get("currency", "INR"),
            "po_ref": raw.get("po_ref"),
        }
        return {"parsed_invoice": parsed_invoice, "line_items": items}

    if tool_name == "normalize_vendor":
        vendor_name = payload.get("vendor_name", "UNKNOWN")
        normalized = vendor_name.strip().upper()
        return {"normalized_name": normalized}

    if tool_name == "compute_flags":
        parsed = payload.get("parsed_invoice", {})
        amount = float(parsed.get("amount", 0) or 0)
        flags = {
            "high_amount": amount > 100000,
            "missing_po": parsed.get("po_ref") in (None, "", "NA"),
        }
        return {"risk_flags": flags}

    if tool_name == "compute_match_score":
        # if payload has "force_mismatch" -> low score; else high score
        raw = payload.get("raw_payload", {})
        if raw.get("force_mismatch") is True:
            return {"match_score": 0.60}
        return {"match_score": 0.92}

    if tool_name == "build_accounting_entries":
        parsed = payload.get("parsed_invoice", {})
        amount = float(parsed.get("amount", 0) or 0)
        entries = [
            {"type": "DEBIT", "account": "Expense", "amount": amount},
            {"type": "CREDIT", "account": "Accounts Payable", "amount": amount},
        ]
        return {"entries": entries, "currency": parsed.get("currency", "INR")}

    if tool_name == "output_final_payload":
        # Final payload should be clean + structured
        return {"final_payload": payload}

    raise ValueError(f"Unknown COMMON tool: {tool_name}")


def _mock_atlas_tool(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mock ATLAS server abilities.
    """
    if tool_name == "ocr_extract":
        provider = payload.get("selected_tool", "tesseract")
        # Mock OCR output
        return {"ocr_text": f"[OCR via {provider}] Invoice text extracted successfully."}

    if tool_name == "enrich_vendor":
        provider = payload.get("selected_tool", "vendor_db")
        normalized = payload.get("normalized_name", "UNKNOWN")
        # Mock enrichment output
        return {
            "enrichment_source": provider,
            "tax_id": "TAX-XXXX",
            "gst_id": "GST-XXXX",
            "pan_id": "PAN-XXXX",
            "credit_score": 0.78,
            "risk_score": 0.25,
            "enrichment_data": {"provider": provider, "vendor_key": normalized},
        }

    if tool_name in ("fetch_po", "fetch_grn", "fetch_history"):
        connector = payload.get("selected_tool", "sap_connector")
        po_ref = payload.get("po_ref")

        if tool_name == "fetch_po":
            return {"po": {"po_ref": po_ref, "total": 1000, "connector": connector}}
        if tool_name == "fetch_grn":
            return {"grn": {"po_ref": po_ref, "received": True, "connector": connector}}
        if tool_name == "fetch_history":
            return {"history": [{"invoice_id": "INV-OLD-1", "amount": 950, "connector": connector}]}

    if tool_name == "apply_invoice_approval_policy":
        amount = float(payload.get("amount", 0) or 0)
        if amount > 250000:
            return {"approved": False, "escalation_required": True, "reason": "Amount exceeds auto-approve limit", "approver_role": "FINANCE_MANAGER"}
        return {"approved": True, "escalation_required": False, "reason": "Auto-approved", "approver_role": "SYSTEM"}

    if tool_name == "post_to_erp":
        return {"posted": True, "erp_invoice_id": f"ERP-{uuid.uuid4().hex[:6]}"}

    if tool_name == "schedule_payment":
        return {"payment_scheduled": True, "payment_date": datetime.now(timezone.utc).date().isoformat()}

    if tool_name == "notify_vendor":
        return {"vendor_notified": True}

    if tool_name == "notify_finance_team":
        return {"finance_notified": True}

    if tool_name == "accept_or_reject_invoice":
        # In MVP, decision is read from DB during resume, so this tool can be a no-op.
        return {"ok": True}

    raise ValueError(f"Unknown ATLAS tool: {tool_name}")


# -----------------------------
# Runtime container
# -----------------------------
@dataclass
class Runtime:
    """
    Holds workflow config + persistence + future MCP/bigtool integrations.
    """
    workflow: Dict[str, Any]
    review_db: ReviewQueueDB

    # flags for later:
    use_real_mcp: bool = False


def make_runtime(workflow: Dict[str, Any], db_path: str = "app.db") -> Runtime:
    """
    Construct runtime. Keeps DB + config accessible from node execution.
    """
    return Runtime(workflow=workflow, review_db=ReviewQueueDB(db_path=db_path))


# -----------------------------
# Main stage executor (called by graph_builder node wrapper)
# -----------------------------
def execute_stage(runtime: Runtime, stage_id: str, state: InvoiceWorkflowState, config: RunnableConfig) -> InvoiceWorkflowState:
    """
    Execute one stage defined in workflow.json:
    - Run its abilities in order
    - Use Bigtool pool selection when required
    - Update state fields
    - Handle HITL checkpoint stage (pause + persist + review_url)
    """
    state = ensure_defaults(state)

    workflow = runtime.workflow
    stages = workflow.get("stages", [])
    abilities_cfg = workflow.get("abilities", {})
    pools = workflow.get("bigtool", {}).get("pools", {})
    globals_cfg = workflow.get("globals", {})

    # Find this stage's definition
    stage_def = next((s for s in stages if s.get("id") == stage_id), None)
    if not stage_def:
        raise ValueError(f"Stage '{stage_id}' not found in workflow.json")

    # Special handling: CHECKPOINT_HITL stage must pause and persist state
    if stage_id == "CHECKPOINT_HITL":
        checkpoint_id = state.get("checkpoint_id") or uuid.uuid4().hex
        state["checkpoint_id"] = checkpoint_id
        state["status"] = globals_cfg.get("hitl", {}).get("pause_status", "PAUSED")

        # Persist full state into human review DB
        review_url = runtime.review_db.enqueue(checkpoint_id, dict(state))
        state["review_url"] = review_url

        # Log what happened
        log_event(
            state,
            stage=stage_id,
            event="checkpoint_created",
            message="Checkpoint created and state persisted for human review",
            checkpoint_id=checkpoint_id,
            review_url=review_url,
        )

        return state

    # Execute abilities for normal stages
    stage_abilities: List[str] = stage_def.get("abilities", [])
    for ability_name in stage_abilities:
        if ability_name not in abilities_cfg:
            raise ValueError(f"Ability '{ability_name}' not defined in workflow.json abilities")

        ability = abilities_cfg[ability_name]
        server = ability.get("server")  # COMMON or ATLAS
        tool = ability.get("tool")      # tool name on that server
        pool_name = ability.get("bigtool_pool")

        selected_tool_name: Optional[str] = None
        if pool_name:
            pool_list = pools.get(pool_name, [])
            if not pool_list:
                raise ValueError(f"Bigtool pool '{pool_name}' is empty/missing in workflow.json")

            # Minimal context for selection (you can enrich this later)
            context = {
                "stage": stage_id,
                "ability": ability_name,
                "raw_payload": state.get("raw_payload", {}),
                "attachments": state.get("attachments", []),
            }
            selected = select_from_pool(pool_list, context)
            selected_tool_name = selected.get("name")

            log_event(
                state,
                stage=stage_id,
                event="bigtool_select",
                message=f"Selected tool '{selected_tool_name}' from pool '{pool_name}'",
                pool=pool_name,
                selected_tool=selected_tool_name,
                candidates=[c.get("name") for c in pool_list],
            )

        # Prepare a payload for tool execution based on current state
        tool_payload = _build_tool_payload(stage_id, ability_name, state, selected_tool_name)

        log_event(
            state,
            stage=stage_id,
            event="ability_call",
            message=f"Calling {server}.{tool}",
            server=server,
            tool=tool,
            ability=ability_name,
            selected_tool=selected_tool_name,
        )

        # Call the tool (MVP = local mocks)
        if server == "COMMON":
            result = _mock_common_tool(tool, tool_payload)
        elif server == "ATLAS":
            result = _mock_atlas_tool(tool, tool_payload)
        else:
            raise ValueError(f"Unknown server '{server}' for ability '{ability_name}'")

        log_event(
            state,
            stage=stage_id,
            event="ability_result",
            message=f"Result received from {server}.{tool}",
            result_keys=list(result.keys()),
        )

        # Apply tool result into shared state (the important part)
        _apply_result_to_state(stage_id, ability_name, state, result)

    # After MATCH_TWO_WAY compute_match_score, set needs_hitl bool for graph routing
    if stage_id == "MATCH_TWO_WAY":
        threshold = float(globals_cfg.get("match_threshold", 0.85))
        match_score = float(state.get("match_score", 0.0))
        state["needs_hitl"] = match_score < threshold

        log_event(
            state,
            stage=stage_id,
            event="match_evaluated",
            message="Computed needs_hitl based on match_threshold",
            match_score=match_score,
            threshold=threshold,
            needs_hitl=state["needs_hitl"],
        )

    # HITL_DECISION stage:
    # In a real setup, this is triggered after a human acts in the UI.
    # For MVP: read decision from DB using checkpoint_id and store into state.
    if stage_id == "HITL_DECISION":
        checkpoint_id = state.get("checkpoint_id")
        if not checkpoint_id:
            raise ValueError("HITL_DECISION requires state['checkpoint_id']")

        row = runtime.review_db.get(checkpoint_id)
        if not row:
            raise ValueError(f"No review queue record found for checkpoint_id={checkpoint_id}")

        decision = row.get("decision")
        state["decision"] = decision  # "ACCEPT" or "REJECT"

        log_event(
            state,
            stage=stage_id,
            event="hitl_decision_loaded",
            message="Loaded human decision from DB",
            checkpoint_id=checkpoint_id,
            decision=decision,
        )

        # If REJECT, set status to manual handling early
        if decision == "REJECT":
            state["status"] = globals_cfg.get("hitl", {}).get("reject_status", "REQUIRES_MANUAL_HANDLING")

    # COMPLETE stage: mark completed
    if stage_id == "COMPLETE":
        if state.get("status") not in ("REQUIRES_MANUAL_HANDLING", "FAILED"):
            state["status"] = "COMPLETED"

    return state


# -----------------------------
# Tool payload building and state updates
# -----------------------------
def _build_tool_payload(stage_id: str, ability_name: str, state: InvoiceWorkflowState, selected_tool: Optional[str]) -> Dict[str, Any]:
    """
    Build the payload you send to an ability/tool.
    Keep it explicit so reviewers see you are deliberately managing state.
    """
    raw = state.get("raw_payload", {})
    parsed_invoice = state.get("parsed_invoice", {})
    vendor = state.get("vendor", {})

    payload: Dict[str, Any] = {
        "raw_payload": raw,
        "attachments": state.get("attachments", []),
        "ocr_text": state.get("ocr_text"),
        "parsed_invoice": parsed_invoice,
        "line_items": state.get("line_items", []),
        "vendor_name": (raw.get("vendor", {}) or {}).get("name") or raw.get("vendor_name"),
        "normalized_name": vendor.get("normalized_name"),
        "po_ref": raw.get("po_ref") or parsed_invoice.get("po_ref"),
        "amount": parsed_invoice.get("amount") or raw.get("amount"),
        "selected_tool": selected_tool,
    }

    return payload


def _apply_result_to_state(stage_id: str, ability_name: str, state: InvoiceWorkflowState, result: Dict[str, Any]) -> None:
    """
    Map each ability result into the shared state keys.
    This is where "state management" becomes explicit and reviewable.
    """
    # INTAKE
    if ability_name == "accept_invoice_payload":
        state["invoice_id"] = result["invoice_id"]

    # UNDERSTAND
    if ability_name == "ocr_extract":
        state["ocr_text"] = result.get("ocr_text", "")

    if ability_name == "parse_line_items":
        state["parsed_invoice"] = result.get("parsed_invoice", {})
        state["line_items"] = result.get("line_items", [])

        # Initialize vendor profile if not present
        if "vendor" not in state:
            raw_name = state["parsed_invoice"].get("vendor_name", "UNKNOWN")
            state["vendor"] = {"raw_name": raw_name}

    # PREPARE
    if ability_name == "normalize_vendor":
        state.setdefault("vendor", {})
        state["vendor"]["normalized_name"] = result.get("normalized_name")

    if ability_name == "enrich_vendor":
        state.setdefault("vendor", {})
        # Merge enrichment fields
        for k, v in result.items():
            state["vendor"][k] = v

    if ability_name == "compute_flags":
        state["risk_flags"] = result.get("risk_flags", {})

    # RETRIEVE
    if ability_name in ("fetch_po", "fetch_grn", "fetch_history"):
        if "retrieval" not in state:
            state["retrieval"] = new_retrieval_artifacts()

        # Merge the returned dict into retrieval
        # e.g. {"po": {...}} or {"grn": {...}} or {"history": [...]}
        for k, v in result.items():
            state["retrieval"][k] = v

    # MATCH
    if ability_name == "compute_match_score":
        state["match_score"] = float(result.get("match_score", 0.0))

    # RECONCILE
    if ability_name == "build_accounting_entries":
        state["accounting_entries"] = {
            "entries": result.get("entries", []),
            "currency": result.get("currency", "INR"),
        }

    # APPROVE
    if ability_name == "apply_invoice_approval_policy":
        state["approval_result"] = result

    # POSTING
    if ability_name == "post_to_erp":
        state.setdefault("erp_post_result", {})
        state["erp_post_result"].update(result)

    if ability_name == "schedule_payment":
        state.setdefault("erp_post_result", {})
        state["erp_post_result"].update(result)

    # NOTIFY (keep as metadata; you can store these in a notify_result field later)
    if ability_name in ("notify_vendor", "notify_finance_team"):
        # Minimal audit trail
        state.setdefault("notify_result", {})
        state["notify_result"][ability_name] = result

    # COMPLETE
    if ability_name == "output_final_payload":
        state["final_payload"] = result.get("final_payload", {})


# -----------------------------
# Convenience helpers
# -----------------------------
def resume_state_from_checkpoint(runtime: Runtime, checkpoint_id: str) -> InvoiceWorkflowState:
    """
    Load the persisted state from human review queue DB.
    This is what you'll pass into resume_app.invoke(...).
    """
    row = runtime.review_db.get(checkpoint_id)
    if not row:
        raise ValueError(f"No checkpoint found for checkpoint_id={checkpoint_id}")

    state = row["state"]
    state = ensure_defaults(state)  # type: ignore
    # Ensure checkpoint_id and review_url present in state
    state["checkpoint_id"] = checkpoint_id
    state["review_url"] = row["review_url"]
    # Keep status as PAUSED until decision is applied
    state.setdefault("status", "PAUSED")

    log_event(
        state,
        stage="RESUME",
        event="resume_loaded",
        message="Loaded state from DB for resuming workflow",
        checkpoint_id=checkpoint_id,
    )
    return state  # type: ignore
