"""Mod load order help: the prompt, and the optional one-click AI call (#164).

Two things live here and nothing else:

* `build_prompt` — the text a language model is asked to answer. It is built
  here, never in the browser and never from free text supplied by the caller,
  so this endpoint is a mod-ordering feature and not a general relay to a
  third-party AI from the server's IP address.
* `ask` — a chat completion, used only when the operator has configured one
  (AI_ORDER_URL): any OpenAI-compatible endpoint, or a local Ollama through its
  own API (#199). With nothing configured the manager makes no outbound AI
  request at all; the wizard falls back to handing the same prompt to the
  user's own browser, which is the route that always works.

The reply is returned to the browser as text and parsed there (frontend
`mods.js`), because the same parser has to handle an answer pasted in by hand.
Nothing here decides a mod order: it proposes one, the user applies it.
"""
import logging
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

import config

logger = logging.getLogger("manager.mod_order")

# Bigger than any real Reforger mod list; a cap keeps one request from turning
# into a very large prompt (and a very large bill for whoever's key it is).
MAX_MODS = 200

REQUEST_TIMEOUT = 90.0  # models are slow; the wizard shows a spinner meanwhile

# A local model on a small GPU first has to be loaded into memory, and may spill
# onto the CPU for part of a long answer — minutes, not seconds.
OLLAMA_TIMEOUT = 300.0

OLLAMA_PORT = 11434

# The model suggested for Ollama, and used when AI_ORDER_MODEL is left empty:
# ~1.9 GB of weights at Ollama's default 4-bit quantisation, a small KV cache
# (so a long mod list still fits beside it on a 4 GB card), good at following a
# strict output format, and not a "thinking" model that would spend the time
# budget reasoning before it answers.
OLLAMA_DEFAULT_MODEL = "qwen2.5:3b"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

# Context window bounds for Ollama. Its default window (2–4k tokens, depending
# on the version) is smaller than the prompt for a big mod list, and Ollama does
# not refuse an over-long prompt: it silently drops the START of it — the
# instructions — so the model answers a question it never saw.
_OLLAMA_MIN_CTX = 4096
_OLLAMA_MAX_CTX = 32768

_ASSET_ID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")

# Mod names come off the Workshop — someone else's text going into a prompt. A
# name is flattened to one line and clipped so it cannot forge extra rows, rules
# or instructions in the listing below.
_UNSAFE_IN_NAME = re.compile(r"[\r\n|`]+")
_NAME_LIMIT = 80


def _clean_name(name: str | None) -> str:
    text = _UNSAFE_IN_NAME.sub(" ", (name or "").strip())
    text = " ".join(text.split())
    if len(text) > _NAME_LIMIT:
        text = text[: _NAME_LIMIT - 1].rstrip() + "…"
    return text or "(name unknown)"


def normalize_mods(raw: object) -> list[dict]:
    """Validate the caller's mod list into [{modId, name, explicit, requires}].

    Raises ValueError with a message meant for the user.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Body must be {\"mods\": [{modId, name, explicit, dependencies}, ...]}")
    if len(raw) > MAX_MODS:
        raise ValueError(f"That is more than {MAX_MODS} mods — too many to order in one request.")
    known: set[str] = set()
    mods: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Every mod must be an object with a modId.")
        mod_id = str(entry.get("modId", "")).strip().upper()
        if not _ASSET_ID_RE.match(mod_id):
            raise ValueError(f"{mod_id or '(empty)'!r} is not a Workshop mod id.")
        if mod_id in known:
            continue  # the same mod twice is one mod
        known.add(mod_id)
        deps = entry.get("dependencies")
        requires = []
        if isinstance(deps, list):
            for d in deps:
                d = str(d).strip().upper()
                if _ASSET_ID_RE.match(d):
                    requires.append(d)
        mods.append({
            "modId": mod_id,
            "name": _clean_name(entry.get("name")),
            "explicit": bool(entry.get("explicit", True)),
            "requires": requires,
        })
    # A "requires" pointing outside the list would ask the model to order a mod
    # that isn't there.
    for m in mods:
        m["requires"] = [d for d in m["requires"] if d in known and d != m["modId"]]
    return mods


def build_prompt(mods: list[dict]) -> str:
    """The question put to the model — also the text the user can copy and paste.

    Written to survive being answered by anything from a small local model to a
    frontier chat assistant: the data is a fixed-width listing, the rules are
    numbered in priority order, the output contract states the exact number of
    lines and shows one, and every rule that matters says what NOT to do. The
    parser on the other side only needs the ids in order, so a model that adds
    commentary or wraps the answer in a code fence still produces a usable
    answer — but a model that follows this exactly produces one we can explain
    back to the user.
    """
    n = len(mods)
    rows = []
    for i, m in enumerate(mods, 1):
        origin = "you chose it" if m["explicit"] else "dependency"
        requires = ", ".join(m["requires"]) if m["requires"] else "nothing"
        rows.append(f"{i:>3} | {m['modId']} | {m['name']} | {origin} | {requires}")
    listing = "\n".join(rows)
    # The example uses a real id (so the model copies a real one) but a made-up
    # name: a Workshop name is untrusted text and appears once, in its own row.
    example = mods[0]["modId"]
    return f"""\
You are ordering the mod list of an Arma Reforger dedicated server.

Below is the `mods` array from that server's config.json, in the order it has
now. Work out the best load order for these mods and return them in that order.

What "best" means here: a mod that builds on another mod is listed after it, and
a mod whose job is to change, patch or add compatibility between other mods is
listed after everything it touches — a later entry is the one that gets the last
word. Frameworks and shared libraries therefore belong near the top, and small
patches at the bottom.

CURRENT ORDER — position | mod id | name | why it is in the list | requires
{listing}

