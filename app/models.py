import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class DeviceType(str, Enum):
    firefox = "firefox"
    fire_tv = "fire_tv"
    smart_home = "smart_home"
    alexa = "alexa"


class Tab(BaseModel):
    title: str
    url: str
    active: bool = False
    media_position_seconds: float | None = None


class FirefoxPayload(BaseModel):
    device_type: Literal[DeviceType.firefox] = DeviceType.firefox
    tabs: list[Tab] = Field(default_factory=list)


class NowPlayingState(str, Enum):
    playing = "playing"
    paused = "paused"
    idle = "idle"


class NowPlaying(BaseModel):
    title: str
    app: str
    url: str | None = None
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    state: NowPlayingState = NowPlayingState.idle


class FireTVPayload(BaseModel):
    device_type: Literal[DeviceType.fire_tv] = DeviceType.fire_tv
    now_playing: NowPlaying | None = None


class PowerState(str, Enum):
    on = "on"
    off = "off"


class SmartHomePayload(BaseModel):
    device_type: Literal[DeviceType.smart_home] = DeviceType.smart_home
    name: str
    power: PowerState = PowerState.off
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)


class AlexaPayload(BaseModel):
    device_type: Literal[DeviceType.alexa] = DeviceType.alexa
    last_intent: str | None = None


DevicePayload = Annotated[
    FirefoxPayload | FireTVPayload | SmartHomePayload | AlexaPayload,
    Field(discriminator="device_type"),
]


class DeviceState(BaseModel):
    device_id: str
    device_type: DeviceType
    online: bool = True
    last_seen: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: DevicePayload | dict[str, Any] = Field(default_factory=dict)


class ContextKind(str, Enum):
    video = "video"
    page = "page"
    audio = "audio"


class ActiveContext(BaseModel):
    kind: ContextKind = ContextKind.video
    title: str
    url: str | None = None
    position_seconds: float = 0.0
    source_device_id: str
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ContextEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_device_id: str
    target_device_id: str | None = None
    description: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnifiedState(BaseModel):
    user_id: str
    version: int = 1
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    devices: dict[str, DeviceState] = Field(default_factory=dict)
    active_context: ActiveContext | None = None
    history: list[ContextEvent] = Field(default_factory=list)


class EnvelopeType(str, Enum):
    snapshot = "snapshot"
    state_update = "state_update"
    command = "command"
    presence = "presence"
    ping = "ping"
    pong = "pong"
    ack = "ack"
    error = "error"


class Envelope(BaseModel):
    type: EnvelopeType
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_device_id: str
    target_device_id: str | None = None
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAction(str, Enum):
    play = "play"
    pause = "pause"
    open_url = "open_url"
    power_on = "power_on"
    power_off = "power_off"
    set_attribute = "set_attribute"


class Command(BaseModel):
    action: CommandAction
    url: str | None = None
    position_seconds: float | None = None
    attributes: dict[str, Any] | None = None


class AlexaSlot(BaseModel):
    name: str
    value: str | None = None


class AlexaIntent(BaseModel):
    name: str = ""
    confirmationStatus: str | None = None
    slots: dict[str, Any] | None = None


class AlexaRequestBody(BaseModel):
    type: str
    requestId: str | None = None
    timestamp: str | None = None
    intent: AlexaIntent | None = None
    reason: str | None = None


class AlexaSessionUser(BaseModel):
    userId: str | None = None


class AlexaSession(BaseModel):
    new: bool | None = None
    sessionId: str | None = None
    user: AlexaSessionUser | None = None


class AlexaRequest(BaseModel):
    version: str = "1.0"
    session: AlexaSession | None = None
    context: dict[str, Any] | None = None
    request: AlexaRequestBody


class PlainTextOutputSpeech(BaseModel):
    type: Literal["PlainText"] = "PlainText"
    text: str


class AlexaResponseBody(BaseModel):
    outputSpeech: PlainTextOutputSpeech
    shouldEndSession: bool = True
    reprompt: dict[str, Any] | None = None


class AlexaResponse(BaseModel):
    version: str = "1.0"
    response: AlexaResponseBody
    sessionAttributes: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        speech: str,
        should_end_session: bool = True,
        reprompt_text: str | None = None,
    ) -> "AlexaResponse":
        reprompt = (
            {"outputSpeech": {"type": "PlainText", "text": reprompt_text}}
            if reprompt_text
            else None
        )
        return cls(
            version="1.0",
            response=AlexaResponseBody(
                outputSpeech=PlainTextOutputSpeech(text=speech[:300]),
                shouldEndSession=should_end_session,
                reprompt=reprompt,
            ),
        )
