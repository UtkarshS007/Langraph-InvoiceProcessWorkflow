# src/state.py
"""
Workflow state schema for the Invoice Processing LangGraph agent.

Design goals:
- State is JSON-serializable (for DB + checkpoints).
- Works naturally with LangGraph (dict-like state).
- Strong typing via TypedDict + Pydantic models for nested structures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


# -----------------------------
# Structured log events
# -----------------------------
class LogEvent(BaseModel):
    """
    One structured log line emitted by a node or tool call.
    """
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stage: str
    event: str  # e.g. "stage_start", "ability_call", "bigtool_select", "stage_end"
    message: str

    # Store structured metadata for debugging/auditing
    data: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------
# Key data structures (nested)
# -----------------------------
class Attachment(BaseModel):
    """
    Attachment metadata. For MVP, keep it simple.
    You can add fields like bytes/base64 later if needed.
    """
    filename: str
    content_type: str = "application/pdf"
    # Optional local path (demo) or external URL (real systems)
    path: Optional[str] = None
    url: Optional[str] = None


class LineItem(BaseModel):
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    sku: Optional[str] = None
    po_line_ref: Optional[str] = None


class VendorProfile(BaseModel):
    raw_name: str
    normalized_name: Optional[str] = None

    # Enrichment fields (PAN/GST/TaxID etc.)
    tax_id: Optional[str] = None
    gst_id: Optional[str] = None
    pan_id: Optional[str] = None

    # Scores (mocked for MVP)
    credit_score: Optional[float] = None
    risk_score: Optional[float] = None

    # Additional enrichment attributes (free-form)
    enrichment_source: Optional[str] = None
    enrichment_data: Dict[str, Any] = Field(default_factory=dict)


class RetrievalArtifacts(BaseModel):
    """
    ERP artifacts fetched during RETRIEVE stage.
    Keep as dicts for maximum flexibility in MVP.
    """
    po: Optional[Dict[str, Any]] = None
    grn: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)


class AccountingEntries(BaseModel):
    """
    Output of RECONCILE stage.
    """
    entries: List[Dict[str, Any]] = Field(default_factory=list)
    currency: str = "INR"


class ApprovalResult(BaseModel):
    """
    Output of APPROVE stage.
    """
    approved: bool = False
    reason: Optional[str] = None
    escalation_required: bool = False
    approver_role: Optional[str] = None


class ERPPostResult(BaseModel):
    """
    Output of POSTING stage.
    """
    posted: bool = False
    erp_invoice_id: Optional[str] = None
    payment_scheduled: bool = False
    payment_date: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------
# Main state (LangGraph-friendly)
# -----------------------------
DecisionType = Optional[Literal["ACCEPT", "REJECT"]]
WorkflowStatus = Literal[
    "NEW",
    "IN_PROGRESS",
    "PAUSED",
    "REQUIRES_MANUAL_HANDLING",
    "COMPLETED",
    "FAILED"
]


class InvoiceWorkflowState(TypedDict, total=False):
    """
    The shared state object carried across all LangGraph nodes.

    total=False makes fields optional at first; nodes fill them in as they execute.
    All values MUST be JSON-serializable so we can persist to DB/checkpoints.
    """

    # --- Identity / input ---
    invoice_id: str
    raw_payload: Dict[str, Any]
    attachments: List[Dict[str, Any]]  # serialized Attachment models

    # --- UNDERSTAND outputs ---
    ocr_text: str
    parsed_invoice: Dict[str, Any]
    line_items: List[Dict[str, Any]]  # serialized LineItem models

    # --- PREPARE outputs ---
    vendor: Dict[str, Any]  # serialized VendorProfile model
    risk_flags: Dict[str, Any]

    # --- RETRIEVE outputs ---
    retrieval: Dict[str, Any]  # serialized RetrievalArtifacts model

    # --- MATCH outputs ---
    match_score: float
    needs_hitl: bool

    # --- HITL checkpoint fields ---
    hitl_checkpoint_id: str
    review_url: str
    decision: DecisionType

    # --- Downstream outputs ---
    accounting_entries: Dict[str, Any]  # serialized AccountingEntries model
    approval_result: Dict[str, Any]  # serialized ApprovalResult model
    erp_post_result: Dict[str, Any]  # serialized ERPPostResult model

    # --- Workflow meta ---
    status: WorkflowStatus
    current_stage: str

    # --- Audit logs ---
    logs: List[Dict[str, Any]]  # serialized LogEvent models


# -----------------------------
# Helper utilities
# -----------------------------
def log_event(state: InvoiceWorkflowState, stage: str, event: str, message: str, **data: Any) -> None:
    """
    Append a structured log event into the state.
    """
    if "logs" not in state:
        state["logs"] = []

    state["logs"].append(
        LogEvent(stage=stage, event=event, message=message, data=data).model_dump()
    )


def ensure_defaults(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    """
    Ensure state has minimal defaults so nodes can safely append logs and set status.
    """
    state.setdefault("status", "NEW")
    state.setdefault("logs", [])
    return state


def serialize_attachments(attachments: List[Attachment]) -> List[Dict[str, Any]]:
    return [a.model_dump() for a in attachments]


def serialize_line_items(items: List[LineItem]) -> List[Dict[str, Any]]:
    return [li.model_dump() for li in items]


def new_vendor_profile(raw_name: str) -> Dict[str, Any]:
    return VendorProfile(raw_name=raw_name).model_dump()


def new_retrieval_artifacts() -> Dict[str, Any]:
    return RetrievalArtifacts().model_dump()


def new_accounting_entries() -> Dict[str, Any]:
    return AccountingEntries().model_dump()


def new_approval_result() -> Dict[str, Any]:
    return ApprovalResult().model_dump()


def new_erp_post_result() -> Dict[str, Any]:
    return ERPPostResult().model_dump()
