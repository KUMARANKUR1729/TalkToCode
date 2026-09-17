"""
Graphify core — deliberately UI-free.

Everything that decides *what the model is told* lives here, separate from
Streamlit, so it can be unit-tested and reused. app.py is only a view.
"""
from .graph import CodeGraph, load_graph, list_targets, staleness_report, read_snippet
from .retrieval import Retrieval, retrieve, gather_context

__all__ = [
    "CodeGraph", "load_graph", "list_targets", "staleness_report", "read_snippet",
    "Retrieval", "retrieve", "gather_context",
]
