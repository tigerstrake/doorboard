"""Typed configuration for door-api session timeouts and durations.

All durations are in seconds. Defaults match ARCHITECTURE.md §8 and the T-401 brief.
Override via environment variables prefixed with ``DOOR_API_``.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass

logger = logging.getLogger("door_api.config")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a trimmed, blank-free tuple.

    Unset uses ``default``; an explicitly empty/blank value also falls back to
    ``default`` so an operator can't accidentally disable topic subscription by
    leaving the value empty.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or default


# Browser origins always permitted by CORS: the two local dev-server origins the
# on-Pi kiosk build has always used. Keeping these as the baseline means the
# CORS policy is unchanged when DOOR_API_CORS_ORIGINS is unset.
_DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def _cors_origins_from_env(name: str) -> tuple[str, ...]:
    """Resolve allowed CORS origins: the defaults plus any comma-separated
    extras from ``name`` (trimmed; blanks ignored; order-preserving, deduped)."""
    origins = list(_DEFAULT_CORS_ORIGINS)
    for part in os.environ.get(name, "").split(","):
        origin = part.strip()
        if origin and origin not in origins:
            origins.append(origin)
    return tuple(origins)


@dataclass(frozen=True, kw_only=True)
class SessionConfig:
    """Timeouts and durations for the visitor session state machine.

    Each value is documented with its purpose and default rationale.
    """

    # How long to wait for the bell to be answered before UNANSWERED_TIMEOUT (seconds).
    ring_timeout_s: float = 30.0

    # How long VISITOR_MODE persists without interaction before auto-transitioning
    # to RINGING (seconds). Immediate in most flows; this is the maximum.
    visitor_mode_auto_ring_s: float = 2.0

    # How long to wait in UNANSWERED_TIMEOUT / ANSWERED before offering video message (seconds).
    offer_delay_s: float = 3.0

    # Maximum recording duration for a video message (seconds).
    max_recording_s: float = 60.0

    # How long VIDEO_MESSAGE_REVIEW stays active before auto-SESSION_END (seconds).
    # MUST be >= max_recording_s plus decision margin: the review timer is a fixed
    # countdown from entering REVIEW and is NOT paused while the visitor plays the
    # clip back, so a value below the recording ceiling silently auto-discards a
    # full-length message mid-review (trigger "timeout:review" -> outcome
    # "abandoned"). Default comfortably fits watching a 60s message and saving it.
    review_timeout_s: float = 180.0

    # How long VIDEO_MESSAGE_SAVED shows confirmation before SESSION_END (seconds).
    saved_linger_s: float = 5.0

    # Inactivity timeout: if no transition occurs within this many seconds, the
    # session auto-expires (seconds). In practice this only schedules a timer for
    # VIDEO_MESSAGE_OFFERED — every other non-IDLE state has a timer of its own —
    # so it is really "how long the door waits while a visitor decides what to do".
    #
    # Ten minutes because that state is where a visitor sits while writing a note on
    # their phone. It is a cap rather than a completion signal: a submitted note is a
    # social write and causes no transition, so the session runs the full window even
    # after the visitor has finished. The DoorPad allows the same budget
    # (VISITOR_WRITING_TIMEOUT_MS in door-ui/src/doorpadTimeouts.ts). The two must
    # agree: whichever is shorter is the real limit, and when this was 120s the
    # server cut a visitor off two and a half minutes into a message the doorboard
    # had promised them ten for.
    #
    # It also bounds how long a persisted session survives a door-api restart
    # (see restore_from_persistence), which the same reasoning favours: a restart
    # mid-message should not silently discard the visitor's session.
    inactivity_timeout_s: float = 600.0

    # APPROACH_DETECTED / IDENTITY_CACHED expire back to IDLE after this long
    # with no button press (seconds).
    approach_timeout_s: float = 10.0

    # SESSION_END lingers briefly before auto-transitioning to IDLE (seconds).
    session_end_linger_s: float = 3.0

    # SQLite database path. Must be provided explicitly or loaded via from_env().
    db_path: str

    # Door identifier included on locally emitted feedback events.
    door_id: str = "primary"

    # Browser origins allowed by CORS. Defaults to the two localhost dev origins;
    # DOOR_API_CORS_ORIGINS adds extras (e.g. http://door-pi.local:5173) so the
    # owner can open /admin over the LAN.
    cors_origins: tuple[str, ...] = _DEFAULT_CORS_ORIGINS

    # door-media base URL used for fire-and-forget recording lifecycle forwarding.
    # door-media binds 127.0.0.1:8082 (see deploy/pi-door doorboard.env.example);
    # the old :8001 default was wrong and silently broke media forwarding whenever
    # DOOR_API_MEDIA_BASE_URL was not set explicitly.
    media_base_url: str = "http://127.0.0.1:8082"

    # Browser-reachable media URL for local DoorPad playback.
    media_public_base_url: str = "http://127.0.0.1:8082"

    # Bounded timeout for door-api -> door-media local loopback calls.
    media_timeout_s: float = 1.0
    media_admin_token: str = ""
    media_outbox_max_rows: int = 4096
    media_forward_poll_s: float = 0.25
    media_retry_base_s: float = 0.5
    media_retry_max_s: float = 30.0

    # door-visiond local base URL. Used only to forward the doorpad's self-service
    # enrollment request (ADR-0019): the kiosks connect to door-api and nothing else
    # (ARCHITECTURE.md §7), so a doorpad action that needs visiond has to come
    # through here. Nothing about recognition or identity flows this way.
    visiond_base_url: str = "http://127.0.0.1:8081"
    visiond_timeout_s: float = 3.0

    # door-sync local base URL for non-critical admin/gallery operations.
    sync_base_url: str = "http://127.0.0.1:8083"
    sync_admin_token: str = ""
    sync_timeout_s: float = 1.0
    sync_outbox_max_rows: int = 4096
    sync_forward_poll_s: float = 0.25
    sync_retry_base_s: float = 0.5
    sync_retry_max_s: float = 30.0

    # Shared secret door-visiond presents on POST /internal/events, the hop that
    # carries recognised identities into the session machine and onto the kiosk
    # WebSocket (ADR-0018 §3). Empty closes the route with 503 — an unauthenticated
    # identity ingest would let anything that can reach door-api assert who is at
    # the door, which is the one claim personalisation reads.
    internal_event_token: str = ""

    # Feature gate for the explicit photo-booth + private gallery flow.
    feature_photobooth: bool = False

    # Short-lived visitor QR tokens.  If unset, a per-process boot secret is used.
    visitor_token_secret: str = ""
    # Matches inactivity_timeout_s: with the DoorPad allowing ten minutes to write
    # a message, a five-minute token made the token the thing that cut the visitor
    # off instead. The exposure is one session's scoped capability (read snapshot,
    # leave a note, vote, request deletion), rate-limited, for five extra minutes.
    visitor_token_ttl_s: float = 600.0
    visitor_public_base_url: str = "http://door.local"

    # Public visitor relay (ADR-0017).  The LAN base URL above cannot load on a
    # phone that is on cellular — which is every stranger at the door — so the QR
    # points at the relay when it is reachable and falls back to the LAN URL when
    # it is not (E-19).  Empty base URL => relay DISABLED: no worker, no egress,
    # QR behaves exactly as it did before.
    visitor_relay_base_url: str = ""
    visitor_relay_device_token: str = ""
    # Origin used to build the QR link, when it differs from the API base.
    visitor_relay_public_url: str = ""
    visitor_relay_poll_interval_s: float = 2.0
    visitor_relay_timeout_s: float = 4.0
    visitor_relay_backoff_max_s: float = 60.0
    # How long a successful exchange keeps the relay considered reachable for QR
    # selection. Short, so a dead relay stops being advertised quickly.
    visitor_relay_freshness_s: float = 30.0

    # ESP32 feedback effect requested for DoorPad touch actions.
    doorpad_effect_id: str = "generic_chime"
    doorpad_effect_duration_ms: int = 900

    # Optional MQTT bridge: subscribe to the NUC control-plane's Mosquitto and
    # re-broadcast NUC-produced ambient/presence events onto /ws so the wallboard
    # tiles receive them. Empty URL => bridge DISABLED (default): no connection,
    # no background task. This is a best-effort, off-critical-path extra; see
    # mqtt_bridge.py. Topics are comma-separated and restricted to ambient/status
    # so door-api never re-broadcasts (duplicates) its own session/vision/social
    # events, which it already emits locally.
    mqtt_url: str = ""
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topics: tuple[str, ...] = ("doorboard/ambient/#", "doorboard/status/#")

    # ESP32 door-controller link (see esp32_link.py).
    #
    # Defaults to "mock", meaning no link is opened and `esp32_transport` stays
    # None — which is exactly how every test, CI run and dev machine behaved
    # before this knob was read at all, so nothing changes for them. Real
    # hardware sets ESP32_TRANSPORT=uart in the Pi's .env.
    #
    # The device default is what an ESP32-S3-DevKitC's onboard bridge enumerates
    # as, because that bridge is wired to GPIO 43/44 — the pins the firmware gives
    # UART1. It is NOT the Pi's own GPIO UART: the AI HAT+ occupies that header.
    esp32_transport: str = "mock"
    esp32_uart_device: str = "/dev/ttyACM1"
    esp32_uart_baud: int = 115_200
    esp32_udp_local_addr: str = ""
    esp32_udp_remote_addr: str = ""
    esp32_reconnect_base_s: float = 1.0
    esp32_reconnect_max_s: float = 30.0

    @staticmethod
    def from_env() -> SessionConfig:
        """Load configuration, applying environment variable overrides."""
        db_path = os.environ.get("DOOR_API_DB_PATH")
        if not db_path:
            ssd_root = os.environ.get("SSD_DATA_ROOT")
            if not ssd_root:
                raise RuntimeError("Either DOOR_API_DB_PATH or SSD_DATA_ROOT must be set")
            db_path = os.path.join(ssd_root, "door-api", "session.sqlite")

        max_recording_s = _env_float("DOOR_API_MAX_RECORDING_S", 60.0)
        review_timeout_s = _env_float("DOOR_API_REVIEW_TIMEOUT_S", 180.0)
        if review_timeout_s < max_recording_s:
            logger.warning(
                "DOOR_API_REVIEW_TIMEOUT_S (%.0fs) is below DOOR_API_MAX_RECORDING_S "
                "(%.0fs); a full-length video message can be auto-discarded before the "
                "visitor finishes reviewing it. Set the review timeout >= max recording.",
                review_timeout_s,
                max_recording_s,
            )

        return SessionConfig(
            ring_timeout_s=_env_float("DOOR_API_RING_TIMEOUT_S", 30.0),
            visitor_mode_auto_ring_s=_env_float("DOOR_API_VISITOR_MODE_AUTO_RING_S", 2.0),
            offer_delay_s=_env_float("DOOR_API_OFFER_DELAY_S", 3.0),
            max_recording_s=max_recording_s,
            review_timeout_s=review_timeout_s,
            saved_linger_s=_env_float("DOOR_API_SAVED_LINGER_S", 5.0),
            inactivity_timeout_s=_env_float("DOOR_API_INACTIVITY_TIMEOUT_S", 600.0),
            approach_timeout_s=_env_float("DOOR_API_APPROACH_TIMEOUT_S", 10.0),
            session_end_linger_s=_env_float("DOOR_API_SESSION_END_LINGER_S", 3.0),
            db_path=db_path,
            door_id=os.environ.get("DOOR_API_DOOR_ID", "primary"),
            cors_origins=_cors_origins_from_env("DOOR_API_CORS_ORIGINS"),
            media_base_url=os.environ.get("DOOR_API_MEDIA_BASE_URL", "http://127.0.0.1:8082"),
            media_public_base_url=os.environ.get(
                "DOOR_API_MEDIA_PUBLIC_BASE_URL",
                os.environ.get("DOOR_API_MEDIA_BASE_URL", "http://127.0.0.1:8082"),
            ),
            media_timeout_s=_env_float("DOOR_API_MEDIA_TIMEOUT_S", 1.0),
            media_admin_token=os.environ.get("DOOR_MEDIA_ADMIN_TOKEN", ""),
            media_outbox_max_rows=int(_env_float("DOOR_API_MEDIA_OUTBOX_MAX_ROWS", 4096.0)),
            media_forward_poll_s=_env_float("DOOR_API_MEDIA_FORWARD_POLL_S", 0.25),
            media_retry_base_s=_env_float("DOOR_API_MEDIA_RETRY_BASE_S", 0.5),
            media_retry_max_s=_env_float("DOOR_API_MEDIA_RETRY_MAX_S", 30.0),
            visiond_base_url=os.environ.get("DOOR_API_VISIOND_BASE_URL", "http://127.0.0.1:8081"),
            visiond_timeout_s=_env_float("DOOR_API_VISIOND_TIMEOUT_S", 3.0),
            sync_base_url=os.environ.get("DOOR_API_SYNC_BASE_URL", "http://127.0.0.1:8083"),
            sync_admin_token=os.environ.get("DOOR_SYNC_ADMIN_TOKEN", ""),
            sync_timeout_s=_env_float("DOOR_API_SYNC_TIMEOUT_S", 1.0),
            sync_outbox_max_rows=int(_env_float("DOOR_API_SYNC_OUTBOX_MAX_ROWS", 4096.0)),
            sync_forward_poll_s=_env_float("DOOR_API_SYNC_FORWARD_POLL_S", 0.25),
            sync_retry_base_s=_env_float("DOOR_API_SYNC_RETRY_BASE_S", 0.5),
            sync_retry_max_s=_env_float("DOOR_API_SYNC_RETRY_MAX_S", 30.0),
            internal_event_token=os.environ.get("DOOR_API_INTERNAL_EVENT_TOKEN", ""),
            feature_photobooth=_env_bool("FEATURE_PHOTOBOOTH", False),
            visitor_token_secret=os.environ.get(
                "DOOR_API_VISITOR_TOKEN_SECRET",
                secrets.token_urlsafe(32),
            ),
            visitor_token_ttl_s=_env_float("DOOR_API_VISITOR_TOKEN_TTL_S", 600.0),
            visitor_public_base_url=os.environ.get(
                "DOOR_API_VISITOR_PUBLIC_BASE_URL",
                "http://door.local",
            ),
            visitor_relay_base_url=os.environ.get("DOOR_API_VISITOR_RELAY_BASE_URL", ""),
            visitor_relay_device_token=os.environ.get("DOOR_API_VISITOR_RELAY_DEVICE_TOKEN", ""),
            visitor_relay_public_url=os.environ.get("DOOR_API_VISITOR_RELAY_PUBLIC_URL", ""),
            visitor_relay_poll_interval_s=_env_float("DOOR_API_VISITOR_RELAY_POLL_S", 2.0),
            visitor_relay_timeout_s=_env_float("DOOR_API_VISITOR_RELAY_TIMEOUT_S", 4.0),
            visitor_relay_backoff_max_s=_env_float("DOOR_API_VISITOR_RELAY_BACKOFF_MAX_S", 60.0),
            visitor_relay_freshness_s=_env_float("DOOR_API_VISITOR_RELAY_FRESHNESS_S", 30.0),
            doorpad_effect_id=os.environ.get("DOOR_API_DOORPAD_EFFECT_ID", "generic_chime"),
            doorpad_effect_duration_ms=int(
                _env_float("DOOR_API_DOORPAD_EFFECT_DURATION_MS", 900.0)
            ),
            mqtt_url=os.environ.get("DOOR_API_MQTT_URL", ""),
            mqtt_username=os.environ.get("DOOR_API_MQTT_USERNAME", ""),
            mqtt_password=os.environ.get("DOOR_API_MQTT_PASSWORD", ""),
            mqtt_topics=_env_csv(
                "DOOR_API_MQTT_TOPICS",
                ("doorboard/ambient/#", "doorboard/status/#"),
            ),
            # Unprefixed, because the door plane shares these with the firmware's
            # view of the same cable rather than owning them.
            esp32_transport=os.environ.get("ESP32_TRANSPORT", "mock").strip().lower(),
            esp32_uart_device=os.environ.get("ESP32_UART_DEVICE", "/dev/ttyACM1"),
            esp32_uart_baud=int(_env_float("ESP32_UART_BAUD", 115_200.0)),
            esp32_udp_local_addr=os.environ.get("ESP32_UDP_LOCAL_ADDR", ""),
            esp32_udp_remote_addr=os.environ.get("ESP32_UDP_REMOTE_ADDR", ""),
            esp32_reconnect_base_s=_env_float("ESP32_RECONNECT_BASE_S", 1.0),
            esp32_reconnect_max_s=_env_float("ESP32_RECONNECT_MAX_S", 30.0),
        )