RULES, most important first
1. Return every one of the {n} mod ids above, each exactly once. Never add a mod
   that is not in the list, never drop one, never merge two, and never correct or
   re-type an id: the ids are opaque 16-character codes, copy them exactly.
2. List every mod after each mod named in its "requires".
3. Otherwise order by what a mod is: shared frameworks, libraries and core mods
   first; then terrains; then content (factions, weapons, vehicles, uniforms,
   sounds); then gameplay and scenario mods; and last of all anything that
   patches, tweaks, overrides or adds compatibility between other mods.
4. If you do not recognise a mod, or its name does not tell you what it does,
   leave it where it is. Keeping a mod in place is always better than guessing.
   Do not reorder anything for tidiness, and do not sort alphabetically.

OUTPUT — follow exactly, and print nothing before it
Print exactly {n} lines, one mod per line, in your new order, formatted:
<mod id> | <name> | <reason it sits here, at most 8 words>

For example, a line looks like this:
{example} | Example Mod Name | nothing depends on it, safe early

After the list you may add up to three sentences of remarks. Do not put any mod
id in the remarks.
"""


def configured() -> bool:
    """True when the operator has set up a one-click provider."""
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
    """What the one-click button talks to: {name, model}, or {} when unset."""
    url = config.settings.ai_order_url
    if not url:
        return {}
    if _is_ollama(url):
        return {"name": "ollama", "model": config.settings.ai_order_model or OLLAMA_DEFAULT_MODEL}
    return {"name": "openai", "model": config.settings.ai_order_model or OPENAI_DEFAULT_MODEL}


def _ollama_chat_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/api/chat", "", ""))


def _ollama_budget(prompt: str, mod_count: int) -> tuple[int, int]:
    """(num_ctx, num_predict) big enough for this prompt and its answer.

    Estimated, not tokenised: hex ids and table rows tokenise badly — measured
    against qwen2.5 at ~2.7 characters a token, so 2.5 is assumed — and ~48
    tokens for each answer line (id, name, a short reason; ~26 measured). The answer budget also stops a small
    model that starts repeating itself from running until the timeout.
    """
    answer = mod_count * 48 + 256
    needed = len(prompt) * 2 // 5 + answer + 256
    num_ctx = min(_OLLAMA_MAX_CTX, max(_OLLAMA_MIN_CTX, -(-needed // 1024) * 1024))
    return num_ctx, answer


# A reasoning model may put its working inside <think>…</think>. That text is
# full of mod ids in no useful order, and the parser takes ids in order — so it
# must never reach the browser. An unclosed block (answer cut off mid-thought)
# is dropped to the end.
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
        logger.warning("AI order request timed out: %s", exc)
        raise RuntimeError(
            f"The AI service did not answer within {int(timeout)} seconds."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("AI order request failed: %s", exc)
        raise RuntimeError(f"Could not reach the AI service: {exc}") from exc
    if response.status_code >= 400:
        # The provider's own words are far more useful than a generic failure —
        # "insufficient quota", "model not found", "invalid api key".
        detail = response.text.strip()[:300] or response.reason_phrase
        logger.warning("AI order request rejected (%s): %s", response.status_code, detail)
        raise RuntimeError(f"The AI service refused the request ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("The AI service did not return JSON.") from exc


def _ask_openai(prompt: str, model: str) -> dict:
    settings = config.settings
    headers = {"Content-Type": "application/json"}
    if settings.ai_order_key:
        headers["Authorization"] = f"Bearer {settings.ai_order_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        # Ordering a list is not a creative task; the same list should come back
        # the same way twice.
        "temperature": 0.2,
    }
    data = _post(settings.ai_order_url, payload, headers, REQUEST_TIMEOUT)
    return {"reply": _content(data), "model": data.get("model") or model}


def _ollama_hint(message: str, model: str) -> str:
    """Add the fix for the two ways a fresh Ollama setup usually fails."""
    if "(404)" in message and "not found" in message:
        return (
            f"{message} — the model has not been downloaded into Ollama yet. "
            f"Run: docker exec reforger-ollama ollama pull {model} "
            f"(or 'ollama pull {model}' where Ollama is installed)."
        )
    if message.startswith("Could not reach"):
        return (
            f"{message} — is Ollama running, and listening where AI_ORDER_URL "
            "points? An Ollama installed on the host (not in Docker) listens only "
            "on 127.0.0.1 unless it is started with OLLAMA_HOST=0.0.0.0."
        )
    return message


def _ask_ollama(prompt: str, model: str, mod_count: int) -> dict:
    """Ollama through its own /api/chat, which — unlike its OpenAI-compatible
    route — accepts a context size for this one request (#199)."""
    settings = config.settings
    headers = {"Content-Type": "application/json"}
    if settings.ai_order_key:
        # Ollama itself has no keys, but one behind an authenticating proxy does.
        headers["Authorization"] = f"Bearer {settings.ai_order_key}"
    num_ctx, num_predict = _ollama_budget(prompt, mod_count)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": num_predict},
    }
    try:
        data = _post(_ollama_chat_url(settings.ai_order_url), payload, headers, OLLAMA_TIMEOUT)
    except RuntimeError as exc:
        raise RuntimeError(_ollama_hint(str(exc), model)) from exc
    return {"reply": _content(data), "model": data.get("model") or model}


def ask(prompt: str, mod_count: int = MAX_MODS) -> dict:
    """Send `prompt` to the configured provider, return {reply, model}.

    Raises RuntimeError with a message meant for the user — every failure here
    is recoverable, because the wizard can always fall back to the copy-and-paste
    route with the very same prompt.
    """
    chosen = provider()
    if chosen["name"] == "ollama":
        return _ask_ollama(prompt, chosen["model"], mod_count)
    return _ask_openai(prompt, chosen["model"])
