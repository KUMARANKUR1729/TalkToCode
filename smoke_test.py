#!/usr/bin/env python3
"""
End-to-end smoke test: run real questions against real graphs.

`pytest` proves the parsers and retrieval behave. This proves the whole chain
works on a codebase you just added — build, retrieve, prompt, answer, verify.

    python smoke_test.py                          # every codebase, with the LLM
    python smoke_test.py --offline                # retrieval only, no network
    python smoke_test.py --target java-shop-sample
    python smoke_test.py --questions my_qs.txt

Questions come from questions_to_ask.txt, which doubles as the spec: a question
tagged "(must refuse)" fails the run if it produces an answer.

Exit code is non-zero if anything failed, so this works in CI.
"""
import argparse
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()

from graphify import config, prompting, verification  # noqa: E402
from graphify.graph import list_targets, load_graph, staleness_report  # noqa: E402
from graphify.llm import LLMError, chat_with_fallback  # noqa: E402
from graphify.retrieval import gather_context, retrieve  # noqa: E402

SECTION_RE = re.compile(r"^\[([\w.-]+)\]\s*$")
MUST_REFUSE = "must refuse"


def parse_questions(path: Path):
    """Yields (target, question, must_refuse)."""
    target = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = SECTION_RE.match(line)
        if header:
            target = header.group(1)
            continue
        if target is None or "?" not in line:
            continue
        must_refuse = MUST_REFUSE in line.lower()
        question = line.split("#")[0].split("(")[0].strip()
        if question.endswith("?"):
            yield target, question, must_refuse


def run_case(graph, target, question, must_refuse, *, offline, model, api_key):
    result = retrieve(graph, question,
                      api_key="" if offline else api_key,
                      model=model,
                      semantic_mode="off" if offline else config.SEMANTIC_MODE)
    blocks = gather_context(graph, result.matches)

    problems = []
    if must_refuse and blocks:
        problems.append(
            f"expected a refusal but retrieval matched "
            f"{[graph.qualified_name(n) for n in result.nodes]}")
    if not must_refuse and not blocks:
        problems.append(f"expected a match but retrieval refused ({result.reason})")

    answer, used_model, findings = "", "-", []
    if not offline:
        messages = prompting.build_messages(question, target, blocks, result)
        try:
            raw, used_model, _ = chat_with_fallback(api_key, model, messages)
            answer = prompting.normalize_answer(raw)
            findings = verification.verify(answer, blocks, graph)
            if any(f.severity == "error" for f in findings):
                retry = prompting.build_correction_messages(
                    question, target, blocks, answer, findings)
                raw, used_model, _ = chat_with_fallback(api_key, used_model, retry)
                answer = prompting.normalize_answer(raw)
                findings = verification.verify(answer, blocks, graph)
            problems += [f"{f.kind}: {f.message}" for f in findings]
        except LLMError as e:
            problems.append(f"LLM error: {e}")

    return result, blocks, answer, used_model, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="questions_to_ask.txt")
    ap.add_argument("--target", action="append",
                    help="limit to one codebase (repeatable)")
    ap.add_argument("--offline", action="store_true",
                    help="retrieval only — no API key or network needed")
    ap.add_argument("--model", default=config.DEFAULT_MODEL)
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    args = ap.parse_args()

    questions_path = ROOT / args.questions
    if not questions_path.exists():
        print(f"No questions file at {questions_path}")
        return 2

    api_key = config.api_key()
    if not args.offline and not api_key:
        print("No NVIDIA_API_KEY found. Use --offline to test retrieval only.")
        return 2

    available = set(list_targets())
    cases = list(parse_questions(questions_path))
    if args.target:
        cases = [c for c in cases if c[0] in set(args.target)]
    if not cases:
        print("No questions matched.")
        return 2

    missing = {t for t, _, _ in cases} - available
    for target in sorted(missing):
        print(f"!! no graph for '{target}' — run: python run_graphify.py --all")
    cases = [c for c in cases if c[0] in available]

    graphs, failures, checked = {}, [], 0
    for target in sorted({t for t, _, _ in cases}):
        graphs[target] = load_graph(target)
        stale = staleness_report(graphs[target])
        if stale.is_stale:
            print(f"!! {target}: graph is stale — {stale.summary()} "
                  "(run: python run_graphify.py --all)")
            failures.append((target, "<graph>", ["stale graph"]))
        broken = graphs[target].meta.get("parse_failures") or []
        if broken:
            print(f"!! {target}: {len(broken)} file(s) failed to parse")
            failures.append((target, "<graph>", [str(broken)]))

    for target, question, must_refuse in cases:
        graph = graphs[target]
        started = time.time()
        result, blocks, answer, used_model, problems = run_case(
            graph, target, question, must_refuse,
            offline=args.offline, model=args.model, api_key=api_key)
        checked += 1
        status = "FAIL" if problems else "ok"
        if problems:
            failures.append((target, question, problems))

        if problems or not args.quiet:
            print(f"\n[{status}] {target}: {question}")
            print(f"    strategy={result.strategy} confidence={result.confidence} "
                  f"matched={[graph.qualified_name(n) for n in result.nodes]} "
                  f"{time.time() - started:.1f}s model={used_model.split('/')[-1]}")
            if result.suggestions:
                print(f"    suggestions={result.suggestions}")
            if answer:
                print("    " + answer.replace("\n", "\n    ")[:600])
            for problem in problems:
                print(f"    !! {problem}")

    print(f"\n{checked - len({(t, q) for t, q, _ in failures})}/{checked} cases clean")
    if failures:
        print(f"{len(failures)} failure(s):")
        for target, question, problems in failures:
            print(f"  - [{target}] {question}: {problems[0]}")
        return 1
    print("all clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
