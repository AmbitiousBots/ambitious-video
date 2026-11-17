from __future__ import annotations

from ambitious_video.providers.base import Provider, ProviderError, RunResult
from ambitious_video.providers.xai_image import XaiImage
from ambitious_video.providers.xai_speech import XaiSpeech
from ambitious_video.providers.xai_video import XaiVideo

_REGISTERED: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    if provider.id in _REGISTERED:
        raise ValueError(provider.id)
    _REGISTERED[provider.id] = provider


def unregister(provider_id: str) -> None:
    _REGISTERED.pop(provider_id, None)


def find(provider_id: str) -> Provider | None:
    return _REGISTERED.get(provider_id)


def registered() -> tuple[Provider, ...]:
    return tuple(_REGISTERED[name] for name in sorted(_REGISTERED))


register(XaiSpeech())
register(XaiVideo())
register(XaiImage())

__all__ = ["Provider", "ProviderError", "RunResult", "find", "register", "registered", "unregister"]
