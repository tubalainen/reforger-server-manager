"""Mod load order help: the prompt, and the optional one-click AI call (#164).

Two things live here and nothing else:

* `build_prompt` — the text a language model is asked to answer. It is built
  here, never in the browser and never from free text supplied by the caller,
  so this endpoint is a mod-ordering feature and not a general relay to a
  third-party AI from the server's IP address.
* `ask` — an OpenAI-compatible chat completion, used only when the operator has
  configured one (AI_ORDER_URL). With nothing configured the manager makes no
  outbound AI request at all; the wizard falls back to handing the same prompt
  to the user's own browser, which is the route that always works.

The reply is returned to the browser as text and parsed there (frontend
`mods.js`), because the same parser has to handle an answer pasted in by hand.
Nothing here decides a mod order: it proposes one, the user applies it.
"""
import logging
import re

import httpx

import config

logger = logging.getLogger("manager.mod_order")

# Bigger than any real Reforger mod list; a cap keeps one request from turning
# into a very large prompt (and a very large bill for whoever's key it is).
MAX_MODS = 200

REQUEST_TIMEOUT = 90.0  # models are slow; the wizard shows a spinner meanwhile

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


def _content(data: dict) -> str:
    """The assistant text out of an OpenAI-compatible response."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The AI service replied in a shape we don't understand.") from exc
    content = message.get("content")
    if isinstance(content, list):
        # Some providers return content as a list of typed parts.
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = (content or "").strip()
    if not text:
        raise RuntimeError("The AI service returned an empty answer.")
    return text


def ask(prompt: str) -> dict:
    """Send `prompt` to the configured provider, return {reply, model}.

    Raises RuntimeError with a message meant for the user — every failure here
    is recoverable, because the wizard can always fall back to the copy-and-paste
    route with the very same prompt.
    """
    settings = config.settings
    model = settings.ai_order_model or "gpt-4o-mini"
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
    try:
        response = httpx.post(
            settings.ai_order_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
        )
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
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("The AI service did not return JSON.") from exc
    return {"reply": _content(data), "model": data.get("model") or model}
