from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.bedrock_service import BedrockService
from app.config import Settings
from app.models import (
    ActiveContext,
    DeviceState,
    DeviceType,
    FirefoxPayload,
    FireTVPayload,
    NowPlaying,
    NowPlayingState,
    PowerState,
    SmartHomePayload,
    Tab,
    UnifiedState,
)


@pytest.mark.asyncio
async def test_bedrock_mock_keyword_branches() -> None:
    service = BedrockService(Settings(USE_AWS=False))
    user_id = "test-ai-user"

    # 1. Empty context
    empty_state = UnifiedState(user_id=user_id)
    speech, degraded = await service.answer("what is open", empty_state)
    assert degraded is False
    assert "no open tabs" in speech.lower()

    speech, _ = await service.answer("what is playing", empty_state)
    assert "nothing is currently playing" in speech.lower()

    speech, _ = await service.answer("is the light on", empty_state)
    assert "no smart home devices" in speech.lower()

    speech, _ = await service.answer("continue media", empty_state)
    assert "nothing is active to continue right now." in speech.lower()

    # 2. Browser tabs branch
    ff_state = UnifiedState(
        user_id=user_id,
        devices={
            "ff-1": DeviceState(
                device_id="ff-1",
                device_type=DeviceType.firefox,
                payload=FirefoxPayload(
                    tabs=[Tab(title="Hacker News", url="https://news.ycombinator.com", active=True)]
                ),
            )
        },
    )
    speech, _ = await service.answer("what's open on my browser", ff_state)
    assert "Hacker News" in speech

    # 3. Fire TV branch
    tv_state = UnifiedState(
        user_id=user_id,
        devices={
            "tv-1": DeviceState(
                device_id="tv-1",
                device_type=DeviceType.fire_tv,
                payload=FireTVPayload(
                    now_playing=NowPlaying(
                        title="Fallout",
                        app="Prime Video",
                        state=NowPlayingState.playing,
                    )
                ),
            )
        },
    )
    speech, _ = await service.answer("what am i watching on tv", tv_state)
    assert "Fallout" in speech
    assert "Prime Video" in speech

    # 4. Smart home branch
    sh_state = UnifiedState(
        user_id=user_id,
        devices={
            "light-1": DeviceState(
                device_id="light-1",
                device_type=DeviceType.smart_home,
                payload=SmartHomePayload(name="Kitchen Light", power=PowerState.on),
            )
        },
    )
    speech, _ = await service.answer("smart home status", sh_state)
    assert "Kitchen Light is on" in speech


@pytest.mark.asyncio
async def test_bedrock_continue_freshness_gate() -> None:
    service = BedrockService(Settings(USE_AWS=False, ACTIVE_CONTEXT_MAX_AGE_SECONDS=1800))
    user_id = "test-freshness-user"
    now = datetime.now(UTC)

    # Fresh context (10 seconds ago)
    fresh_state = UnifiedState(
        user_id=user_id,
        active_context=ActiveContext(
            title="Dune 2",
            position_seconds=1200.0,
            source_device_id="tv-1",
            updated_at=(now - timedelta(seconds=10)).isoformat(),
        ),
    )
    speech, _ = await service.answer("continue watching", fresh_state)
    assert "Dune 2" in speech
    assert "1200" in speech

    # Stale context (3600 seconds ago > 1800s limit)
    stale_state = UnifiedState(
        user_id=user_id,
        active_context=ActiveContext(
            title="Dune 2",
            position_seconds=1200.0,
            source_device_id="tv-1",
            updated_at=(now - timedelta(seconds=3600)).isoformat(),
        ),
    )
    speech_stale, _ = await service.answer("continue watching", stale_state)
    assert "nothing is active to continue right now." in speech_stale.lower()


@pytest.mark.asyncio
async def test_bedrock_aws_fallback_and_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    service = BedrockService(Settings(USE_AWS=True))
    state = UnifiedState(user_id="u1")

    # 1. When converse raises, degraded=True and fallback speech is returned
    mock_client_fail = MagicMock()
    mock_client_fail.converse.side_effect = RuntimeError("Bedrock Throttling")
    monkeypatch.setattr(service, "_get_client", lambda: mock_client_fail)

    speech, degraded = await service.answer("what is open", state)
    assert degraded is True
    assert "no open tabs" in speech.lower()

    # 2. Output is truncated at 300 chars
    mock_client_long = MagicMock()
    long_text = "A" * 400
    mock_client_long.converse.return_value = {"output": {"message": {"content": [{"text": long_text}]}}}
    monkeypatch.setattr(service, "_get_client", lambda: mock_client_long)

    speech_long, degraded2 = await service.answer("what is open", state)
    assert degraded2 is False
    assert len(speech_long) == 300
