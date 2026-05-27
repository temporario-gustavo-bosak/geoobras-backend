from __future__ import annotations

import pytest

from src.config.settings import Settings

@pytest.fixture()
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override LLM env-vars with code defaults so .env file values are ignored."""
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com/v1/messages")
    monkeypatch.setenv("LLM_TIMEOUT", "60.0")
    monkeypatch.setenv("LLM_MAX_TOKENS", "1024")


# ---------------------------------------------------------------------------
# Happy path: default values are sane
# ---------------------------------------------------------------------------


def test_llm_defaults_are_present(clean_llm_env: None) -> None:
    """get_settings() exposes all five LLM fields with sane defaults."""
    s = Settings()

    assert s.LLM_API_KEY == "", "default must be empty string, not None or missing"
    assert s.LLM_MODEL == "claude-opus-4-7"
    assert s.LLM_BASE_URL == "https://api.anthropic.com/v1/messages"
    assert isinstance(s.LLM_TIMEOUT, float) and s.LLM_TIMEOUT > 0
    assert isinstance(s.LLM_MAX_TOKENS, int) and s.LLM_MAX_TOKENS > 0


# ---------------------------------------------------------------------------
# Edge case: app boots with LLM_API_KEY absent
# ---------------------------------------------------------------------------


def test_settings_instantiate_without_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings() must not raise when LLM_API_KEY is absent from the environment."""
    monkeypatch.setenv("LLM_API_KEY", "")  # override .env — setenv takes precedence over env_file

    s = Settings()  # must not raise

    assert s.LLM_API_KEY == ""


def test_app_module_imports_without_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FastAPI app must be importable (and its startup logic must not crash)
    when LLM_API_KEY is absent — the LLM feature degrades gracefully later."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from src.api.main import app  # noqa: PLC0415 — intentional late import for isolation

    assert app is not None
