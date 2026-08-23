from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
)


def _ensure_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    if offset != timedelta(0):
        msg = "datetime must be UTC"
        raise ValueError(msg)
    return value


def _ensure_uuid7(value: UUID) -> UUID:
    if value.version != 7:
        msg = "event_id must be a UUIDv7"
        raise ValueError(msg)
    return value


type UTCDateTime = Annotated[AwareDatetime, AfterValidator(_ensure_utc)]
type UUIDv7 = Annotated[UUID, AfterValidator(_ensure_uuid7)]


# Consent policy shared by every service that acts on a recognised identity
# (ADR-0018). Lives here rather than in one service because door-visiond gates the
# arrival log and door-api gates attribution, and the two must not drift into
# disagreeing about what a given enrollee agreed to.
#
# v1/v2 scoped recognition to "greeting, lights, and sounds". v3 added the arrival
# log, attribution of interactions, and the name in notifications. Someone enrolled
# under an older statement consented to the greeting only, so the extended
# behaviours must be withheld from them until they re-enroll under current text.
CONSENT_VERSION_FOR_EXTENDED_PERSONALISATION = "v3"


def _consent_ordinal(version: str | None) -> int:
    """Parse a 'vN' consent tag to N. Unparseable or missing sorts lowest."""
    if not version:
        return -1
    try:
        return int(version.lstrip("vV"))
    except ValueError:
        return -1


