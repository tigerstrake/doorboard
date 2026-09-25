#ifndef DOOR_PROTOCOL_H
#define DOOR_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DOOR_PROTOCOL_VERSION 1
#define DOOR_PROTOCOL_MAX_FRAME_BYTES 512
#define DOOR_PROTOCOL_MAX_RETRIES 3
#define DOOR_PROTOCOL_RETRY_SPACING_MS 50
#define DOOR_PROTOCOL_HEARTBEAT_TIMEOUT_MS 5000
#define DOOR_PROTOCOL_PARTIAL_FRAME_TIMEOUT_MS 1000
#define DOOR_PROTOCOL_PROFILE_ID_BYTES 64
#define DOOR_PROTOCOL_BOOT_ID_BYTES 64
#define DOOR_PROTOCOL_VERSION_BYTES 32
#define DOOR_PROTOCOL_EFFECT_ID_BYTES 64
#define DOOR_PROTOCOL_PRESS_ID_BYTES 40
#define DOOR_PROTOCOL_PENDING_DEPTH 8
#define DOOR_PROTOCOL_BOOT_ID_HEX_CHARS 16

typedef enum {
    DOOR_PROTOCOL_RX_DROPPED = 0,
    DOOR_PROTOCOL_RX_ACCEPTED = 1,
    DOOR_PROTOCOL_RX_ACK_EMITTED = 2,
} door_protocol_rx_result_t;

typedef enum {
    DOOR_PROTOCOL_MSG_UNKNOWN = 0,
    DOOR_PROTOCOL_MSG_HELLO,
    DOOR_PROTOCOL_MSG_HEARTBEAT,
    DOOR_PROTOCOL_MSG_PROFILE_UPDATE,
    DOOR_PROTOCOL_MSG_PROFILE_CLEAR,
    DOOR_PROTOCOL_MSG_EFFECT_PLAY,
    DOOR_PROTOCOL_MSG_BUTTON_EVENT,
    DOOR_PROTOCOL_MSG_KNOCK_EVENT,
    DOOR_PROTOCOL_MSG_CONTACT_EVENT,
    DOOR_PROTOCOL_MSG_ACK,
} door_protocol_message_type_t;

/* A decoded inbound frame. Filled by door_protocol_parse_line, which touches no
 * shared state, so a caller can parse outside whatever lock guards the context. */
typedef struct {
    int version;
    uint32_t seq;
    door_protocol_message_type_t type;
    bool has_ack;
    uint32_t ack;
    char boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
    char priority[16];
    char reason[24];
    char effect_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
    uint32_t ttl_ms;
    uint32_t duration_ms;
} door_protocol_message_t;

/* What a button press should play after the generic flash. */
typedef enum {
    DOOR_PROTOCOL_PRESS_GENERIC_ONLY = 0,
    DOOR_PROTOCOL_PRESS_PROFILE = 1,
    DOOR_PROTOCOL_PRESS_FALLBACK = 2,
} door_protocol_press_followup_t;

typedef struct {
    bool active;
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
    char priority[16];
    uint64_t expires_at_mono_ms;
} door_protocol_cached_profile_t;

typedef struct {
    uint32_t rx_errors;
    uint32_t tx_retries;
    uint32_t tx_dropped;
    uint32_t profile_updates_applied;
    /* A normal-priority push of a different profile while a high-priority one is
     * still valid: acked, not applied. */
    uint32_t profile_updates_rejected;
    uint32_t profile_clears_applied;
    uint32_t effect_plays_applied;
    /* An effect_id that is not in the pre-flashed table: acked, nothing plays. */
    uint32_t effect_plays_rejected;
} door_protocol_stats_t;

typedef bool (*door_protocol_random_fill_fn)(uint8_t *buffer, size_t len, void *user);

typedef struct {
    char boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    char fw_version[DOOR_PROTOCOL_VERSION_BYTES];
    char peer_boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    uint32_t next_seq;
    bool connected;
    bool fallback_active;
    bool fallback_reported;
    bool have_pi_heartbeat;
    uint64_t last_pi_heartbeat_mono_ms;
    door_protocol_cached_profile_t cached_profile;
    door_protocol_stats_t stats;

    /* Line framer for the byte stream from the Pi (see door_protocol_rx_byte). */
    struct {
        char line[DOOR_PROTOCOL_MAX_FRAME_BYTES + 2];
        size_t len;
        bool discarding;
        uint64_t last_byte_mono_ms;
    } rx;

    /* The latest effect_play the Pi asked for, waiting to be collected by
     * door_protocol_take_effect_play outside the context lock. */
    struct {
        bool pending;
        char effect_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
        uint32_t duration_ms;
    } effect_request;

    struct {
        bool used;
        char boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
        uint32_t seq;
    } seen[16];
    size_t seen_next;

    struct {
        bool active;
        bool sent_once;
        uint32_t seq;
        uint8_t retries_sent;
        uint64_t last_tx_mono_ms;
        char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    } pending[DOOR_PROTOCOL_PENDING_DEPTH];
    size_t pending_head;
    size_t pending_count;

    /* Optional: invoked by door_protocol_receive_from_pi after the message has
     * been applied and the context is no longer being mutated. A caller that
     * holds a lock around the apply step should leave this NULL and collect the
     * request with door_protocol_take_effect_play instead. */
    void (*effect_play_cb)(const char *effect_id, uint32_t duration_ms, void *user);
    void *effect_play_cb_user;
} door_protocol_t;

