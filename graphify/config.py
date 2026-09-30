"""Validated environment configuration for local and production modes."""
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Local convenience only; real environment variables keep precedence.
load_dotenv(ROOT / ".env", override=False)
OUTPUTS_DIR = ROOT / "outputs"
CODEBASE_DIR = ROOT / "codebase"


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _endpoint(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be an HTTPS URL without embedded credentials")
    return value


CHAT_ENDPOINT = _endpoint("GRAPHIFY_CHAT_ENDPOINT",
                          "https://integrate.api.nvidia.com/v1/chat/completions")
EMBED_ENDPOINT = _endpoint("GRAPHIFY_EMBED_ENDPOINT",
                           "https://integrate.api.nvidia.com/v1/embeddings")
MODELS_ENDPOINT = _endpoint("GRAPHIFY_MODELS_ENDPOINT",
                            "https://integrate.api.nvidia.com/v1/models")
ALLOWED_LLM_HOSTS = frozenset(
    host.strip().lower() for host in
    os.getenv("GRAPHIFY_ALLOWED_LLM_HOSTS", "integrate.api.nvidia.com").split(",")
    if host.strip()
)

DEFAULT_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")
KNOWN_MODELS = [
    "meta/llama-3.2-11b-vision-instruct",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]
MODEL_NOTES = {
    KNOWN_MODELS[0]: "Fast and reliably formatted; less detailed.",
    KNOWN_MODELS[1]: "Rich answers but higher latency and lower availability.",
    KNOWN_MODELS[2]: "Reasoning model; slower, with normalized output.",
}
EMBED_MODEL = os.getenv("NVIDIA_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")
SEMANTIC_MODE = os.getenv("GRAPHIFY_SEMANTIC", "off").lower()
if SEMANTIC_MODE not in {"auto", "embeddings", "llm", "off"}:
    raise ValueError("GRAPHIFY_SEMANTIC must be auto, embeddings, llm, or off")

ENVIRONMENT = os.getenv("GRAPHIFY_ENV", "development").strip().lower()
if ENVIRONMENT not in {"development", "test", "production"}:
    raise ValueError("GRAPHIFY_ENV must be development, test, or production")
IS_PRODUCTION = ENVIRONMENT == "production"
TRUSTED_AUTH_BOUNDARY = _bool("GRAPHIFY_TRUSTED_AUTH_BOUNDARY", False)
ALLOW_EXTERNAL_LLM = _bool("GRAPHIFY_ALLOW_EXTERNAL_LLM", False)
ALLOW_BROWSER_API_KEY = _bool("GRAPHIFY_ALLOW_BROWSER_API_KEY", False)
ALLOW_UI_REBUILD = _bool("GRAPHIFY_ALLOW_UI_REBUILD", not IS_PRODUCTION)
REQUIRE_ARTIFACT_CHECKSUM = _bool("GRAPHIFY_REQUIRE_ARTIFACT_CHECKSUM", IS_PRODUCTION)

REQUEST_TIMEOUT = _int("GRAPHIFY_TIMEOUT", 60, 1, 300)
MAX_RETRIES = _int("GRAPHIFY_MAX_RETRIES", 3, 1, 6)
MAX_CONTEXT_NODES = _int("GRAPHIFY_MAX_NODES", 5, 1, 20)
MAX_QUESTION_CHARS = _int("GRAPHIFY_MAX_QUESTION_CHARS", 4000, 64, 20000)
MAX_SNIPPET_CHARS = _int("GRAPHIFY_MAX_SNIPPET_CHARS", 20000, 1000, 200000)
MAX_MODEL_INPUT_CHARS = _int("GRAPHIFY_MAX_MODEL_INPUT_CHARS", 150000, 1000, 1000000)
MAX_MODEL_OUTPUT_TOKENS = _int("GRAPHIFY_MAX_MODEL_OUTPUT_TOKENS", 2000, 64, 16000)
MAX_SOURCE_FILES = _int("GRAPHIFY_MAX_SOURCE_FILES", 10000, 1, 1000000)
MAX_SOURCE_FILE_BYTES = _int("GRAPHIFY_MAX_SOURCE_FILE_BYTES", 5_000_000, 1024, 100_000_000)
MAX_REPOSITORY_BYTES = _int("GRAPHIFY_MAX_REPOSITORY_BYTES", 1_000_000_000, 1024, 100_000_000_000)
MAX_GRAPH_BYTES = _int("GRAPHIFY_MAX_GRAPH_BYTES", 250_000_000, 1024, 2_000_000_000)
MAX_GRAPH_NODES = _int("GRAPHIFY_MAX_GRAPH_NODES", 1_000_000, 1, 10_000_000)
MAX_GRAPH_EDGES = _int("GRAPHIFY_MAX_GRAPH_EDGES", 3_000_000, 1, 30_000_000)
INDEX_CACHE_SIZE = _int("GRAPHIFY_INDEX_CACHE_SIZE", 8, 1, 128)
ARTIFACT_LOCK_TIMEOUT = _int("GRAPHIFY_ARTIFACT_LOCK_TIMEOUT", 30, 1, 600)
ARTIFACT_LOCK_STALE_AFTER = _int("GRAPHIFY_ARTIFACT_LOCK_STALE_AFTER", 900, 30, 86400)
SUPPORTED_GRAPH_SCHEMAS = frozenset({2})


def api_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "").strip().strip('"').strip("'")
