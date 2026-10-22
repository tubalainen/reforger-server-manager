"""The optional AI service: one chat question, and whether it works (#164, #199, #254).

Used by the Console's "Ask AI" (services/log_explain.py), which builds its own
question. Nothing here builds one: callers pass a prompt they built from data
the manager holds, never free text from the browser, so the manager is never a
relay that forwards someone's text to a third-party AI from its own address.

The service is set up in .env (AI_ORDER_URL, AI_ORDER_MODEL, AI_ORDER_KEY; the
names come from the mod-order feature they were first made for): any
OpenAI-compatible endpoint, or a local Ollama through its own API. With nothing
set up the manager makes no outbound AI request at all.
"""
import logging
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

import config

logger = logging.getLogger("manager.ai")

REQUEST_TIMEOUT = 90.0  # models are slow; the GUI shows a spinner meanwhile

# A local model on a small GPU first has to be loaded into memory, and may spill
# onto the CPU for part of a long answer — minutes, not seconds.
OLLAMA_TIMEOUT = 300.0

OLLAMA_PORT = 11434

# The model suggested for Ollama, and used when AI_ORDER_MODEL is left empty:
# ~1.9 GB of weights at Ollama's default 4-bit quantisation, so it fits a 4 GB
# card with room for a long log excerpt, and not a "thinking" model that spends
# the time budget reasoning before it answers.
OLLAMA_DEFAULT_MODEL = "qwen2.5:3b"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

# How long an answer may be, in tokens, unless the caller says otherwise. The cap
# also stops a small model that starts repeating itself from running until the
# timeout.
DEFAULT_ANSWER_TOKENS = 700

# Context window bounds for Ollama. Its default window (2–4k tokens, depending
# on the version) can be smaller than a prompt, and Ollama does not refuse an
# over-long prompt: it silently drops the START of it — the instructions — so
# the model answers a question it never saw.
_OLLAMA_MIN_CTX = 4096
_OLLAMA_MAX_CTX = 32768

# Text from elsewhere — a mod's Workshop name, a scenario's name — going into a
# prompt is flattened to one line and clipped, so it cannot forge extra lines,
# rules or instructions in the question.
_UNSAFE_IN_TEXT = re.compile(r"[\r\n|`]+")
_TEXT_LIMIT = 80


def clean_text(text: str | None) -> str:
    out = _UNSAFE_IN_TEXT.sub(" ", (text or "").strip())
    out = " ".join(out.split())
    if len(out) > _TEXT_LIMIT:
        out = out[: _TEXT_LIMIT - 1].rstrip() + "…"
    return out or "(name unknown)"


def configured() -> bool:
    """True when the operator has set up an AI service."""
    return bool(config.settings.ai_order_url)


