from .base import TTSProvider, TTSError
from .gtts_provider import GTTSProvider
from .elevenlabs_provider import ElevenLabsProvider
from .mock_provider import MockTTSProvider
from .offline_provider import OfflineTTSProvider
from .assembler import assemble


def get_provider(name: str = "gtts") -> TTSProvider:
    """Factory function returning a TTSProvider by name."""
    providers = {
        "gtts": GTTSProvider,
        "elevenlabs": ElevenLabsProvider,
        "mock": MockTTSProvider,
        "offline": OfflineTTSProvider,
    }
    if name not in providers:
        raise ValueError(
            f"Unknown TTS provider '{name}'. "
            f"Available: {', '.join(providers)}"
        )
    return providers[name]()


__all__ = [
    "TTSProvider",
    "TTSError",
    "GTTSProvider",
    "ElevenLabsProvider",
    "MockTTSProvider",
    "OfflineTTSProvider",
    "assemble",
    "get_provider",
]
