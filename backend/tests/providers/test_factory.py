import pytest

from studio.config import Settings
from studio.tts.elevenlabs import ElevenLabsProvider
from studio.tts.factory import build_provider


async def test_uses_elevenlabs_only_when_a_key_is_configured() -> None:
    offline = build_provider(Settings(elevenlabs_api_key=None))
    assert offline.name.startswith("offline-")

    remote = build_provider(Settings(elevenlabs_api_key="k", elevenlabs_base_url="http://127.0.0.1:9"))
    assert isinstance(remote, ElevenLabsProvider)
    await remote.aclose()


def test_reads_the_standard_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")
    assert Settings().elevenlabs_api_key == "from-env"