def _is_ollama(url: str) -> bool:
    """Does AI_ORDER_URL point at Ollama rather than an OpenAI-style service?

    Ollama's own API is `/api/chat`; a bare Ollama address (no path) means the
    same. Its OpenAI-compatible `/v1/chat/completions` — which older notes here
    suggested — is treated as Ollama too when it is on Ollama's port, because
    that route cannot be given a context size and cuts long prompts short.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path in ("", "/api", "/api/chat"):
        return True
    return parts.port == OLLAMA_PORT and path == "/v1/chat/completions"


def provider() -> dict:
    """What the questions go to: {name, model}, or {} when nothing is set up."""
    url = config.settings.ai_order_url
    if not url:
        return {}
    if _is_ollama(url):
        return {"name": "ollama", "model": config.settings.ai_order_model or OLLAMA_DEFAULT_MODEL}
    return {"name": "openai", "model": config.settings.ai_order_model or OPENAI_DEFAULT_MODEL}


def _ollama_chat_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/api/chat", "", ""))


def _ollama_budget(prompt: str, answer_tokens: int) -> tuple[int, int]:
    """(num_ctx, num_predict) big enough for this prompt and its answer.

    Estimated, not tokenised: log lines, ids and hex tokenise badly — measured
    against qwen2.5 at ~2.7 characters a token, so 2.5 is assumed.
    """
    needed = len(prompt) * 2 // 5 + answer_tokens + 256
    num_ctx = min(_OLLAMA_MAX_CTX, max(_OLLAMA_MIN_CTX, -(-needed // 1024) * 1024))
    return num_ctx, answer_tokens


# A reasoning model may put its working inside <think>…</think>. That is not the
# answer, so it never reaches the browser. An unclosed block (answer cut off
# mid-thought) is dropped to the end.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _content(data: dict) -> str:
    """The assistant text out of an OpenAI-compatible or Ollama response."""
    try:
        if "choices" in data:
            message = data["choices"][0]["message"]
        else:
            message = data["message"]  # Ollama's /api/chat
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The AI service replied in a shape we don't understand.") from exc
    content = message.get("content")
    if isinstance(content, list):
        # Some providers return content as a list of typed parts.
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = _strip_thinking(content or "")
    if not text:
        raise RuntimeError("The AI service returned an empty answer.")
    return text


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST and return the JSON body; RuntimeError with the provider's words."""
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        logger.warning("AI request timed out: %s", exc)
        raise RuntimeError(
            f"The AI service did not answer within {int(timeout)} seconds."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("AI request failed: %s", exc)
        raise RuntimeError(f"Could not reach the AI service: {exc}") from exc
    if response.status_code >= 400:
        # The provider's own words are far more useful than a generic failure —
        # "insufficient quota", "model not found", "invalid api key".
        detail = response.text.strip()[:300] or response.reason_phrase
        logger.warning("AI request rejected (%s): %s", response.status_code, detail)
        raise RuntimeError(f"The AI service refused the request ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("The AI service did not return JSON.") from exc


def _auth_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if config.settings.ai_order_key:
        # Ollama itself has no keys, but one behind an authenticating proxy does.
        headers["Authorization"] = f"Bearer {config.settings.ai_order_key}"
    return headers


def _ask_openai(prompt: str, model: str, answer_tokens: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        # Explaining a log is not a creative task: the same question should get
        # much the same answer twice.
        "temperature": 0.2,
        "max_tokens": answer_tokens,
    }
    data = _post(config.settings.ai_order_url, payload, _auth_headers(), REQUEST_TIMEOUT)
    return {"reply": _content(data), "model": data.get("model") or model}


def _ollama_hint(message: str, model: str) -> str:
    """Add the fix for the two ways a fresh Ollama setup usually fails."""
    if "(404)" in message and "not found" in message:
        return (
            f"{message} — the model has not been downloaded into Ollama yet. "
            f"Run: docker exec {config.settings.rsm_stack}-ollama ollama pull {model} "
            f"(or 'ollama pull {model}' where Ollama is installed)."
        )
    if message.startswith("Could not reach"):
        return (
            f"{message} — is Ollama running, and listening where AI_ORDER_URL "
            "points? An Ollama installed on the host (not in Docker) listens only "
            "on 127.0.0.1 unless it is started with OLLAMA_HOST=0.0.0.0."
        )
    return message


def _ask_ollama(prompt: str, model: str, answer_tokens: int) -> dict:
    """Ollama through its own /api/chat, which — unlike its OpenAI-compatible
    route — accepts a context size for this one request (#199)."""
    headers = _auth_headers()
    num_ctx, num_predict = _ollama_budget(prompt, answer_tokens)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": num_predict},
    }
    if _ollama_thinks(model, headers):
        # A thinking model spends the answer budget on its reasoning first and
        # can run out before it writes a word of the answer (a 12B model did:
        # every token went to thinking, the answer came back empty). Asked not
        # to think, it answers straight away.
        payload["think"] = False
    try:
        data = _post(_ollama_chat_url(config.settings.ai_order_url), payload, headers,
                     OLLAMA_TIMEOUT)
    except RuntimeError as exc:
        raise RuntimeError(_ollama_hint(str(exc), model)) from exc
    try:
        reply = _content(data)
    except RuntimeError as exc:
        raise RuntimeError(_empty_hint(data, model, num_predict) or str(exc)) from exc
    return {"reply": reply, "model": data.get("model") or model}


# model -> does it think? Asked once per model per manager run.
_thinks: dict[str, bool] = {}


def _ollama_thinks(model: str, headers: dict) -> bool:
    """Whether Ollama lists 'thinking' among the model's capabilities.

    Only such a model is sent think=false: an older Ollama, or one that cannot
    say, gets the request exactly as before.
    """
    if model in _thinks:
        return _thinks[model]
    parts = urlsplit(config.settings.ai_order_url)
    show = urlunsplit((parts.scheme, parts.netloc, "/api/show", "", ""))
    try:
        response = httpx.post(show, json={"model": model}, headers=headers, timeout=PROBE_TIMEOUT)
        if response.status_code >= 400:
            return False  # not remembered: ask again next time
        capabilities = response.json().get("capabilities") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.info("Could not ask Ollama what %s can do: %s", model, exc)
        return False
    _thinks[model] = "thinking" in capabilities
    return _thinks[model]


def _empty_hint(data: dict, model: str, budget: int) -> str:
    """Why Ollama's answer came back empty, when its reply says."""
    message = data.get("message") if isinstance(data, dict) else None
    content = (message or {}).get("content") or ""
    thought = bool((message or {}).get("thinking")) or "<think>" in content.lower()
    if data.get("done_reason") == "length":
        return (
            f"{model} used its whole answer budget ({data.get('eval_count') or budget} tokens) "
            f"{'thinking ' if thought else ''}before it wrote an answer. Try a model that "
            f"does not think first, such as {OLLAMA_DEFAULT_MODEL}, or a newer Ollama "
            "(0.9 or later lets the manager switch the thinking off)."
        )
    if thought:
        return (
            f"{model} only thought and gave no answer. Try a model that does not think "
            f"first, such as {OLLAMA_DEFAULT_MODEL}."
        )
    return ""


def ask(prompt: str, answer_tokens: int = DEFAULT_ANSWER_TOKENS) -> dict:
    """Send `prompt` to the configured service, return {reply, model}.

    Raises RuntimeError with a message meant for the user.
    """
    chosen = provider()
    if not chosen:
        raise RuntimeError("No AI service is set up (AI_ORDER_URL in .env).")
    if chosen["name"] == "ollama":
        return _ask_ollama(prompt, chosen["model"], answer_tokens)
    return _ask_openai(prompt, chosen["model"], answer_tokens)


# --------------------------------------------------------------------------- #
# Is it working? (#254)
# --------------------------------------------------------------------------- #
#
# A button that only fails is worse than none, so the Console offers "Ask AI"
# only when this says the service answers. Probing costs a request, so the
# answer is kept for a while; a failed question forgets it, so the next look
# probes again.

STATUS_TTL = 300.0
PROBE_TIMEOUT = 5.0
_status_lock = threading.Lock()
_status_cache: tuple[float, dict] | None = None


def _models_url(url: str) -> str:
    """The OpenAI-style model list next to a chat-completions URL."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/models", "", ""))


def _probe() -> dict:
    chosen = provider()
    if not chosen:
        return {"available": False, "reason": "No AI service is set up (AI_ORDER_URL in .env)."}
    headers = {}
    if config.settings.ai_order_key:
        headers["Authorization"] = f"Bearer {config.settings.ai_order_key}"
    base = {"provider": chosen["name"], "model": chosen["model"]}
    try:
        if chosen["name"] == "ollama":
            parts = urlsplit(config.settings.ai_order_url)
            tags = urlunsplit((parts.scheme, parts.netloc, "/api/tags", "", ""))
            response = httpx.get(tags, headers=headers, timeout=PROBE_TIMEOUT)
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models") or []}
            model = chosen["model"]
            if model not in names and f"{model}:latest" not in names:
                return {**base, "available": False,
                        "reason": f"Ollama answers, but the model {model} is not downloaded."}
            return {**base, "available": True, "reason": ""}
        response = httpx.get(_models_url(config.settings.ai_order_url), headers=headers,
                             timeout=PROBE_TIMEOUT)
        if response.status_code in (401, 403):
            return {**base, "available": False,
                    "reason": f"The AI service refused the key ({response.status_code})."}
        # 404: a service without a model list. It answered, which is what counts.
        if response.status_code >= 500:
            return {**base, "available": False,
                    "reason": f"The AI service is failing ({response.status_code})."}
        return {**base, "available": True, "reason": ""}
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("AI service probe failed: %s", exc)
        return {**base, "available": False, "reason": f"Could not reach the AI service: {exc}"}


def status(refresh: bool = False) -> dict:
    """{available, provider, model, reason}, probed at most every STATUS_TTL."""
    global _status_cache
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache
    if cached and not refresh and now - cached[0] < STATUS_TTL:
        return cached[1]
    result = _probe()
    with _status_lock:
        _status_cache = (now, result)
    return result


def forget_status() -> None:
    """After a failed question: probe again next time instead of trusting the cache."""
    global _status_cache
    with _status_lock:
        _status_cache = None
