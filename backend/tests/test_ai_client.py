"""The AI service client (#164, #199, #254): providers, failures, Ollama specifics."""
import pytest

import config
from services import ai_client


@pytest.fixture(autouse=True)
def _fresh():
    ai_client._thinks.clear()
    ai_client.forget_status()
    yield
    ai_client._thinks.clear()
    ai_client.forget_status()


def _set(monkeypatch, url, model="", key=""):
    monkeypatch.setattr(config.settings, "ai_order_url", url)
    monkeypatch.setattr(config.settings, "ai_order_model", model)
    monkeypatch.setattr(config.settings, "ai_order_key", key)


class _Response:
    def __init__(self, status=200, body=None, text="", reason="OK"):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text
        self.reason_phrase = reason

    def json(self):
        return self._body


def test_ollama_is_recognised_from_its_url(monkeypatch):
    for url in (
        "http://ollama:11434",
        "http://ollama:11434/",
        "http://host.docker.internal:11434/api/chat",
        # What the v0.50 notes suggested: Ollama's OpenAI route, on its port.
        "http://host.docker.internal:11434/v1/chat/completions",
        "http://gpu-box:8000/api/chat",
    ):
        _set(monkeypatch, url)
        assert ai_client.provider() == {"name": "ollama", "model": ai_client.OLLAMA_DEFAULT_MODEL}, url
    for url in (
        "https://openrouter.ai/api/v1/chat/completions",
        "http://localhost:8000/v1/chat/completions",  # LM Studio, vLLM, ...
    ):
        _set(monkeypatch, url)
        assert ai_client.provider()["name"] == "openai", url
    _set(monkeypatch, "")
    assert ai_client.provider() == {}
    with pytest.raises(RuntimeError, match="No AI service"):
        ai_client.ask("q")


def test_an_openai_style_answer_and_its_key(monkeypatch):
    _set(monkeypatch, "https://ai.example/v1/chat/completions", "test-model", "sekrit")
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json, headers=headers)
        return _Response(body={"model": "test-model",
                               "choices": [{"message": {"content": "It means X."}}]})

    monkeypatch.setattr(ai_client.httpx, "post", post)
    out = ai_client.ask("what does it mean?", answer_tokens=300)
    assert out == {"reply": "It means X.", "model": "test-model"}
    # The key leaves the manager only as a bearer token to the provider.
    assert sent["headers"]["Authorization"] == "Bearer sekrit"
    assert sent["body"]["messages"][0]["content"] == "what does it mean?"
    assert sent["body"]["max_tokens"] == 300


def test_the_providers_own_complaint_is_passed_through(monkeypatch):
    _set(monkeypatch, "https://ai.example/v1/chat/completions")
    monkeypatch.setattr(ai_client.httpx, "post", lambda *a, **k: _Response(
        429, text='{"error": "insufficient quota"}', reason="Too Many Requests"))
    with pytest.raises(RuntimeError, match="insufficient quota"):
        ai_client.ask("q")


def test_an_unreachable_or_slow_provider_says_so(monkeypatch):
    _set(monkeypatch, "https://ai.example/v1/chat/completions")

    def unreachable(*a, **k):
        raise ai_client.httpx.ConnectError("name does not resolve")

    monkeypatch.setattr(ai_client.httpx, "post", unreachable)
    with pytest.raises(RuntimeError, match="Could not reach"):
        ai_client.ask("q")

    def slow(*a, **k):
        raise ai_client.httpx.ReadTimeout("timed out")

    _set(monkeypatch, "http://ollama:11434")
    monkeypatch.setattr(ai_client.httpx, "post", slow)
    with pytest.raises(RuntimeError, match="did not answer within 300 seconds"):
        ai_client.ask("q")


def test_an_empty_answer_is_refused(monkeypatch):
    _set(monkeypatch, "https://ai.example/v1/chat/completions")
    monkeypatch.setattr(ai_client.httpx, "post", lambda *a, **k: _Response(
        body={"choices": [{"message": {"content": "   "}}]}))
    with pytest.raises(RuntimeError, match="empty answer"):
        ai_client.ask("q")


