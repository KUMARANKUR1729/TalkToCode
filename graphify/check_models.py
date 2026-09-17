"""
Report which NVIDIA models this API key can actually reach.

Hosted catalogs retire models: several defaults that worked when this project
was written now return HTTP 410 Gone. Rather than debugging a confusing error
inside the app, run:

    python -m graphify.check_models
"""
import sys

import requests
from dotenv import load_dotenv

from . import config
from .llm import LLMError, chat, embed


def probe_chat(api_key: str, model: str) -> str:
    try:
        chat(api_key, model, [{"role": "user", "content": "Reply with OK."}],
             temperature=0.0, max_tokens=8)
        return "available"
    except LLMError as e:
        return str(e).split(":", 1)[-1].strip()[:90]


def probe_embed(api_key: str, model: str) -> str:
    try:
        vectors = embed(api_key, ["ping"], model=model, input_type="query")
        return f"available (dim={len(vectors[0])})"
    except LLMError as e:
        return str(e).split(":", 1)[-1].strip()[:90]


def main() -> int:
    load_dotenv()
    api_key = config.api_key()
    if not api_key:
        print("No NVIDIA_API_KEY found. Put it in .env first.")
        return 1

    try:
        resp = requests.get(config.MODELS_ENDPOINT,
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        catalog = sorted(m["id"] for m in resp.json().get("data", [])) if resp.ok else []
        print(f"Catalog lists {len(catalog)} models (listing does not imply availability).")
    except requests.RequestException as e:
        print(f"Could not list models: {e}")

    print("\nChat models:")
    for model in config.KNOWN_MODELS:
        marker = " (default)" if model == config.DEFAULT_MODEL else ""
        print(f"  {model}{marker}: {probe_chat(api_key, model)}")

    print("\nEmbedding model:")
    print(f"  {config.EMBED_MODEL}: {probe_embed(api_key, config.EMBED_MODEL)}")
    print("\nIf the embedding model is unavailable, retrieval still works: set "
          "GRAPHIFY_SEMANTIC=llm to use the chat model as the symbol selector, "
          "or =off for lexical-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
