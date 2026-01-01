# src/mcp/common_server.py
"""
COMMON MCP server (mocked abilities) + in-process callable registry.

Why both?
- FastMCP server: meets "real MCP tool interface" requirement.
- In-process registry: simplest Plan A so workflow runs without starting servers.
"""

from __future__ import annotations

from typing import Any, Dict
import uuid

try:
    # FastMCP (standalone package)
    from fastmcp import FastMCP  # type: ignore
except Exception:
    # Fallback: some setups use the MCP SDK path
    from mcp.server.fastmcp import FastMCP  # type: ignore


mcp = FastMCP(name="COMMON Invoice Abilities")


# -------------------------
# Tools (MCP exposed)
# -------------------------
@mcp.tool
def accept_invoice_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and persist raw invoice payload (mock). Returns invoice_id."""
    raw = payload.get("raw_payload", {})
    invoice_id = raw.get("invoice_id") or raw.get("invoice_number") or f"INV-{uuid.uuid4().hex[:8]}"
    return {"invoice_id": invoice_id, "raw_persisted": True}


@mcp.tool
def parse_line_items(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse invoice fields + line items (mock)."""
    raw = payload.get("raw_payload", {})
    vendor_name = (raw.get("vendor") or {}).get("name") or raw.get("vendor_name") or "UNKNOWN"

    parsed_invoice = {
        "invoice_number": raw.get("invoice_number", raw.get("invoice_id")),
        "vendor_name": vendor_name,
        "amount": raw.get("amount", 0),
        "currency": raw.get("currency", "INR"),
        "po_ref": raw.get("po_ref"),
    }

    line_items = raw.get("line_items")
    if not line_items:
        line_items = [
            {
                "description": "Service",
                "quantity": 1,
                "unit_price": float(raw.get("amount", 0) or 0),
                "amount": float(raw.get("amount", 0) or 0),
            }
        ]

    return {"parsed_invoice": parsed_invoice, "line_items": line_items}


@mcp.tool
def normalize_vendor(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize vendor name (mock)."""
    vendor_name = payload.get("vendor_name") or "UNKNOWN"
    return {"normalized_name": vendor_name.strip().upper()}


@mcp.tool
def compute_flags(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Compute validation / risk flags (mock)."""
    parsed = payload.get("parsed_invoice", {}) or {}
    amount = float(parsed.get("amount", 0) or 0)
    flags = {
        "high_amount": amount > 100000,
        "missing_po": parsed.get("po_ref") in (None, "", "NA"),
    }
    return {"risk_flags": flags}


@mcp.tool
def compute_match_score(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Compute 2-way match score Invoice vs PO (mock)."""
    raw = payload.get("raw_payload", {}) or {}
    if raw.get("force_mismatch") is True:
        return {"match_score": 0.60}
    return {"match_score": 0.92}


@mcp.tool
def build_accounting_entries(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build accounting entries (mock)."""
    parsed = payload.get("parsed_invoice", {}) or {}
    amount = float(parsed.get("amount", 0) or 0)
    entries = [
        {"type": "DEBIT", "account": "Expense", "amount": amount},
        {"type": "CREDIT", "account": "Accounts Payable", "amount": amount},
    ]
    return {"entries": entries, "currency": parsed.get("currency", "INR")}


@mcp.tool
def output_final_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Produce final structured payload (mock)."""
    return {"final_payload": payload}


# -------------------------
# In-process callable registry
# -------------------------
def call_tool(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    In-process dispatcher used by LocalMCPClient (Plan A).
    """
    registry = {
        "accept_invoice_payload": accept_invoice_payload,
        "parse_line_items": parse_line_items,
        "normalize_vendor": normalize_vendor,
        "compute_flags": compute_flags,
        "compute_match_score": compute_match_score,
        "build_accounting_entries": build_accounting_entries,
        "output_final_payload": output_final_payload,
    }
    if tool_name not in registry:
        raise ValueError(f"COMMON tool not found: {tool_name}")
    return registry[tool_name](payload)


if __name__ == "__main__":
    # Default is stdio; explicitly specifying is fine too
    mcp.run()
