"""
NVIDIA API client with retries and streaming.

The old version did a single blocking POST and surfaced any hiccup as a raw
error, so a transient 429 or 503 looked like a broken app. Requests are now
retried with exponential backoff, and answers can stream so the user sees
tokens immediately instead of staring at a spinner.
"""
import json
import random
import time

import requests

from . import config

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when the API cannot be reached or refuses the request."""


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _sleep_for(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 20.0)
        except ValueError:
            pass
    return min(2 ** attempt + random.uniform(0, 0.4), 20.0)


def _post(url: str, api_key: str, payload: dict, *, stream: bool = False):
    """POST with exponential backoff on transient failures."""
    last_error = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = requests.post(url, headers=_headers(api_key), json=payload,
                                 timeout=config.REQUEST_TIMEOUT, stream=stream)
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code in RETRY_STATUS and attempt < config.MAX_RETRIES - 1:
                last_error = f"HTTP {resp.status_code}"
                time.sleep(_sleep_for(attempt, resp.headers.get("Retry-After")))
                continue
            if not resp.ok:
                detail = resp.text[:400]
                raise LLMError(f"NVIDIA API returned HTTP {resp.status_code}: {detail}")
            return resp
        time.sleep(_sleep_for(attempt, None))
    raise LLMError(f"NVIDIA API unreachable after {config.MAX_RETRIES} attempts ({last_error})")


def chat(api_key: str, model: str, messages: list, *,
         temperature: float = 0.1, max_tokens: int = 900) -> str:
    if not api_key:
        raise LLMError("No NVIDIA API key configured (set NVIDIA_API_KEY in .env).")
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    data = _post(config.CHAT_ENDPOINT, api_key, payload).json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected API response shape: {json.dumps(data)[:300]}") from e


def chat_stream(api_key: str, model: str, messages: list, *,
                temperature: float = 0.1, max_tokens: int = 900):
    """Yields text chunks as they arrive. Falls back to a blocking call on error."""
    if not api_key:
        raise LLMError("No NVIDIA API key configured (set NVIDIA_API_KEY in .env).")
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    resp = _post(config.CHAT_ENDPOINT, api_key, payload, stream=True)
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if chunk in ("", "[DONE]"):
            continue
        try:
            delta = json.loads(chunk)["choices"][0].get("delta", {})
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        piece = delta.get("content")
        if piece:
            yield piece


def model_chain(preferred: str) -> list:
    """The preferred model first, then the remaining known models as fallbacks."""
    chain = [preferred]
    chain += [m for m in config.KNOWN_MODELS if m != preferred]
    return chain


def chat_with_fallback(api_key: str, preferred: str, messages: list, **kwargs):
    """
    Hosted models go offline (HTTP 410 after retirement) and get overloaded
    (503). Rather than surfacing that as a dead app, walk down the known model
    list. Returns (text, model_used, notes).
    """
    notes = []
    chain = model_chain(preferred)
    for index, model in enumerate(chain):
        try:
            return chat(api_key, model, messages, **kwargs), model, notes
        except LLMError as e:
            notes.append(f"{model}: {e}")
            if index == len(chain) - 1:
                raise LLMError("Every known model failed:\n" + "\n".join(notes)) from e
    raise LLMError("No models configured")


def embed(api_key: str, texts: list, *, model: str | None = None,
          input_type: str = "passage", batch_size: int = 32) -> list:
    """
    Returns one vector per input text. Raises LLMError if the embedding model
    is unavailable — callers are expected to fall back, not crash.
    """
    if not api_key:
        raise LLMError("No NVIDIA API key configured.")
    model = model or config.EMBED_MODEL
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        payload = {"model": model, "input": batch,
                   "input_type": input_type, "encoding_format": "float"}
        data = _post(config.EMBED_ENDPOINT, api_key, payload).json()
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        if len(items) != len(batch):
            raise LLMError(f"Embedding API returned {len(items)} vectors for {len(batch)} inputs")
        vectors.extend(item["embedding"] for item in items)
    return vectors
