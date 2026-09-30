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
from urllib.parse import urlparse

import requests

from . import config

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_SESSION = requests.Session()


class LLMError(RuntimeError):
    """Raised when the provider or local model policy refuses a request."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_access_denied(self) -> bool:
        return self.status_code in {401, 403}


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _validate_messages(messages: list, max_tokens: int) -> None:
    if (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or
            not 1 <= max_tokens <= config.MAX_MODEL_OUTPUT_TOKENS):
        raise LLMError("Requested model output exceeds policy.")
    if not isinstance(messages, list) or not messages:
        raise LLMError("At least one model message is required.")
    total = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
                "system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise LLMError("Invalid model message format.")
        total += len(message["content"])
    if total > config.MAX_MODEL_INPUT_CHARS:
        raise LLMError("Model input exceeds configured policy limit.")


def _sleep_for(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 20.0)
        except ValueError:
            pass
    return min(2 ** attempt + random.uniform(0, 0.4), 20.0)


def _post(url: str, api_key: str, payload: dict, *, stream: bool = False):
    """POST through an allowlisted HTTPS endpoint with bounded, sanitized retries."""
    if not config.ALLOW_EXTERNAL_LLM:
        raise LLMError("External model access is disabled by policy.")
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in config.ALLOWED_LLM_HOSTS:
        raise LLMError("Model endpoint is not permitted by the egress policy.")

    last_error = "provider unavailable"
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = _SESSION.post(url, headers=_headers(api_key), json=payload,
                                 timeout=config.REQUEST_TIMEOUT, stream=stream)
        except requests.RequestException as exc:
            last_error = type(exc).__name__
        else:
            if resp.status_code in RETRY_STATUS and attempt < config.MAX_RETRIES - 1:
                last_error = f"HTTP {resp.status_code}"
                delay = _sleep_for(attempt, resp.headers.get("Retry-After"))
                resp.close()
                time.sleep(delay)
                continue
            if not resp.ok:
                status = resp.status_code
                request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
                resp.close()
                suffix = f" (request {request_id})" if request_id else ""
                raise LLMError(
                    f"Model provider returned HTTP {status}{suffix}.",
                    status_code=status,
                )
            return resp
        if attempt < config.MAX_RETRIES - 1:
            time.sleep(_sleep_for(attempt, None))
    raise LLMError(
        f"Model provider unreachable after {config.MAX_RETRIES} attempts ({last_error}).")


def chat(api_key: str, model: str, messages: list, *,
         temperature: float = 0.1, max_tokens: int = 900) -> str:
    if not api_key:
        raise LLMError("No NVIDIA API key configured (set NVIDIA_API_KEY in .env).")
    _validate_messages(messages, max_tokens)
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    response = _post(config.CHAT_ENDPOINT, api_key, payload)
    try:
        data = response.json()
    except (requests.JSONDecodeError, ValueError) as exc:
        raise LLMError("Model provider returned malformed JSON.") from exc
    finally:
        response.close()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Model provider returned an unexpected response shape.") from exc


def chat_stream(api_key: str, model: str, messages: list, *,
                temperature: float = 0.1, max_tokens: int = 900):
    """Yields text chunks as they arrive. Falls back to a blocking call on error."""
    if not api_key:
        raise LLMError("No NVIDIA API key configured (set NVIDIA_API_KEY in .env).")
    _validate_messages(messages, max_tokens)
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    resp = _post(config.CHAT_ENDPOINT, api_key, payload, stream=True)
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            chunk = raw[5:].strip()
            if chunk in ("", "[DONE]"):
                continue
            try:
                delta = json.loads(chunk)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
            piece = delta.get("content")
            if piece:
                yield piece
    finally:
        resp.close()


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
        except LLMError as exc:
            if exc.is_access_denied:
                raise LLMError(
                    "NVIDIA rejected the configured API key or account access "
                    f"(HTTP {exc.status_code}). Replace NVIDIA_API_KEY with an active "
                    "key that is authorized for the configured endpoint.",
                    status_code=exc.status_code,
                ) from exc
            notes.append(f"{model}: {exc}")
            if index == len(chain) - 1:
                raise LLMError("Every known model failed:\n" + "\n".join(notes)) from exc
    raise LLMError("No models configured")


def embed(api_key: str, texts: list, *, model: str | None = None,
          input_type: str = "passage", batch_size: int = 32) -> list:
    """
    Returns one vector per input text. Raises LLMError if the embedding model
    is unavailable — callers are expected to fall back, not crash.
    """
    if not api_key:
        raise LLMError("No NVIDIA API key configured.")
    if (not isinstance(texts, list) or not texts or
            any(not isinstance(text, str) for text in texts)):
        raise LLMError("Embedding input must be a non-empty list of strings.")
    if sum(len(text) for text in texts) > config.MAX_MODEL_INPUT_CHARS:
        raise LLMError("Embedding input exceeds configured policy limit.")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 256:
        raise LLMError("Embedding batch size exceeds policy.")
    model = model or config.EMBED_MODEL
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        payload = {"model": model, "input": batch,
                   "input_type": input_type, "encoding_format": "float"}
        response = _post(config.EMBED_ENDPOINT, api_key, payload)
        try:
            data = response.json()
        except (requests.JSONDecodeError, ValueError) as exc:
            raise LLMError("Embedding provider returned malformed JSON.") from exc
        finally:
            response.close()
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        if len(items) != len(batch):
            raise LLMError(f"Embedding API returned {len(items)} vectors for {len(batch)} inputs")
        vectors.extend(item["embedding"] for item in items)
    return vectors
