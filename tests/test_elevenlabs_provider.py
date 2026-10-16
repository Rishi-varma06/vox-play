"""
ElevenLabs provider tests.

Every one of these mocks the HTTP layer. The provider spends real credits
per call, so a suite that hit the API would bill the person running it and
fail on a machine with no key -- which is most machines, including CI.
"""

from __future__ import annotations

import pytest

from voxplay.tts.base import TTSError
from voxplay.tts.elevenlabs_provider import (
    DEFAULT_MODEL,
    DEFAULT_VOICE_ID,
    ElevenLabsProvider,
)


class FakeResponse:
    def __init__(self, status_code=200, content=b"ID3fake-audio",
                 headers=None, payload=None, text=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers if headers is not None else {"content-type": "audio/mpeg"}
        self._payload = payload
        self.text = text or (str(payload) if payload else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def captured(monkeypatch):
    """Capture the outgoing request instead of sending it."""
    sent = {}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, params=params,
                    json=json, timeout=timeout)
        return sent.get("_response", FakeResponse())

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_writes_mp3_and_returns_its_path(tmp_path, captured):
    provider = ElevenLabsProvider(api_key="sk_test")
    out = provider.synthesize("Morphy brings the knight out.", tmp_path / "beat")

    assert out.suffix == ".mp3"
    assert out.read_bytes() == b"ID3fake-audio"


def test_sends_key_text_and_model(tmp_path, captured):
    provider = ElevenLabsProvider(api_key="sk_test")
    provider.synthesize("The bishop pins the knight.", tmp_path / "b")

    assert captured["headers"]["xi-api-key"] == "sk_test"
    assert captured["json"]["text"] == "The bishop pins the knight."
    assert captured["json"]["model_id"] == DEFAULT_MODEL
    assert DEFAULT_VOICE_ID in captured["url"]


def test_key_comes_from_the_environment(tmp_path, captured, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  sk_from_env  ")
    provider = ElevenLabsProvider()
    provider.synthesize("Check.", tmp_path / "b")

    # Surrounding whitespace is the usual result of pasting into a .env, and
    # it would otherwise be sent as part of the key and rejected as a 401.
    assert captured["headers"]["xi-api-key"] == "sk_from_env"


def test_voice_id_can_be_set_by_environment(tmp_path, captured, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "my-cloned-voice")
    provider = ElevenLabsProvider(api_key="sk_test")
    provider.synthesize("Check.", tmp_path / "b")

    assert "my-cloned-voice" in captured["url"]


def test_missing_key_names_the_variable_and_the_alternative(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    provider = ElevenLabsProvider()

    with pytest.raises(TTSError) as excinfo:
        provider.synthesize("Check.", tmp_path / "b")

    message = str(excinfo.value)
    assert "ELEVENLABS_API_KEY" in message
    assert "gtts" in message


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "rejected the API key"),
        (402, "out of credits"),
        (404, "No such voice"),
        (429, "rate-limited"),
        (500, "ElevenLabs error 500"),
    ],
)
def test_http_failures_explain_themselves(tmp_path, captured, status, expected):
    captured["_response"] = FakeResponse(
        status_code=status, payload={"detail": {"message": "upstream text"}}
    )
    provider = ElevenLabsProvider(api_key="sk_test")

    with pytest.raises(TTSError, match=expected):
        provider.synthesize("Check.", tmp_path / "b")


def test_json_body_with_200_is_not_written_as_audio(tmp_path, captured):
    # The API answers some failures with 200 and an error envelope. Writing
    # that to disk yields a plausible-looking file that is silent, and the
    # timeline would happily measure it.
    captured["_response"] = FakeResponse(
        headers={"content-type": "application/json"},
        payload={"detail": "quota exceeded"},
        content=b'{"detail":"quota exceeded"}',
    )
    provider = ElevenLabsProvider(api_key="sk_test")

    with pytest.raises(TTSError, match="instead of audio"):
        provider.synthesize("Check.", tmp_path / "b")

    assert not (tmp_path / "b.mp3").exists()


def test_empty_body_is_rejected(tmp_path, captured):
    captured["_response"] = FakeResponse(content=b"")
    provider = ElevenLabsProvider(api_key="sk_test")

    with pytest.raises(TTSError, match="empty response"):
        provider.synthesize("Check.", tmp_path / "b")


def test_network_failure_is_reported_as_tts_error(tmp_path, monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", boom)
    provider = ElevenLabsProvider(api_key="sk_test")

    with pytest.raises(TTSError, match="could not reach ElevenLabs"):
        provider.synthesize("Check.", tmp_path / "b")


def test_dotted_game_id_does_not_collapse_clips(tmp_path, captured):
    # with_audio_extension appends rather than replaces, so a PGN date in the
    # stem cannot turn every clip in a game into the same filename.
    provider = ElevenLabsProvider(api_key="sk_test")
    out = provider.synthesize("Check.", tmp_path / "Morphy_1858.11.02_004")

    assert out.name == "Morphy_1858.11.02_004.mp3"
