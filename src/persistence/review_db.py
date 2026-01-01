# src/persistence/review_db.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class ReviewQueueDB:
    """
    SQLite-backed Human Review queue.

    Stores:
    - checkpoint_id (primary key)
    - status (PAUSED/DECIDED/etc.)
    - review_url
    - full workflow state as JSON (state_json)
    - decision (ACCEPT/REJECT)
    """

    def __init__(self, db_path: str = "app.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
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

    def enqueue(self, checkpoint_id: str, state: Dict[str, Any], review_url_base: str = "http://localhost:8000") -> str:
        review_url = f"{review_url_base}/review/{checkpoint_id}"
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
                "UPDATE human_review_queue SET decision = ?, status = ? WHERE checkpoint_id = ?;",
                (decision, "DECIDED", checkpoint_id),
            )
            conn.commit()
