# src/api/server.py
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Literal

from src.persistence.review_db import ReviewQueueDB

app = FastAPI(title="Invoice HITL Review API")

db = ReviewQueueDB(db_path="app.db")


class DecisionBody(BaseModel):
    decision: Literal["ACCEPT", "REJECT"]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/review/{checkpoint_id}")
def get_review(checkpoint_id: str):
    row = db.get(checkpoint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return {
        "checkpoint_id": row["checkpoint_id"],
        "status": row["status"],
        "review_url": row["review_url"],
        "created_at": row["created_at"],
        "decision": row["decision"],
        # For our UI we may want a redacted view; for demo we return full state:
        "state": row["state"],
    }


@app.post("/review/{checkpoint_id}/decision")
def set_decision(checkpoint_id: str, body: DecisionBody):
    row = db.get(checkpoint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    db.set_decision(checkpoint_id, body.decision)
    return {"checkpoint_id": checkpoint_id, "decision": body.decision, "status": "DECIDED"}
