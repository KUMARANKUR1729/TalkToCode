"""
Automated post-hoc verification of the model's answer.

This is the automated version of what a human reviewer would check: did the
model actually use the names and locations it was given, or did it drift back
to something it remembered? Three checks:

  * renamed symbol   — an expected exact name is missing from the answer
  * fabricated range — a cited FILE#LINE is not one of the real node ranges
  * invented symbol  — a `Class.method` style citation that does not exist in
                       the graph at all (the classic failure: reporting a C
                       function `auth_login` as `AuthService.login`)

Findings are warnings, not silent rewrites, and they drive one corrective
retry before anything is shown as trustworthy.
"""
import re
from dataclasses import dataclass

RANGE_RE = re.compile(r"([\w./\\-]+\.\w+)\s*#\s*L(\d+)\s*-\s*(\d+)")
BACKTICKED_RE = re.compile(r"`([A-Za-z_][\w.]*)`")
#: `order_repo.py` is a filename, not a `Class.method`. Without this the
#: invented-symbol check flagged every answer that named a file it was given.
SOURCE_SUFFIXES = (".py", ".java", ".c", ".h", ".js", ".jsx", ".mjs", ".ts",
                   ".tsx", ".json", ".md", ".txt", ".html", ".css", ".yml", ".yaml")

SEVERITY_ORDER = {"error": 2, "warning": 1}


@dataclass
class Finding:
    kind: str
    severity: str
    message: str


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def verify(answer: str, blocks: list, graph=None) -> list:
    """Returns a list of Finding. Empty list means the answer is clean."""
    if not answer or not blocks:
        return []

    findings = []

    # 1. the answer must ground itself in at least one of the real names.
    #    Requiring *every* retrieved name was wrong: retrieval hands over
    #    candidates, and a good answer legitimately focuses on the relevant one.
    #    The failure worth catching is an answer that uses none of them.
    expected = []
    for block in blocks:
        expected.append(block["node"]["name"])
        expected.append(block["qualified_name"])
    if not any(name in answer for name in expected):
        primary = blocks[0]
        findings.append(Finding(
            kind="renamed_symbol", severity="error",
            message=(f"The answer uses none of the real names it was given "
                     f"(e.g. `{primary['qualified_name']}`), so it may have renamed "
                     "or substituted the symbol."),
        ))

    # 2. cited line ranges must be ranges we actually handed over
    valid = {(_normalize(b["node"]["file"]), int(b["node"]["line_start"]),
              int(b["node"]["line_end"])) for b in blocks}
    valid_files = {f for f, _, _ in valid}
    for raw_file, start, end in RANGE_RE.findall(answer):
        key = (_normalize(raw_file), int(start), int(end))
        if key in valid:
            continue
        if key[0] not in valid_files:
            findings.append(Finding(
                kind="fabricated_range", severity="error",
                message=(f"The answer cites `{raw_file}#L{start}-{end}`, but that file "
                         "was not part of the facts provided."),
            ))
        else:
            findings.append(Finding(
                kind="fabricated_range", severity="error",
                message=(f"The answer cites `{raw_file}#L{start}-{end}`, which is not "
                         "one of the real graph ranges — likely a subdivided or "
                         "invented location."),
            ))

    # 3. dotted symbols in backticks that do not exist anywhere in the graph
    if graph is not None:
        known = {n["name"] for n in graph.nodes}          # includes file names
        known |= {n["file"] for n in graph.nodes}         # and full relative paths
        known |= set(graph.all_qualified_names())
        known |= {b["qualified_name"] for b in blocks}
        for block in blocks:
            known |= {ep["name"] for ep in block["callers"]}
            known |= {ep["name"] for ep in block["callees"]}
            known |= {ep["name"] for ep in block.get("renders", [])}
            known |= {ep["name"] for ep in block.get("rendered_by", [])}
        for token in set(BACKTICKED_RE.findall(answer)):
            if "." not in token or token in known:
                continue
            if token.lower().endswith(SOURCE_SUFFIXES):
                continue  # a filename, not a symbol reference
            # `foo.bar` where neither the whole thing nor the owner is real
            owner, _, member = token.rpartition(".")
            if owner in known or member in known:
                continue
            if token.split(".")[0] in known:
                continue
            findings.append(Finding(
                kind="invented_symbol", severity="warning",
                message=(f"The answer mentions `{token}`, which does not exist in the "
                         "knowledge graph under that name."),
            ))

    return findings


def worst_severity(findings: list) -> str | None:
    if not findings:
        return None
    return max((f.severity for f in findings), key=lambda s: SEVERITY_ORDER.get(s, 0))


def summarize(findings: list) -> str:
    return "\n".join(f"- **{f.kind}**: {f.message}" for f in findings)
