"""
语音识别封装测试：覆盖 key 轮询与 provider 路由逻辑。
"""
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace

from core import speech_recognition


class RecoverableError(Exception):
    """用于模拟可恢复的 API 错误。"""


def test_api_key_round_robin() -> None:
    pool = speech_recognition.ApiKeyRoundRobin(["k1", "k2", "k3"])
    picked = [pool.get_next_key() for _ in range(5)]
    assert picked == ["k1", "k2", "k3", "k1", "k2"]


def test_create_speech_recognizer_fallback_to_local() -> None:
    recognizer = speech_recognition.create_speech_recognizer(
        provider="groq",
        groq_api_keys=[],
    )
    assert isinstance(recognizer, speech_recognition.LocalWhisperSpeechRecognizer)


def test_groq_recognizer_key_failover(monkeypatch) -> None:
    called_keys = []

    class FakeOpenAI:
        def __init__(self, api_key: str, base_url: str, timeout: int) -> None:
            self._api_key = api_key
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=self._create)
            )

        def _create(self, model: str, file, language: str | None = None):  # noqa: ANN001
            called_keys.append(self._api_key)
            if self._api_key == "bad-key":
                raise RecoverableError("rate limit")
            return SimpleNamespace(text="识别成功")

    monkeypatch.setattr(speech_recognition, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        speech_recognition,
        "_is_recoverable_groq_error",
        lambda exc: isinstance(exc, RecoverableError),
    )

    recognizer = speech_recognition.GroqSpeechRecognizer(
        api_keys=["bad-key", "good-key"],
        model="whisper-large-v3-turbo",
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        path = Path(temp_file.name)

    try:
        text = recognizer.transcribe_file(path, language="zh")
        assert text == "识别成功"
        assert called_keys == ["bad-key", "good-key"]
    finally:
        path.unlink(missing_ok=True)
