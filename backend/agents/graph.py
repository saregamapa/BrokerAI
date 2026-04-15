import os
from pathlib import Path
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from backend.agents.nodes import (
    approval_gate_node,
    compliance_node,
    content_node,
    lead_capture_node,
    media_node,
    persist_posts_node,
    publishing_node,
    research_node,
    scheduling_node,
    strategy_node,
)
from backend.agents.state import AgentState
from backend.core.logger import get_logger

log = get_logger("brokerai.agents")

# ---------------------------------------------------------------------------
# Persistent checkpointer — survives server restarts
# ---------------------------------------------------------------------------
# Try SQLite-based checkpoint (recommended for single-worker deployments).
# Falls back to in-memory if the package isn't installed yet.
_checkpointer = None


def _get_checkpointer():
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    # Determine checkpoint DB path — same directory as the main brokerai.db
    db_url = os.getenv("CHECKPOINT_DB_URL", "")
    if not db_url:
        base = Path(__file__).resolve().parent.parent.parent
        db_path = base / "brokerai_checkpoints.db"
        db_url = f"sqlite:///{db_path}"

    try:
        import sqlite3 as _sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver

        # Resolve DB path from the URL
        if db_url.startswith("sqlite:///"):
            path = db_url[len("sqlite:///"):]  # Preserve leading / for absolute paths
        else:
            path = db_url  # Already a plain path

        # In langgraph-checkpoint-sqlite>=2.0, from_conn_string() is a context manager.
        # Create the connection directly with sqlite3 to keep it open for the app lifetime.
        _conn = _sqlite3.connect(str(path), check_same_thread=False)
        _checkpointer = SqliteSaver(_conn)
        log.info("LangGraph checkpoint: persistent SQLite at %s", path)
    except (ImportError, Exception) as e:
        log.warning(
            "SQLite checkpointer unavailable (%s), falling back to MemorySaver. "
            "Install langgraph-checkpoint-sqlite for persistent checkpoints.",
            e,
        )
        from langgraph.checkpoint.memory import MemorySaver

        _checkpointer = MemorySaver()

    return _checkpointer


_compiled = None


def build_campaign_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("strategy", strategy_node)
    g.add_node("research", research_node)  # platform trend analysis before content generation
    g.add_node("content", content_node)
    g.add_node("media", media_node)
    g.add_node("compliance", compliance_node)
    g.add_node("scheduling", scheduling_node)
    g.add_node("persist", persist_posts_node)
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("publishing", publishing_node)
    g.add_node("lead_capture", lead_capture_node)  # after persist — lead + DM setup before approval

    g.set_entry_point("strategy")
    g.add_edge("strategy", "research")
    g.add_edge("research", "content")
    g.add_edge("content", "media")
    g.add_edge("media", "compliance")
    g.add_edge("compliance", "scheduling")
    g.add_edge("scheduling", "persist")
    # Lead capture runs before approval so forms exist during review; publishing is last.
    g.add_edge("persist", "lead_capture")
    g.add_edge("lead_capture", "approval_gate")
    g.add_edge("approval_gate", "publishing")
    g.add_edge("publishing", END)
    return g


def get_campaign_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_campaign_graph().compile(
            checkpointer=_get_checkpointer(),
            interrupt_before=["publishing"],
        )
        log.info("LangGraph campaign pipeline compiled (interrupt_before=publishing)")
    return _compiled


def thread_config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def run_campaign_phase1(initial_state: AgentState, thread_id: str) -> AgentState:
    """Run agents through persist + approval_gate; pauses before publishing."""
    app = get_campaign_graph()
    cfg = thread_config(thread_id)
    out = app.invoke(initial_state, cfg)
    log.info("LangGraph phase1 finished thread=%s keys=%s", thread_id, list(out.keys()))
    return out


def resume_campaign_publishing(
    thread_id: str, campaign_id: int, user_id: int
) -> AgentState:
    """After user approval: set approved and run publishing node.

    With persistent SQLite checkpointing, this reliably resumes even after
    server restarts. Falls back to direct DB publishing if checkpoint is
    somehow missing.
    """
    app = get_campaign_graph()
    cfg = thread_config(thread_id)
    try:
        app.update_state(cfg, {"approved": True})
        out = app.invoke(None, cfg)
        log.info("LangGraph publish phase done thread=%s", thread_id)
        return out
    except Exception:
        log.exception(
            "LangGraph resume failed; running publishing node directly campaign_id=%s",
            campaign_id,
        )
        return publishing_node(
            {
                "campaign_id": campaign_id,
                "user_id": user_id,
                "approved": True,
                "campaign_data": {},
                "step_log": [],
            }
        )
