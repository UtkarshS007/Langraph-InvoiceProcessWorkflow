I built an Invoice Processing Agent using LangGraph that models the full workflow as a stateful graph of 12 stages—from intake and OCR through 
vendor enrichment, ERP retrieval, matching, approval, posting, and notifications. The agent persists a shared state across nodes and supports dynamic
tool selection (Bigtool) for OCR/enrichment/ERP connectors. If two-way matching fails, it creates a Human-in-the-Loop checkpoint, stores the full state
in SQLite, pauses execution, and exposes an API for accept/reject decisions. After the human decision, the workflow resumes from the checkpoint and
completes end-to-end with structured logs and a final payload.
