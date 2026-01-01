# src/graph_builder.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver  # native sqlite checkpointer :contentReference[oaicite:1]{index=1}

from src.state import InvoiceWorkflowState, ensure_defaults, log_event
from src.runner import Runtime, execute_stage


def load_workflow(workflow_path: str | Path) -> Dict[str, Any]:
    path = Path(workflow_path)
    if not path.exists():
        raise FileNotFoundError(f"workflow.json not found at: {path.resolve()}")
    return json.loads(path.read_text(encoding="utf-8"))


def _index_stages(workflow: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    stage_map: Dict[str, Dict[str, Any]] = {}
    for s in workflow.get("stages", []):
        sid = s.get("id")
        if not sid:
            raise ValueError("Each stage must have an 'id'")
        if sid in stage_map:
            raise ValueError(f"Duplicate stage id: {sid}")
        stage_map[sid] = s
    if not stage_map:
        raise ValueError("workflow.json must have non-empty 'stages'")
    return stage_map


def _route_match_two_way(state: InvoiceWorkflowState, workflow: Dict[str, Any]) -> str:
    threshold = float(workflow.get("globals", {}).get("match_threshold", 0.85))
    match_score = float(state.get("match_score", 0.0))

    log_event(
        state,
        stage="MATCH_TWO_WAY",
        event="route_decision",
        message="Routing based on match_score threshold",
        match_score=match_score,
        threshold=threshold,
    )

    return "CHECKPOINT_HITL" if match_score < threshold else "RECONCILE"


def _route_hitl_decision(state: InvoiceWorkflowState) -> str:
    decision = state.get("decision")
    log_event(
        state,
        stage="HITL_DECISION",
        event="route_decision",
        message="Routing based on human decision",
        decision=decision,
    )
    return "RECONCILE" if decision == "ACCEPT" else "COMPLETE"


def make_stage_node(stage_id: str):
    def _node(state: InvoiceWorkflowState, config: RunnableConfig) -> InvoiceWorkflowState:
        state = ensure_defaults(state)
        state["current_stage"] = stage_id
        if state.get("status") in (None, "NEW"):
            state["status"] = "IN_PROGRESS"

        log_event(state, stage=stage_id, event="stage_start", message=f"Starting stage {stage_id}")

        runtime: Optional[Runtime] = None
        if config and "configurable" in config:
            runtime = config["configurable"].get("runtime")

        if runtime is None:
            # Helpful during wiring; but in your demo you should always pass runtime.
            log_event(state, stage=stage_id, event="warning", message="No runtime provided; no-op stage")
            log_event(state, stage=stage_id, event="stage_end", message=f"Completed stage {stage_id} (no-op)")
            return state

        state = execute_stage(runtime, stage_id, state, config)

        log_event(state, stage=stage_id, event="stage_end", message=f"Completed stage {stage_id}")
        return state

    return _node


def build_graphs(
    workflow_path: str | Path = "configs/workflow.json",
    checkpoint_db_path: str = "checkpoints.sqlite",
) -> Tuple[Any, Any, Dict[str, Any]]:
    workflow = load_workflow(workflow_path)
    stage_map = _index_stages(workflow)

    # Native LangGraph checkpointing to SQLite
    conn = sqlite3.connect(checkpoint_db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    # Main graph (INTAKE -> ... -> either COMPLETE or CHECKPOINT_HITL stop)
    main_graph = StateGraph(InvoiceWorkflowState)
    _add_nodes_and_edges(main_graph, workflow, stage_map, entry_stage="INTAKE", main_mode=True)
    main_app = main_graph.compile(checkpointer=checkpointer)

    # Resume graph (HITL_DECISION -> ... -> COMPLETE)
    resume_graph = StateGraph(InvoiceWorkflowState)
    _add_nodes_and_edges(resume_graph, workflow, stage_map, entry_stage="HITL_DECISION", main_mode=False)
    resume_app = resume_graph.compile(checkpointer=checkpointer)

    return main_app, resume_app, workflow


def _add_nodes_and_edges(graph: StateGraph, workflow: Dict[str, Any], stage_map: Dict[str, Dict[str, Any]], entry_stage: str, main_mode: bool) -> None:
    for stage_id in stage_map.keys():
        graph.add_node(stage_id, make_stage_node(stage_id))

    graph.set_entry_point(entry_stage)

    for stage_id, cfg in stage_map.items():
        if cfg.get("terminal") is True:
            graph.add_edge(stage_id, END)
            continue

        if stage_id == "MATCH_TWO_WAY":
            graph.add_conditional_edges(
                "MATCH_TWO_WAY",
                lambda st, wf=workflow: _route_match_two_way(st, wf),
                {"CHECKPOINT_HITL": "CHECKPOINT_HITL", "RECONCILE": "RECONCILE"},
            )
            continue

        if stage_id == "HITL_DECISION":
            graph.add_conditional_edges(
                "HITL_DECISION",
                lambda st: _route_hitl_decision(st),
                {"RECONCILE": "RECONCILE", "COMPLETE": "COMPLETE"},
            )
            continue

        # In MAIN graph, once we reach CHECKPOINT_HITL we stop (pause).
        if main_mode and stage_id == "CHECKPOINT_HITL":
            graph.add_edge("CHECKPOINT_HITL", END)
            continue

        nxt = cfg.get("next")
        if nxt:
            graph.add_edge(stage_id, nxt)
        else:
            graph.add_edge(stage_id, END)