void door_protocol_init(
    door_protocol_t *ctx,
    const char *boot_id,
    const char *fw_version
);

void door_protocol_apply_timeouts(door_protocol_t *ctx, uint64_t now_mono_ms);

/*
 * Write a DOOR_PROTOCOL_BOOT_ID_HEX_CHARS-character hex boot identifier drawn from
 * two calls to `rng` into `out` (`out_len` >= DOOR_PROTOCOL_BOOT_ID_HEX_CHARS + 1).
 * Generated once per boot: the Pi dedupes inbound frames by (boot_id, seq), so a
 * boot_id that survives a reboot makes every post-reboot frame look like a stale
 * retransmit of the previous run for as long as that run's sequence numbers ran.
 */
bool door_protocol_make_boot_id(char *out, size_t out_len, uint32_t (*rng)(void));

bool door_protocol_make_uuid_v4(
    char *out,
    size_t out_len,
    door_protocol_random_fill_fn fill_random,
    void *random_user
);

/*
 * Feed one byte from the Pi. Returns the length of a complete line (newline
 * included, NUL-terminated in ctx->rx.line) or 0 when more bytes are needed;
 * copy the line out before the next call. A partial line older than
 * DOOR_PROTOCOL_PARTIAL_FRAME_TIMEOUT_MS is discarded before the byte is added,
 * and a line longer than DOOR_PROTOCOL_MAX_FRAME_BYTES (newline excluded, the
 * same cap the Pi applies) is skipped up to and including its newline so its
 * tail can never parse as a frame of its own. Both count in stats.rx_errors.
 */
size_t door_protocol_rx_byte(door_protocol_t *ctx, uint8_t byte, uint64_t now_mono_ms);

/* Decode one line. Pure: no context, so safe to call outside any lock. False on
 * a malformed frame, an unknown type, a missing required field, a line over the
 * cap, or a protocol version other than DOOR_PROTOCOL_VERSION. */
bool door_protocol_parse_line(
    const char *line,
    size_t line_len,
    door_protocol_message_t *msg
);

/* Record a frame that failed door_protocol_parse_line. */
void door_protocol_count_rx_error(door_protocol_t *ctx);

/* Apply a parsed message: dedupe, state change, and the ack (if the type needs
 * one) into ack_out. Never calls back into the caller; an effect_play is left
 * for door_protocol_take_effect_play. */
door_protocol_rx_result_t door_protocol_apply_message(
    door_protocol_t *ctx,
    const door_protocol_message_t *msg,
    uint64_t now_mono_ms,
    const char *sender_boot_id,
    char *ack_out,
    size_t ack_out_len
);

/* parse + count + apply in one call, then effect_play_cb if set. Convenient
 * where nothing else touches the context concurrently. */
door_protocol_rx_result_t door_protocol_receive_from_pi(
    door_protocol_t *ctx,
    const char *line,
    size_t line_len,
    uint64_t now_mono_ms,
    const char *sender_boot_id,
    char *ack_out,
    size_t ack_out_len
);

/* Collect (and clear) a pending effect_play request. */
bool door_protocol_take_effect_play(
    door_protocol_t *ctx,
    char *effect_id_out,
    size_t effect_id_out_len,
    uint32_t *duration_ms_out
);

/* Queue a hello. Also drops any partial inbound line: a hello is a link (re)start
 * and whatever the previous link left half-received cannot complete. */
bool door_protocol_start_hello(door_protocol_t *ctx);

/*
 * Apply timeouts and report the transition *into* fallback, once per entry.
 * On that edge a hello is queued so a Pi that is merely slow to answer
 * re-handshakes without waiting for its own restart. Boot state is fallback
 * and does not count as an edge.
 */
bool door_protocol_fallback_edge(door_protocol_t *ctx, uint64_t now_mono_ms);

/*
 * What to play after the generic press flash: the cached profile if one is
 * valid (copied into profile_id_out), the fallback animation while the Pi is
 * lost, nothing otherwise.
 */
door_protocol_press_followup_t door_protocol_press_followup(
    door_protocol_t *ctx,
    uint64_t now_mono_ms,
    char *profile_id_out,
    size_t profile_id_out_len
);

bool door_protocol_emit_button_event(
    door_protocol_t *ctx,
    const char *press_id,
    uint64_t pressed_at_mono_ms
);

bool door_protocol_emit_knock_event(
    door_protocol_t *ctx,
    const char *pattern_id,
    double confidence
);

bool door_protocol_next_tx(
    door_protocol_t *ctx,
    uint64_t now_mono_ms,
    char *frame_out,
    size_t frame_out_len
);

bool door_protocol_make_heartbeat(
    door_protocol_t *ctx,
    uint64_t now_mono_ms,
    uint32_t uptime_s,
    char *frame_out,
    size_t frame_out_len
);

bool door_protocol_has_cached_profile(
    door_protocol_t *ctx,
    uint64_t now_mono_ms
);

const char *door_protocol_cached_profile_id(
    door_protocol_t *ctx,
    uint64_t now_mono_ms
);

#ifdef __cplusplus
}
#endif

#endif
