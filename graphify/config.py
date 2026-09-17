"""Central configuration. Every knob is an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
CODEBASE_DIR = ROOT / "codebase"

CHAT_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
EMBED_ENDPOINT = "https://integrate.api.nvidia.com/v1/embeddings"
MODELS_ENDPOINT = "https://integrate.api.nvidia.com/v1/models"

# Measured on this task, not assumed. The 11B model is a *vision* model being
# used for a text job, which looks like a mistake — but the text alternatives
# reachable on this catalog are reasoning models that leak their scratchpad and
# run 5-25x slower, so it stays the default. The 120B produces richer answers
# (it names the specific helpers a function calls) at much higher latency and
# with frequent 503s, so it is offered rather than forced.
DEFAULT_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")

#: Order matters: this is also the automatic fallback chain.
KNOWN_MODELS = [
    "meta/llama-3.2-11b-vision-instruct",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]

MODEL_NOTES = {
    "meta/llama-3.2-11b-vision-instruct":
        "Fast and reliably formatted. Answers are correct but less detailed.",
    "nvidia/nemotron-3-super-120b-a12b":
        "Richest answers (names the specific helpers involved) but slow and "
        "often overloaded; leaks reasoning text, which gets stripped.",
    "nvidia/nemotron-3.5-lightning-30b-a3b":
        "Reasoning model. Always emits a scratchpad, which gets stripped. Slow.",
}

EMBED_MODEL = os.getenv("NVIDIA_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")

# auto = embeddings if they work, else fall back to asking the chat model to
# pick symbols; embeddings = require embeddings; llm = always use the chat
# model as the selector; off = lexical retrieval only (fully offline).
SEMANTIC_MODE = os.getenv("GRAPHIFY_SEMANTIC", "auto").lower()

REQUEST_TIMEOUT = int(os.getenv("GRAPHIFY_TIMEOUT", "120"))
MAX_RETRIES = int(os.getenv("GRAPHIFY_MAX_RETRIES", "3"))
MAX_CONTEXT_NODES = int(os.getenv("GRAPHIFY_MAX_NODES", "5"))


def api_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "").strip().strip('"').strip("'")
