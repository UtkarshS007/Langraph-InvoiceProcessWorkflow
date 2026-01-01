# src/mcp/atlas_server.py
"""
ATLAS MCP server (mocked external abilities) + in-process callable registry.
"""

from __future__ import annotations

from typing import Any, Dict
import uuid
from datetime import datetime, timezone

try:
    from fastmcp import FastMCP  # type: ignore
except Exception:
    from mcp.server.fastmcp import FastMCP  # type: ignore


mcp = FastMCP(name="ATLAS Invoice Abilities")


@mcp.tool
def ocr_extract(payload: Dict[str, Any]) -> Dict[str, Any]:
    """OCR extraction on invoice attachments (mock)."""
    provider = payload.get("selected_tool") or "tesseract"
    return {"ocr_text": f"[OCR via {provider}] Extracted invoice text successfully."}


@mcp.tool
def enrich_vendor(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Vendor enrichment (mock)."""
    provider = payload.get("selected_tool") or "vendor_db"
    normalized = payload.get("normalized_name") or "UNKNOWN"
    return {
        "enrichment_source": provider,
        "tax_id": "TAX-XXXX",
        "gst_id": "GST-XXXX",
        "pan_id": "PAN-XXXX",
        "credit_score": 0.78,
        "risk_score": 0.25,
        "enrichment_data": {"provider": provider, "vendor_key": normalized},
    }


@mcp.tool
def fetch_po(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch PO from ERP (mock)."""
    connector = payload.get("selected_tool") or "sap_connector"
    po_ref = payload.get("po_ref")
    return {"po": {"po_ref": po_ref, "total": 1000, "connector": connector}}


@mcp.tool
def fetch_grn(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch GRN from ERP (mock)."""
    connector = payload.get("selected_tool") or "sap_connector"
    po_ref = payload.get("po_ref")
    return {"grn": {"po_ref": po_ref, "received": True, "connector": connector}}


@mcp.tool
def fetch_history(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch invoice history (mock)."""
    connector = payload.get("selected_tool") or "sap_connector"
    return {"history": [{"invoice_id": "INV-OLD-1", "amount": 950, "connector": connector}]}


@mcp.tool
def apply_invoice_approval_policy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Approval policy evaluation (mock)."""
    amount = float(payload.get("amount", 0) or 0)
    if amount > 250000:
        return {
            "approved": False,
            "escalation_required": True,
            "reason": "Amount exceeds auto-approve limit",
            "approver_role": "FINANCE_MANAGER",
        }
    return {"approved": True, "escalation_required": False, "reason": "Auto-approved", "approver_role": "SYSTEM"}


@mcp.tool
def post_to_erp(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Post invoice to ERP/AP (mock)."""
    return {"posted": True, "erp_invoice_id": f"ERP-{uuid.uuid4().hex[:6]}"}


@mcp.tool
def schedule_payment(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule payment (mock)."""
    return {"payment_scheduled": True, "payment_date": datetime.now(timezone.utc).date().isoformat()}


@mcp.tool
def notify_vendor(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Notify vendor (mock)."""
    return {"vendor_notified": True}


@mcp.tool
def notify_finance_team(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Notify finance team (mock)."""
    return {"finance_notified": True}


@mcp.tool
def accept_or_reject_invoice(payload: Dict[str, Any]) -> Dict[str, Any]:
    """HITL decision tool (mock placeholder)."""
    return {"ok": True}


def call_tool(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    registry = {
        "ocr_extract": ocr_extract,
        "enrich_vendor": enrich_vendor,
        "fetch_po": fetch_po,
        "fetch_grn": fetch_grn,
        "fetch_history": fetch_history,
        "apply_invoice_approval_policy": apply_invoice_approval_policy,
        "post_to_erp": post_to_erp,
        "schedule_payment": schedule_payment,
        "notify_vendor": notify_vendor,
        "notify_finance_team": notify_finance_team,
        "accept_or_reject_invoice": accept_or_reject_invoice,
    }
    if tool_name not in registry:
        raise ValueError(f"ATLAS tool not found: {tool_name}")
    return registry[tool_name](payload)


if __name__ == "__main__":
    mcp.run()
