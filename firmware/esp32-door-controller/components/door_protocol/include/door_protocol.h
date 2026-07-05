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
#define DOOR_PROTOCOL_PROFILE_ID_BYTES 64
#define DOOR_PROTOCOL_BOOT_ID_BYTES 64
#define DOOR_PROTOCOL_VERSION_BYTES 32
#define DOOR_PROTOCOL_EFFECT_ID_BYTES 64
#define DOOR_PROTOCOL_PRESS_ID_BYTES 40
#define DOOR_PROTOCOL_PENDING_DEPTH 8

typedef enum {
    DOOR_PROTOCOL_RX_DROPPED = 0,
    DOOR_PROTOCOL_RX_ACCEPTED = 1,
    DOOR_PROTOCOL_RX_ACK_EMITTED = 2,
} door_protocol_rx_result_t;

typedef struct {
    bool active;
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
    char priority[16];
    uint64_t expires_at_mono_ms;
} door_protocol_cached_profile_t;

typedef struct {
    uint32_t rx_errors;
    uint32_t tx_retries;
    uint32_t profile_updates_applied;
    uint32_t profile_clears_applied;
    uint32_t effect_plays_applied;
} door_protocol_stats_t;

typedef bool (*door_protocol_random_fill_fn)(uint8_t *buffer, size_t len, void *user);

typedef struct {
    char boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    char fw_version[DOOR_PROTOCOL_VERSION_BYTES];
    char peer_boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    uint32_t next_seq;
    bool connected;
    bool fallback_active;
    bool have_pi_heartbeat;
    uint64_t last_pi_heartbeat_mono_ms;
    door_protocol_cached_profile_t cached_profile;
    door_protocol_stats_t stats;

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
} door_protocol_t;

void door_protocol_init(
    door_protocol_t *ctx,
    const char *boot_id,
    const char *fw_version
);

void door_protocol_apply_timeouts(door_protocol_t *ctx, uint64_t now_mono_ms);

bool door_protocol_make_uuid_v4(
    char *out,
    size_t out_len,
    door_protocol_random_fill_fn fill_random,
    void *random_user
);

door_protocol_rx_result_t door_protocol_receive_from_pi(
    door_protocol_t *ctx,
    const char *line,
    size_t line_len,
    uint64_t now_mono_ms,
    const char *sender_boot_id,
    char *ack_out,
    size_t ack_out_len
);

bool door_protocol_start_hello(door_protocol_t *ctx);

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
