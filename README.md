# Invoice Process Workflow - DETERMINISTIC LANGGRAPH Implementation

------------------------------------------------
**PROJECT STATUS** - `Ongoing` as of 02 Jan 2026
------------------------------------------------

I built an Invoice Processing Agent using LangGraph that models the full workflow as a stateful graph of **12 stages** - from intake and OCR through vendor enrichment, ERP retrieval, matching, approval, posting, and notifications. The agent persists a shared state across nodes and supports dynamic tool selection (Bigtool) for OCR/enrichment/ERP connectors. If two-way matching fails, it creates a Human-in-the-Loop checkpoint, stores the full state in SQLite, pauses execution, and exposes an API for accept/reject decisions. After the human decision, the workflow resumes from the checkpoint and completes end-to-end with structured logs and a final payload.

## Determinsitc Langgraph vs LLM-Based Langgraph (Agentic)
Before we proceed I wanted to make a brief distiction between the 2 types of Langgraph. 

*The distinction between a deterministic and an LLM-based LangGraph lies in the orchestrator of the logic: in a deterministic graph, the developer defines a fixed 'train track' where every transition is governed by hard-coded Python rules or if/else statements, ensuring 100% predictability and auditability. In contrast, an LLM-based graph operates like a 'GPS-guided vehicle,' where an LLM acts as the agentic brain at each node, dynamically reasoning through the current state to decide the next best action or tool to call. While the deterministic approach excels in high-stakes environments requiring strict compliance and reliability, the LLM-based approach is superior for handling ambiguous, unstructured tasks where the optimal path cannot be pre-defined.*

This Repo focuses on creating an Invoice Process Workflow using Determisnitic Langgraph. 

** *A similar project will follow on with Agentic Langgraph - to draw comparisions.* **


## IN DETAIL 
- I designed the pipeline as a `LangGraph State Machine`, where each workflow step is a node and all nodes share a single persistent state object (invoice metadata, OCR text, parsed line items, vendor profile, ERP artifacts, match score, decisions, and logs).
- The graph starts with `INTAKE`, validating and persisting the raw payload. In `UNDERSTAND`, the agent performs OCR on attachments and then parses structured fields like line items and PO references.
- Next, the `PREPARE` stage normalizes the vendor name, enriches vendor details like tax IDs and risk/credit scores, and computes validation flags. In `RETRIEVE`, the agent fetches PO/GRN and invoice history via an ERP connector.
- For parts where multiple providers can do the same job - OCR engines, enrichment sources, or ERP connectors - I implemented a Bigtool router that selects the best tool from a pool at runtime and logs the decision, so you can swap providers without rewriting the workflow.
- The key control point is `MATCH_TWO_WAY`: the agent computes a match score between invoice and PO data. If the score drops below a threshold, the workflow routes to a HITL checkpoint node, where the current state is checkpointed and stored in a database and a review URL is created. The workflow pauses in a `“PAUSED”` state until a human reviewer acts.
- A lightweight FastAPI service exposes endpoints to view the stored state and submit an `ACCEPT/REJECT` decision. When a decision arrives, the workflow resumes at `HITL_DECISION`: ACCEPT continues to reconciliation and posting, while `REJECT` finalizes the run as `REQUIRES_MANUAL_HANDLING`.
- Finally, the agent builds accounting entries, applies approval policy, posts to ERP, schedules payment, sends notifications, and returns a final structured payload. Throughout execution, every node writes structured logs (tool calls, selections, routing, state updates) so it’s easy to audit and debug.