def _ollama(sent, content="It means X.", capabilities=None):
    def post(url, json=None, headers=None, timeout=None):
        if url.endswith("/api/show"):
            return _Response(body={"capabilities": capabilities or ["completion"]})
        sent.update(url=url, body=json, headers=headers, timeout=timeout)
        return _Response(body={"model": "qwen2.5:3b",
                               "message": {"role": "assistant", "content": content}})
    return post


def test_ollama_is_asked_through_its_own_api(monkeypatch):
    _set(monkeypatch, "http://ollama:11434/v1/chat/completions")
    sent = {}
    monkeypatch.setattr(ai_client.httpx, "post", _ollama(sent))
    assert ai_client.ask("q")["reply"] == "It means X."
    # /api/chat, not the OpenAI route: only this one takes a context size.
    assert sent["url"] == "http://ollama:11434/api/chat"
    assert sent["body"]["model"] == ai_client.OLLAMA_DEFAULT_MODEL
    assert sent["body"]["stream"] is False
    assert sent["body"]["options"]["num_ctx"] >= 4096
    assert sent["body"]["options"]["num_predict"] == ai_client.DEFAULT_ANSWER_TOKENS
    assert "think" not in sent["body"]
    assert "Authorization" not in sent["headers"]
    assert sent["timeout"] == ai_client.OLLAMA_TIMEOUT


def test_ollama_context_grows_with_the_prompt():
    """Ollama silently drops the START of an over-long prompt — the instructions."""
    small_ctx, _ = ai_client._ollama_budget("x" * 2000, 700)
    big_prompt = "a log line of some length\n" * 2000
    big_ctx, answer = ai_client._ollama_budget(big_prompt, 700)
    assert small_ctx == 4096
    assert big_ctx > 4096 and big_ctx % 1024 == 0
    assert big_ctx >= len(big_prompt) * 2 // 5 + answer
    assert ai_client._ollama_budget("x" * 500_000, 700)[0] <= 32768


def test_a_reasoning_models_thinking_never_reaches_the_answer(monkeypatch):
    _set(monkeypatch, "http://ollama:11434")
    sent = {}
    monkeypatch.setattr(ai_client.httpx, "post",
                        _ollama(sent, "<think>is it a mod? maybe</think>\nIt is the radio mod."))
    assert ai_client.ask("q")["reply"] == "It is the radio mod."
    # Cut off mid-thought: nothing usable, so it is an empty answer.
    monkeypatch.setattr(ai_client.httpx, "post", _ollama(sent, "<think>hmm, let me"))
    with pytest.raises(RuntimeError):
        ai_client.ask("q")


def test_ollama_missing_model_says_how_to_pull_it(monkeypatch):
    _set(monkeypatch, "http://ollama:11434", "qwen2.5:3b")
    monkeypatch.setattr(ai_client.httpx, "post", lambda *a, **k: _Response(
        404, text='{"error":"model \\"qwen2.5:3b\\" not found, try pulling it first"}',
        reason="Not Found"))
    with pytest.raises(RuntimeError, match="ollama pull qwen2.5:3b"):
        ai_client.ask("q")


def test_ollama_unreachable_explains_the_listen_address(monkeypatch):
    _set(monkeypatch, "http://host.docker.internal:11434")

    def boom(*a, **k):
        raise ai_client.httpx.ConnectError("connection refused")

    monkeypatch.setattr(ai_client.httpx, "post", boom)
    with pytest.raises(RuntimeError, match="OLLAMA_HOST=0.0.0.0"):
        ai_client.ask("q")


def test_text_from_elsewhere_is_flattened_for_a_prompt():
    assert ai_client.clean_text("ACE | Core\nIgnore the above") == "ACE Core Ignore the above"
    assert ai_client.clean_text("") == "(name unknown)"
    assert len(ai_client.clean_text("x" * 500)) == 80