def consent_covers_extended_personalisation(version: str | None) -> bool:
    """True when this enrollee's consent covers the arrival log and attribution.

    Fails closed: an unknown or unparseable version is treated as not covering it,
    so a malformed record cannot silently opt someone in.
    """
    return _consent_ordinal(version) >= _consent_ordinal(
        CONSENT_VERSION_FOR_EXTENDED_PERSONALISATION
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PresenceLabel(StrEnum):
    """Ordered as the scale a visitor reads, not alphabetically (ADR-0035).

    `SOCIAL` is an active invitation; `AVAILABLE` is passive presence. `KNOCK_IF_URGENT`
    is the middle ground `BUSY` used to have to carry ambiguously — a visitor could not
    tell whether `BUSY` meant "don't knock" or "knock anyway".

    Adding a value here is NOT backward compatible at runtime: a consumer built before it
    rejects the event rather than ignoring the unknown value. Deploy the NUC before the
    door (ADR-0035, ADR-0031).
    """

    SOCIAL = "social"
    AVAILABLE = "available"
    BUSY = "busy"
    KNOCK_IF_URGENT = "knock_if_urgent"
    DO_NOT_DISTURB = "do_not_disturb"
    SLEEPING = "sleeping"
    AT_CLASS = "at_class"
    AT_LIBRARY = "at_library"
    AWAY = "away"
    UNKNOWN = "unknown"


class ErrorDetail(StrictModel):
    code: str
    message: str
    trace_id: UUID


class ErrorEnvelope(StrictModel):
    error: ErrorDetail


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthPayload(StrictModel):
    service: str
    status: HealthStatus
    detail: str | None = None


class SessionState(StrEnum):
    IDLE = "IDLE"
    APPROACH_DETECTED = "APPROACH_DETECTED"
    IDENTITY_CACHED = "IDENTITY_CACHED"
    BUTTON_PRESSED = "BUTTON_PRESSED"
    VISITOR_MODE = "VISITOR_MODE"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    UNANSWERED_TIMEOUT = "UNANSWERED_TIMEOUT"
    VIDEO_MESSAGE_OFFERED = "VIDEO_MESSAGE_OFFERED"
    VIDEO_MESSAGE_RECORDING = "VIDEO_MESSAGE_RECORDING"
    VIDEO_MESSAGE_REVIEW = "VIDEO_MESSAGE_REVIEW"
    VIDEO_MESSAGE_SAVED = "VIDEO_MESSAGE_SAVED"
    SESSION_END = "SESSION_END"


LEGAL_SESSION_TRANSITIONS: dict[SessionState, tuple[SessionState, ...]] = {
    SessionState.IDLE: (
        SessionState.APPROACH_DETECTED,
        SessionState.BUTTON_PRESSED,
    ),
    SessionState.APPROACH_DETECTED: (
        SessionState.IDENTITY_CACHED,
        SessionState.BUTTON_PRESSED,
        SessionState.IDLE,
    ),
    SessionState.IDENTITY_CACHED: (
        SessionState.BUTTON_PRESSED,
        SessionState.IDLE,
        SessionState.APPROACH_DETECTED,
    ),
    SessionState.BUTTON_PRESSED: (
        SessionState.VISITOR_MODE,
        SessionState.SESSION_END,
    ),
    SessionState.VISITOR_MODE: (
        SessionState.RINGING,
        SessionState.SESSION_END,
    ),
    SessionState.RINGING: (
        SessionState.ANSWERED,
        SessionState.UNANSWERED_TIMEOUT,
        SessionState.SESSION_END,
    ),
    SessionState.ANSWERED: (
        SessionState.VIDEO_MESSAGE_OFFERED,
        SessionState.SESSION_END,
    ),
    SessionState.UNANSWERED_TIMEOUT: (
        SessionState.VIDEO_MESSAGE_OFFERED,
        SessionState.SESSION_END,
    ),
    SessionState.VIDEO_MESSAGE_OFFERED: (
        SessionState.VIDEO_MESSAGE_RECORDING,
        SessionState.SESSION_END,
    ),
    SessionState.VIDEO_MESSAGE_RECORDING: (
        SessionState.VIDEO_MESSAGE_REVIEW,
        SessionState.SESSION_END,
    ),
    SessionState.VIDEO_MESSAGE_REVIEW: (
        SessionState.VIDEO_MESSAGE_SAVED,
        SessionState.VIDEO_MESSAGE_RECORDING,
        SessionState.SESSION_END,
    ),
    SessionState.VIDEO_MESSAGE_SAVED: (SessionState.SESSION_END,),
    SessionState.SESSION_END: (SessionState.IDLE,),
}


class BaseEvent(StrictModel):
    event_id: UUIDv7
    source: str
    occurred_at: UTCDateTime
    monotonic_ms: int
    door_id: str
    trace_id: UUID


class DoorButtonPressedPayload(StrictModel):
    press_id: UUID
    had_cached_profile: bool
    profile_id: str | None


class DoorKnockDetectedPayload(StrictModel):
    pattern_id: str
    confidence: float


class DoorContactChangedPayload(StrictModel):
    state: Literal["open", "closed"]


class DoorProfileUpdatePayload(StrictModel):
    profile_id: str
    expires_at_monotonic_ms: int
    priority: Literal["normal", "high"]


class DoorProfileClearPayload(StrictModel):
    reason: Literal["expired", "privacy_mode", "admin"]


class DoorEffectPlayPayload(StrictModel):
    effect_id: str
    duration_ms: int


class DoorControllerHealthPayload(StrictModel):
    uptime_s: int
    fw_version: str
    cached_profile_id: str | None
    fallback_active: bool


class VisionFaceVisiblePayload(StrictModel):
    face_count: int
    largest_face_px: int


class VisionIdentityStablePayload(StrictModel):
    person_id: str
    display_name: str
    # The colour this person chose at enrollment (ADR-0021), as `#rgb`/`#rrggbb`. The
    # kiosks accent the greeting, the identity badge and the doorpad frame with it. Not
    # unique across people and NOT the same thing as `profile_id`, which names an ESP32
    # LED effect and must stay unique. None means "fall back to the profile's catalogue
    # colour" — an older door, or a row enrolled before this field existed.
    accent_color: str | None = None
    # Which consent statement this person enrolled under (ADR-0018). door-api needs
    # it to decide whether attribution is permitted for them; the greeting is
    # covered by every version, so this gates only the extended behaviours.
    consent_version: str | None = None
    confidence: float
    expires_at: UTCDateTime
    expires_at_monotonic_ms: int
    profile_id: str


class VisionIdentityExpiredPayload(StrictModel):
    person_id: str
    # Why the identity went away (ADR-0029). door-visiond has always known this and dropped
    # it here, which left the consumer unable to tell two very different facts apart:
    #
    #   "expired"      — the 2.5 s cache lapsed because the face left frame. Routine: it
    #                    happens constantly while someone stands at the doorpad looking down
    #                    at it, and ADR-0020 requires the held name to survive it.
    #   "admin"        — the person was unenrolled. Their face data is gone and their name
    #                    must come off the screen now, not when a timer happens to lapse.
    #   "privacy_mode" — recognition was switched off (ADR-0009 §4).
    #
    # Optional, so an older producer still validates; a consumer that sees None must assume
    # the routine case, which is the safe default — it keeps the name up rather than
    # flickering it off, and the deletion paths below are the ones that explicitly clear.
    reason: Literal["expired", "admin", "privacy_mode"] | None = None


class VisionPrivacyModeChangedPayload(StrictModel):
    enabled: bool
    changed_by: Literal["admin", "schedule", "physical"]


class VisionPipelineStatusPayload(StrictModel):
    mode: Literal["disabled", "mock", "single-camera", "dual-camera", "hardware"]
    hailo_ok: bool
    fps: float
    inference_ms_p50: float


class SessionStateChangedPayload(StrictModel):
    session_id: UUID
    from_state: SessionState
    to_state: SessionState
    trigger: str
    # Recognised visitor's display name, or None when nobody is recognised
    # (ADR-0018 §4). The control plane evaluates notification rules from this
    # event and has no other route to the name, so "Tiger is here" needs it here.
    # Deliberately the name alone -- never person_id alongside it in a message
    # that leaves the house -- and None keeps the generic notification path intact.
    display_name: str | None = None
    # Chosen recipient KEYS for per-recipient video-message routing (ADR-0014).
    # Only populated on the VIDEO_MESSAGE_SAVED transition, when the visitor
    # picked who the clip should go to (e.g. ["tiger"], ["adam"], or both). Keys
    # are lowercase resident ids resolved to Telegram chat ids on the control
    # plane via VIDEO_MESSAGE_RECIPIENTS. None/absent (the default) means legacy
    # broadcast to every configured chat — so existing producers/consumers stay
    # backward-compatible.
    recipients: list[str] | None = None


class SessionStartedPayload(StrictModel):
    session_id: UUID
    entry: Literal["button", "touch", "approach"]


class SessionEndedPayload(StrictModel):
    session_id: UUID
    outcome: Literal[
        "answered",
        "unanswered_timeout",
        "message_left",
        "abandoned",
        "reset",
    ]


class MediaRecordingStartedPayload(StrictModel):
    recording_id: UUID
    session_id: UUID
    kind: Literal["bell_clip", "video_message", "photo_booth"]
    stream: str


class MediaRecordingFinalizedPayload(StrictModel):
    recording_id: UUID
    path: str
    duration_s: float
    size_bytes: int
    sha256: str
    consent_context: Literal["visitor_initiated", "bell_event"]


class MediaThumbnailReadyPayload(StrictModel):
    recording_id: UUID
    path: str


class MediaRetentionDeletedPayload(StrictModel):
    recording_id: UUID
    reason: Literal["age", "space", "user_request", "synced"]


class MediaStorageStatusPayload(StrictModel):
    free_bytes: int
    queue_depth: int
    oldest_unsynced_s: int
    recording_allowed: bool


class SyncUploadQueuedPayload(StrictModel):
    item_id: UUID
    recording_id: UUID
    target: Literal["nas", "nuc"]


class SyncUploadCompletedPayload(StrictModel):
    item_id: UUID
    verified_sha256: str
    attempts: int


class SyncUploadFailedPayload(StrictModel):
    item_id: UUID
    attempts: int
    next_retry_at: UTCDateTime
    error_class: str


class StatusPresenceChangedPayload(StrictModel):
    subject_id: str
    label: PresenceLabel
    # Must match SOURCE_PRECEDENCE in control-plane-api's presence.py. Adding a
    # source there without adding it here does not fail at startup — it fails when
    # that source first WINS, as a 500 out of presence resolution. That is exactly
    # how `schedule` shipped broken on 2026-08-22.
    source: Literal["manual", "focus_shortcut", "geofence_label", "calendar", "schedule", "default"]
    until: UTCDateTime | None


class SocialGuestbookEntryCreatedPayload(StrictModel):
    entry_id: UUID
    text: str
    author_label: str | None
    # Optional reference to a visitor-captured photo_booth recording, mirroring
    # SocialCheckinCreatedPayload (ADR-0033). Only the reference travels — the
    # image stays private in the photo-booth pipeline until the owner approves
    # it. Additive to the schema but NOT backward compatible at runtime
    # (StrictModel forbids extras), so deploy the NUC at or before the door:
    # see ADR-0033's "Deployment ordering" and ADR-0031.
    photo_recording_id: str | None = None


class SocialPollVoteCastPayload(StrictModel):
    poll_id: str
    option_id: str


class SocialCheckinCreatedPayload(StrictModel):
    checkin_id: UUID
    person_id: str | None
    label: str | None
    # Optional reference to a visitor-captured photo_booth recording (see
    # ADR-0013). Defaults to None so existing producers/consumers stay
    # backward-compatible; the photo itself lives in the photo-booth/gallery
    # pipeline — the check-in only stores the reference.
    photo_recording_id: str | None = None


class SocialMoodUpdatedPayload(StrictModel):
    subject_id: str
    mood: str


class SocialScoreboardUpdatedPayload(StrictModel):
    board_id: str
    entry_id: UUID
    delta: int


class SocialDeletionRequestedPayload(StrictModel):
    target_kind: Literal["guestbook", "video_message", "photo", "checkin", "enrollment"]
    target_id: str


class AmbientBirdSpeciesSummary(StrictModel):
    name: str
    count: int
    confidence_avg: float


class AmbientBirdSummaryPayload(StrictModel):
    window: Literal["today"]
    top_species: list[AmbientBirdSpeciesSummary]
    total_detections: int


class SatelliteTrackSample(StrictModel):
    """One point on a pass: seconds after rise, where to look, and where it is (ADR-0030).

    ``azimuth_deg``/``elevation_deg`` answer "where do I look from here" and drive the sky
    dome. ``lat``/``lng`` are the sub-satellite point — the spot on Earth it is directly
    over — which is what a globe needs and what the az/el pair cannot be converted into
    without the orbit. The provider computes both from the same moment, so they cannot
    disagree about where the satellite was.

    The ground position is optional: a producer from before this existed omits it, and the
    globe then has nothing to plot and says so rather than guessing a position from a bearing.
    """

    t_offset_s: float
    azimuth_deg: float
    elevation_deg: float
    lat: float | None = None
    lng: float | None = None


class AmbientSatellitePassPayload(StrictModel):
    satellite: str
    rise_at: UTCDateTime
    max_elevation_deg: float
    direction: str
    visible: bool
    # Additive pass geometry (ADR-0025). The provider already finds rise, culmination and
    # set events and then kept only the culmination compass point, so the shape of the pass
    # — the thing that tells you where to look and for how long — was computed and
    # discarded. All optional: older producers and the offline mock omit them, so consumers
    # must treat every field below as "may be absent" and fall back to the text above.
    set_at: UTCDateTime | None = None
    rise_azimuth_deg: float | None = None
    set_azimuth_deg: float | None = None
    culmination_azimuth_deg: float | None = None
    # Sampled arc from rise to set, for drawing the path across the sky. Bounded by the
    # producer; a consumer must not assume a fixed count or even spacing.
    track: list[SatelliteTrackSample] = []


class SatelliteOrbitSample(StrictModel):
    """One point on a full-period ground track: an absolute UTC time and the
    sub-satellite point at that time (ADR-0041).

    Absolute ``at`` — not a ``t_offset_s`` from a rise, the way ``SatelliteTrackSample``
    carries — because this track wraps a whole orbit and stays on the wire between hourly
    refreshes. The client draws a LIVE marker by wrapping "now" into the period and
    interpolating along the track, and that needs real timestamps, not offsets from an
    event that may already be an hour old.

    ``lat``/``lng`` are required, unlike the pass track's optional ones: the whole point of
    this event is the ground track, so a producer that cannot compute a position emits no
    orbit for that satellite rather than a sample without one.
    """

    at: UTCDateTime
    lat: float
    lng: float


class SatelliteOrbit(StrictModel):
    """One tracked satellite and one full revolution of its ground track (ADR-0041)."""

    name: str
    norad_id: int
    # Where it is right now, from the same TLE the track was sampled from, so the live
    # marker has a truthful position even before the client starts interpolating.
    sub_lat: float
    sub_lng: float
    # One full orbital period, sampled and bounded by the producer (~90-120 points). A
    # consumer must not assume a fixed count or even spacing; the client interpolates
    # between whatever samples arrive and wraps "now" into the period for the live marker.
    track: list[SatelliteOrbitSample] = []


class AmbientSatelliteOrbitsPayload(StrictModel):
    """Every interesting satellite, each with a full-period ground track and its current
    sub-point (ADR-0041).

    A SEPARATE event from ``ambient.satellite_pass``, deliberately not an extension of it.
    The pass stays the single next VISIBLE pass and its sky (az/el) geometry — "which way
    do I look, and when". This answers a different question — where every tracked satellite
    is right now and the whole loop each one traces on the ground — and overloading the
    pass's ``track`` (whose az/el only mean anything during that one pass) with a full orbit
    would conflate the two.

    Being a brand-new event type it is additive to the catalogue but NOT backward compatible
    at runtime: a consumer on older contracts rejects it rather than ignoring it (StrictModel
    + a discriminated union with no such member). Deploy the NUC at or before the door — see
    ADR-0041's deployment ordering and ADR-0031.
    """

    satellites: list[SatelliteOrbit] = []
    # The moment every ``sub_lat``/``sub_lng`` above was true, so the tile can show its age.
    # The live marker does not depend on this being fresh: the track carries absolute times,
    # so the client re-anchors on the real "now" even from an hour-old payload.
    as_of: UTCDateTime


class AmbientAircraftNearby(StrictModel):
    callsign: str
    altitude_ft: int
    distance_km: float
    heading: int
    # Additive per-plane detail (ADR-0015). All optional / backward-compatible:
    # older producers and the offline mock omit them, so consumers must treat
    # every field below as "may be absent".
    #
    # Derived from the OpenSky state vector already fetched for the summary:
    icao24: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ground_speed_kmh: int | None = None
    vertical_rate_fpm: int | None = None
    on_ground: bool | None = None
    origin_country: str | None = None
    # Best-effort external enrichment (adsbdb / planespotters), nearest planes
    # only, cached; any lookup miss/timeout leaves the field absent.
    registration: str | None = None
    aircraft_type: str | None = None
    operator: str | None = None
    origin: str | None = None
    destination: str | None = None
    photo_url: str | None = None
    photo_attribution: str | None = None


class AircraftObserver(StrictModel):
    latitude: float
    longitude: float


class AmbientAircraftSummaryPayload(StrictModel):
    nearby: list[AmbientAircraftNearby]
    as_of: UTCDateTime
    # Configured observer centre so a UI map can centre on it (ADR-0015).
    observer: AircraftObserver | None = None


class AmbientPrinterStatusPayload(StrictModel):
    state: Literal["idle", "printing", "paused", "error", "offline"]
    job_name: str | None
    progress_pct: float | None
    eta: UTCDateTime | None


class AmbientFoodRecommendationPayload(StrictModel):
    date: date
    title: str
    detail: str | None
    provider: str
    # The recommended hall as its own field (ADR-0026), because `title` is prose — it is
    # built as "{best_hall} — {meal}" and a consumer that wants to place the hall on a map
    # would otherwise have to split a display string on an em dash. Optional: a provider
    # with no concept of a named venue omits it, and the consumer then draws no point rather
    # than guessing one.
    hall: str | None = None
    backup_hall: str | None = None


class AcademicMilestone(StrictModel):
    """One dated thing on the academic calendar."""

    label: str
    date: date
    days_until: int
    """Whole days from today, local. Negative is not published — see the worker."""
    kind: Literal["term_start", "classes_end", "finals", "break", "commencement"]


class AmbientAcademicCountdownPayload(StrictModel):
    """Days until the next few academic milestones (ADR-0039).

    Read from a configured date table rather than fetched: the university publishes
    the calendar as a web page with no iCalendar feed, and a scraper for something
    that changes three times a year would be the most fragile part of the system.
    A table also means this job makes no outbound request at all.
    """

    next: AcademicMilestone
    """The one to lead with. Absent-if-none is expressed by not publishing at all."""
    upcoming: list[AcademicMilestone] = []
    """The following few, so a subtitle can say what comes after."""
    source: str
    """Which table this came from, so a stale one is identifiable on screen."""


class SystemServiceHealthPayload(HealthPayload):
    pass


class SystemStorageAlertPayload(StrictModel):
    host: str
    mount: str
    free_bytes: int
    severity: Literal["warning", "critical"]


class SystemLatencySamplePayload(StrictModel):
    path: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    window_s: int


class DoorButtonPressedEvent(BaseEvent):
    type: Literal["door.button_pressed"]
    payload: DoorButtonPressedPayload


class DoorKnockDetectedEvent(BaseEvent):
    type: Literal["door.knock_detected"]
    payload: DoorKnockDetectedPayload


class DoorContactChangedEvent(BaseEvent):
    type: Literal["door.contact_changed"]
    payload: DoorContactChangedPayload


class DoorProfileUpdateEvent(BaseEvent):
    type: Literal["door.profile_update"]
    payload: DoorProfileUpdatePayload


class DoorProfileClearEvent(BaseEvent):
    type: Literal["door.profile_clear"]
    payload: DoorProfileClearPayload


class DoorEffectPlayEvent(BaseEvent):
    type: Literal["door.effect_play"]
    payload: DoorEffectPlayPayload


class DoorControllerHealthEvent(BaseEvent):
    type: Literal["door.controller_health"]
    payload: DoorControllerHealthPayload


class VisionFaceVisibleEvent(BaseEvent):
    type: Literal["vision.face_visible"]
    payload: VisionFaceVisiblePayload


class VisionIdentityStableEvent(BaseEvent):
    type: Literal["vision.identity_stable"]
    payload: VisionIdentityStablePayload


class VisionIdentityExpiredEvent(BaseEvent):
    type: Literal["vision.identity_expired"]
    payload: VisionIdentityExpiredPayload


class VisionPrivacyModeChangedEvent(BaseEvent):
    type: Literal["vision.privacy_mode_changed"]
    payload: VisionPrivacyModeChangedPayload


class VisionPipelineStatusEvent(BaseEvent):
    type: Literal["vision.pipeline_status"]
    payload: VisionPipelineStatusPayload


class SessionStateChangedEvent(BaseEvent):
    type: Literal["session.state_changed"]
    payload: SessionStateChangedPayload


class SessionStartedEvent(BaseEvent):
    type: Literal["session.started"]
    payload: SessionStartedPayload


class SessionEndedEvent(BaseEvent):
    type: Literal["session.ended"]
    payload: SessionEndedPayload


class MediaRecordingStartedEvent(BaseEvent):
    type: Literal["media.recording_started"]
    payload: MediaRecordingStartedPayload


class MediaRecordingFinalizedEvent(BaseEvent):
    type: Literal["media.recording_finalized"]
    payload: MediaRecordingFinalizedPayload


class MediaThumbnailReadyEvent(BaseEvent):
    type: Literal["media.thumbnail_ready"]
    payload: MediaThumbnailReadyPayload


class MediaRetentionDeletedEvent(BaseEvent):
    type: Literal["media.retention_deleted"]
    payload: MediaRetentionDeletedPayload


class MediaStorageStatusEvent(BaseEvent):
    type: Literal["media.storage_status"]
    payload: MediaStorageStatusPayload


class SyncUploadQueuedEvent(BaseEvent):
    type: Literal["sync.upload_queued"]
    payload: SyncUploadQueuedPayload


class SyncUploadCompletedEvent(BaseEvent):
    type: Literal["sync.upload_completed"]
    payload: SyncUploadCompletedPayload


class SyncUploadFailedEvent(BaseEvent):
    type: Literal["sync.upload_failed"]
    payload: SyncUploadFailedPayload


class StatusPresenceChangedEvent(BaseEvent):
    type: Literal["status.presence_changed"]
    payload: StatusPresenceChangedPayload


class SocialGuestbookEntryCreatedEvent(BaseEvent):
    type: Literal["social.guestbook_entry_created"]
    payload: SocialGuestbookEntryCreatedPayload


class SocialPollVoteCastEvent(BaseEvent):
    type: Literal["social.poll_vote_cast"]
    payload: SocialPollVoteCastPayload


class SocialCheckinCreatedEvent(BaseEvent):
    type: Literal["social.checkin_created"]
    payload: SocialCheckinCreatedPayload


class SocialMoodUpdatedEvent(BaseEvent):
    type: Literal["social.mood_updated"]
    payload: SocialMoodUpdatedPayload


class SocialScoreboardUpdatedEvent(BaseEvent):
    type: Literal["social.scoreboard_updated"]
    payload: SocialScoreboardUpdatedPayload


class SocialDeletionRequestedEvent(BaseEvent):
    type: Literal["social.deletion_requested"]
    payload: SocialDeletionRequestedPayload


class AmbientBirdSummaryEvent(BaseEvent):
    type: Literal["ambient.bird_summary"]
    payload: AmbientBirdSummaryPayload


class AmbientSatellitePassEvent(BaseEvent):
    type: Literal["ambient.satellite_pass"]
    payload: AmbientSatellitePassPayload


class AmbientSatelliteOrbitsEvent(BaseEvent):
    type: Literal["ambient.satellite_orbits"]
    payload: AmbientSatelliteOrbitsPayload


class AmbientAircraftSummaryEvent(BaseEvent):
    type: Literal["ambient.aircraft_summary"]
    payload: AmbientAircraftSummaryPayload


class AmbientPrinterStatusEvent(BaseEvent):
    type: Literal["ambient.printer_status"]
    payload: AmbientPrinterStatusPayload


class AmbientFoodRecommendationEvent(BaseEvent):
    type: Literal["ambient.food_recommendation"]
    payload: AmbientFoodRecommendationPayload


class AmbientAcademicCountdownEvent(BaseEvent):
    type: Literal["ambient.academic_countdown"]
    payload: AmbientAcademicCountdownPayload


class SystemServiceHealthEvent(BaseEvent):
    type: Literal["system.service_health"]
    payload: SystemServiceHealthPayload


class SystemStorageAlertEvent(BaseEvent):
    type: Literal["system.storage_alert"]
    payload: SystemStorageAlertPayload


class SystemLatencySampleEvent(BaseEvent):
    type: Literal["system.latency_sample"]
    payload: SystemLatencySamplePayload


EVENT_MODELS: tuple[type[BaseEvent], ...] = (
    DoorButtonPressedEvent,
    DoorKnockDetectedEvent,
    DoorContactChangedEvent,
    DoorProfileUpdateEvent,
    DoorProfileClearEvent,
    DoorEffectPlayEvent,
    DoorControllerHealthEvent,
    VisionFaceVisibleEvent,
    VisionIdentityStableEvent,
    VisionIdentityExpiredEvent,
    VisionPrivacyModeChangedEvent,
    VisionPipelineStatusEvent,
    SessionStateChangedEvent,
    SessionStartedEvent,
    SessionEndedEvent,
    MediaRecordingStartedEvent,
    MediaRecordingFinalizedEvent,
    MediaThumbnailReadyEvent,
    MediaRetentionDeletedEvent,
    MediaStorageStatusEvent,
    SyncUploadQueuedEvent,
    SyncUploadCompletedEvent,
    SyncUploadFailedEvent,
    StatusPresenceChangedEvent,
    SocialGuestbookEntryCreatedEvent,
    SocialPollVoteCastEvent,
    SocialCheckinCreatedEvent,
    SocialMoodUpdatedEvent,
    SocialScoreboardUpdatedEvent,
    SocialDeletionRequestedEvent,
    AmbientBirdSummaryEvent,
    AmbientSatellitePassEvent,
    AmbientSatelliteOrbitsEvent,
    AmbientAircraftSummaryEvent,
    AmbientPrinterStatusEvent,
    AmbientFoodRecommendationEvent,
    AmbientAcademicCountdownEvent,
    SystemServiceHealthEvent,
    SystemStorageAlertEvent,
    SystemLatencySampleEvent,
)


def _event_model_type(model: type[BaseEvent]) -> str:
    args = getattr(model.model_fields["type"].annotation, "__args__", ())
    if len(args) != 1 or not isinstance(args[0], str):
        msg = f"{model.__name__}.type must be a single string Literal"
        raise TypeError(msg)
    return args[0]


EVENT_TYPE_TO_MODEL: dict[str, type[BaseEvent]] = {
    _event_model_type(model): model for model in EVENT_MODELS
}

type DoorboardEvent = Annotated[
    DoorButtonPressedEvent
    | DoorKnockDetectedEvent
    | DoorContactChangedEvent
    | DoorProfileUpdateEvent
    | DoorProfileClearEvent
    | DoorEffectPlayEvent
    | DoorControllerHealthEvent
    | VisionFaceVisibleEvent
    | VisionIdentityStableEvent
    | VisionIdentityExpiredEvent
    | VisionPrivacyModeChangedEvent
    | VisionPipelineStatusEvent
    | SessionStateChangedEvent
    | SessionStartedEvent
    | SessionEndedEvent
    | MediaRecordingStartedEvent
    | MediaRecordingFinalizedEvent
    | MediaThumbnailReadyEvent
    | MediaRetentionDeletedEvent
    | MediaStorageStatusEvent
    | SyncUploadQueuedEvent
    | SyncUploadCompletedEvent
    | SyncUploadFailedEvent
    | StatusPresenceChangedEvent
    | SocialGuestbookEntryCreatedEvent
    | SocialPollVoteCastEvent
    | SocialCheckinCreatedEvent
    | SocialMoodUpdatedEvent
    | SocialScoreboardUpdatedEvent
    | SocialDeletionRequestedEvent
    | AmbientBirdSummaryEvent
    | AmbientSatellitePassEvent
    | AmbientSatelliteOrbitsEvent
    | AmbientAircraftSummaryEvent
    | AmbientPrinterStatusEvent
    | AmbientFoodRecommendationEvent
    | AmbientAcademicCountdownEvent
    | SystemServiceHealthEvent
    | SystemStorageAlertEvent
    | SystemLatencySampleEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[DoorboardEvent] = TypeAdapter(DoorboardEvent)


def parse_event(data: Any) -> DoorboardEvent:
    return EVENT_ADAPTER.validate_python(data)


def event_json_schema() -> dict[str, Any]:
    return EVENT_ADAPTER.json_schema()
