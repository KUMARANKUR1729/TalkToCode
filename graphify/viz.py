"""
Graphviz DOT rendering of the retrieved neighbourhood.

The project calls itself a knowledge graph but there was no way to look at it.
Seeing callers above and callees below the matched symbol makes a wrong edge
obvious at a glance, which is the fastest way to spot a parser bug.
"""
TYPE_STYLE = {
    "class": ("box", "#e8f0fe"),
    "interface": ("box", "#e8f0fe"),
    "record": ("box", "#e8f0fe"),
    "enum": ("box", "#e8f0fe"),
    "struct": ("box", "#e8f0fe"),
    "union": ("box", "#e8f0fe"),
    "component": ("box", "#e6f4ea"),
    "hook": ("ellipse", "#e6f4ea"),
    "method": ("ellipse", "#ffffff"),
    "function": ("ellipse", "#ffffff"),
    "constructor": ("ellipse", "#fff7e0"),
    "function_declaration": ("note", "#f5f5f5"),
    "macro": ("hexagon", "#fce8e6"),
    "typedef": ("note", "#f5f5f5"),
    "field": ("plaintext", "#ffffff"),
    "global": ("plaintext", "#ffffff"),
    "constant": ("plaintext", "#ffffff"),
    "file": ("folder", "#f1f3f4"),
}

EDGE_STYLE = {
    "internal": ("solid", "#1a73e8"),
    "local": ("dashed", "#188038"),
    "external": ("dotted", "#9aa0a6"),
}


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _node_stmt(key: str, label: str, ntype: str, highlight: bool) -> str:
    shape, fill = TYPE_STYLE.get(ntype, ("ellipse", "#ffffff"))
    pen = "2.4" if highlight else "1.0"
    color = "#1a73e8" if highlight else "#5f6368"
    return (f'  "{_escape(key)}" [label="{_escape(label)}", shape={shape}, '
            f'style="filled,rounded", fillcolor="{fill}", color="{color}", '
            f'penwidth={pen}, fontname="Helvetica", fontsize=10];')


def neighbourhood_dot(graph, blocks: list, max_edges_per_node: int = 12) -> str:
    """DOT for `st.graphviz_chart`: matched nodes plus their callers/callees/renders."""
    if not blocks:
        return ""

    lines = [
        "digraph G {",
        '  rankdir=TB;',
        '  bgcolor="transparent";',
        '  node [margin="0.12,0.06"];',
        '  edge [fontname="Helvetica", fontsize=8, arrowsize=0.7];',
    ]
    emitted_nodes = set()
    emitted_edges = set()
    focus_ids = {b["node"]["id"] for b in blocks}

    def emit_node(key, label, ntype, highlight=False):
        if key in emitted_nodes:
            return
        emitted_nodes.add(key)
        lines.append(_node_stmt(key, label, ntype, highlight))

    def emit_edge(a, b, label, resolution):
        if (a, b, label) in emitted_edges:
            return
        emitted_edges.add((a, b, label))
        style, color = EDGE_STYLE.get(resolution, ("solid", "#5f6368"))
        lines.append(f'  "{_escape(a)}" -> "{_escape(b)}" '
                     f'[label="{_escape(label)}", style={style}, color="{color}", '
                     f'fontcolor="{color}"];')

    for block in blocks:
        node = block["node"]
        emit_node(node["id"], f"{block['qualified_name']}\\n<{node['type']}>",
                  node["type"], highlight=True)

        for ep in block["callers"][:max_edges_per_node]:
            other = ep["node"]
            key = other["id"] if other else f"ext::{ep['name']}"
            emit_node(key, ep["name"], other["type"] if other else "external")
            emit_edge(key, node["id"], "calls", ep["resolution"])

        for ep in block["callees"][:max_edges_per_node]:
            other = ep["node"]
            key = other["id"] if other else f"ext::{ep['name']}"
            if other is None:
                emit_node(key, f"{ep['name']}\\n<{ep['resolution']}>", "external")
            else:
                emit_node(key, ep["name"], other["type"], highlight=other["id"] in focus_ids)
            emit_edge(node["id"], key, "calls", ep["resolution"])

        for ep in block.get("renders", []):
            other = ep["node"]
            key = other["id"] if other else f"ext::{ep['name']}"
            emit_node(key, ep["name"], other["type"] if other else "component")
            emit_edge(node["id"], key, "renders", ep["resolution"])

        for ep in block.get("rendered_by", []):
            other = ep["node"]
            key = other["id"] if other else f"ext::{ep['name']}"
            emit_node(key, ep["name"], other["type"] if other else "component")
            emit_edge(key, node["id"], "renders", ep["resolution"])

    lines.append("}")
    return "\n".join(lines)
