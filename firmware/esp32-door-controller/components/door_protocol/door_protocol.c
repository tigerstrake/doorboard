#include "door_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef enum {
    MSG_UNKNOWN = 0,
    MSG_HELLO,
    MSG_HEARTBEAT,
    MSG_PROFILE_UPDATE,
    MSG_PROFILE_CLEAR,
    MSG_EFFECT_PLAY,
    MSG_BUTTON_EVENT,
    MSG_KNOCK_EVENT,
    MSG_CONTACT_EVENT,
    MSG_ACK,
} message_type_t;

typedef struct {
    int version;
    uint32_t seq;
    message_type_t type;
    bool has_ack;
    uint32_t ack;
    char boot_id[DOOR_PROTOCOL_BOOT_ID_BYTES];
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
    char priority[16];
    char reason[24];
    char effect_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
    uint32_t ttl_ms;
    uint32_t duration_ms;
} parsed_message_t;

static void copy_text(char *dst, size_t dst_len, const char *src)
{
    if (dst_len == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    snprintf(dst, dst_len, "%s", src);
}

static const char *skip_ws(const char *cursor)
{
    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\r' || *cursor == '\n') {
        cursor++;
    }
    return cursor;
}

static const char *find_key(const char *json, const char *key)
{
    char needle[48];
    int written = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (written <= 0 || (size_t)written >= sizeof(needle)) {
        return NULL;
    }
    return strstr(json, needle);
}

static bool json_uint(const char *json, const char *key, uint32_t *value)
{
    const char *cursor = find_key(json, key);
    char *end = NULL;
    unsigned long parsed = 0;

    if (cursor == NULL) {
        return false;
    }
    cursor = strchr(cursor, ':');
    if (cursor == NULL) {
        return false;
    }
    cursor = skip_ws(cursor + 1);
    parsed = strtoul(cursor, &end, 10);
    if (end == cursor) {
        return false;
    }
    *value = (uint32_t)parsed;
    return true;
}

static bool json_string(const char *json, const char *key, char *out, size_t out_len)
{
    const char *cursor = find_key(json, key);
    size_t index = 0;

    if (cursor == NULL || out_len == 0) {
        return false;
    }
    cursor = strchr(cursor, ':');
    if (cursor == NULL) {
        return false;
    }
    cursor = skip_ws(cursor + 1);
    if (*cursor != '"') {
        return false;
    }
    cursor++;
    while (*cursor != '\0' && *cursor != '"') {
        if (*cursor == '\\') {
            return false;
        }
        if (index + 1 < out_len) {
            out[index++] = *cursor;
        }
        cursor++;
    }
    if (*cursor != '"') {
        return false;
    }
    out[index] = '\0';
    return true;
}

static message_type_t parse_type(const char *text)
{
    if (strcmp(text, "hello") == 0) {
        return MSG_HELLO;
    }
    if (strcmp(text, "heartbeat") == 0) {
        return MSG_HEARTBEAT;
    }
    if (strcmp(text, "profile_update") == 0) {
        return MSG_PROFILE_UPDATE;
    }
    if (strcmp(text, "profile_clear") == 0) {
        return MSG_PROFILE_CLEAR;
    }
    if (strcmp(text, "effect_play") == 0) {
        return MSG_EFFECT_PLAY;
    }
    if (strcmp(text, "button_event") == 0) {
        return MSG_BUTTON_EVENT;
    }
    if (strcmp(text, "knock_event") == 0) {
        return MSG_KNOCK_EVENT;
    }
    if (strcmp(text, "contact_event") == 0) {
        return MSG_CONTACT_EVENT;
    }
    if (strcmp(text, "ack") == 0) {
        return MSG_ACK;
    }
    return MSG_UNKNOWN;
}

static bool ack_required(message_type_t type)
{
    return type == MSG_HELLO || type == MSG_PROFILE_UPDATE || type == MSG_PROFILE_CLEAR ||
           type == MSG_EFFECT_PLAY || type == MSG_BUTTON_EVENT || type == MSG_KNOCK_EVENT ||
           type == MSG_CONTACT_EVENT;
}

static bool next_seq(door_protocol_t *ctx, uint32_t *seq)
{
    if (ctx->next_seq == UINT32_MAX) {
        return false;
    }
    ctx->next_seq++;
    *seq = ctx->next_seq;
    return true;
}

static bool parse_message(const char *json, parsed_message_t *msg)
{
    char type_text[32];
    uint32_t version = 0;

    memset(msg, 0, sizeof(*msg));
    if (!json_uint(json, "v", &version) || !json_uint(json, "seq", &msg->seq) ||
        !json_string(json, "t", type_text, sizeof(type_text))) {
        return false;
    }

    msg->version = (int)version;
    msg->type = parse_type(type_text);
    if (msg->type == MSG_UNKNOWN) {
        return false;
    }

    msg->has_ack = json_uint(json, "ack", &msg->ack);

    if (msg->type == MSG_HELLO) {
        (void)json_string(json, "boot_id", msg->boot_id, sizeof(msg->boot_id));
    } else if (msg->type == MSG_PROFILE_UPDATE) {
        if (!json_string(json, "profile_id", msg->profile_id, sizeof(msg->profile_id)) ||
            !json_uint(json, "ttl_ms", &msg->ttl_ms) ||
            !json_string(json, "priority", msg->priority, sizeof(msg->priority))) {
            return false;
        }
    } else if (msg->type == MSG_PROFILE_CLEAR) {
        if (!json_string(json, "reason", msg->reason, sizeof(msg->reason))) {
            return false;
        }
    } else if (msg->type == MSG_EFFECT_PLAY) {
        if (!json_string(json, "effect_id", msg->effect_id, sizeof(msg->effect_id)) ||
            !json_uint(json, "duration_ms", &msg->duration_ms)) {
            return false;
        }
    }

    return true;
}

static bool make_ack(door_protocol_t *ctx, uint32_t ack_seq, char *out, size_t out_len)
{
    uint32_t seq = 0;

    if (!next_seq(ctx, &seq)) {
        return false;
    }
    int written = snprintf(
        out,
        out_len,
        "{\"v\":1,\"seq\":%u,\"t\":\"ack\",\"ack\":%u,\"p\":{}}\n",
        seq,
        ack_seq
    );
    return written > 0 && (size_t)written < out_len;
}

static bool remember_seen(door_protocol_t *ctx, const char *boot_id, uint32_t seq)
{
    const char *stable_boot_id = boot_id != NULL && boot_id[0] != '\0' ? boot_id : "unknown";

    for (size_t index = 0; index < (sizeof(ctx->seen) / sizeof(ctx->seen[0])); index++) {
        if (ctx->seen[index].used && ctx->seen[index].seq == seq &&
            strcmp(ctx->seen[index].boot_id, stable_boot_id) == 0) {
            return true;
        }
    }

    ctx->seen[ctx->seen_next].used = true;
    ctx->seen[ctx->seen_next].seq = seq;
    copy_text(ctx->seen[ctx->seen_next].boot_id, sizeof(ctx->seen[ctx->seen_next].boot_id), stable_boot_id);
    ctx->seen_next = (ctx->seen_next + 1U) % (sizeof(ctx->seen) / sizeof(ctx->seen[0]));
    return false;
}

static size_t pending_index(const door_protocol_t *ctx, size_t offset)
{
    return (ctx->pending_head + offset) % DOOR_PROTOCOL_PENDING_DEPTH;
}

static bool start_pending(door_protocol_t *ctx, uint32_t seq, const char *frame)
{
    size_t frame_len = strlen(frame);
    if (frame_len > DOOR_PROTOCOL_MAX_FRAME_BYTES) {
        return false;
    }
    if (ctx->pending_count >= DOOR_PROTOCOL_PENDING_DEPTH) {
        return false;
    }

    size_t index = pending_index(ctx, ctx->pending_count);
    ctx->pending[index].active = true;
    ctx->pending[index].sent_once = false;
    ctx->pending[index].seq = seq;
    ctx->pending[index].retries_sent = 0;
    ctx->pending[index].last_tx_mono_ms = 0;
    copy_text(ctx->pending[index].frame, sizeof(ctx->pending[index].frame), frame);
    ctx->pending_count++;
    return true;
}

static void remove_pending_at(door_protocol_t *ctx, size_t offset)
{
    if (offset >= ctx->pending_count) {
        return;
    }

    if (offset == 0) {
        memset(&ctx->pending[ctx->pending_head], 0, sizeof(ctx->pending[ctx->pending_head]));
        ctx->pending_head = (ctx->pending_head + 1U) % DOOR_PROTOCOL_PENDING_DEPTH;
        ctx->pending_count--;
        if (ctx->pending_count == 0) {
            ctx->pending_head = 0;
        }
        return;
    }

    for (size_t current = offset; current + 1U < ctx->pending_count; current++) {
        size_t dst = pending_index(ctx, current);
        size_t src = pending_index(ctx, current + 1U);
        ctx->pending[dst] = ctx->pending[src];
    }

    size_t tail = pending_index(ctx, ctx->pending_count - 1U);
    memset(&ctx->pending[tail], 0, sizeof(ctx->pending[tail]));
    ctx->pending_count--;
    if (ctx->pending_count == 0) {
        ctx->pending_head = 0;
    }
}

static void ack_pending(door_protocol_t *ctx, uint32_t ack_seq)
{
    for (size_t offset = 0; offset < ctx->pending_count; offset++) {
        size_t index = pending_index(ctx, offset);
        if (ctx->pending[index].active && ctx->pending[index].seq == ack_seq) {
            remove_pending_at(ctx, offset);
            return;
        }
    }
}

static void apply_message_once(door_protocol_t *ctx, const parsed_message_t *msg, uint64_t now_mono_ms)
{
    if (msg->type == MSG_HELLO) {
        ctx->connected = true;
        ctx->fallback_active = false;
        ctx->have_pi_heartbeat = true;
        ctx->last_pi_heartbeat_mono_ms = now_mono_ms;
        if (msg->boot_id[0] != '\0') {
            copy_text(ctx->peer_boot_id, sizeof(ctx->peer_boot_id), msg->boot_id);
        }
        return;
    }

    if (msg->type == MSG_HEARTBEAT) {
        ctx->connected = true;
        ctx->fallback_active = false;
        ctx->have_pi_heartbeat = true;
        ctx->last_pi_heartbeat_mono_ms = now_mono_ms;
        return;
    }

    if (msg->type == MSG_PROFILE_UPDATE) {
        ctx->cached_profile.active = true;
        ctx->cached_profile.expires_at_mono_ms = now_mono_ms + msg->ttl_ms;
        copy_text(ctx->cached_profile.profile_id, sizeof(ctx->cached_profile.profile_id), msg->profile_id);
        copy_text(ctx->cached_profile.priority, sizeof(ctx->cached_profile.priority), msg->priority);
        ctx->stats.profile_updates_applied++;
        return;
    }

    if (msg->type == MSG_PROFILE_CLEAR) {
        memset(&ctx->cached_profile, 0, sizeof(ctx->cached_profile));
        ctx->stats.profile_clears_applied++;
        return;
    }

    if (msg->type == MSG_EFFECT_PLAY) {
        ctx->stats.effect_plays_applied++;
    }
}

void door_protocol_init(door_protocol_t *ctx, const char *boot_id, const char *fw_version)
{
    memset(ctx, 0, sizeof(*ctx));
    copy_text(ctx->boot_id, sizeof(ctx->boot_id), boot_id);
    copy_text(ctx->fw_version, sizeof(ctx->fw_version), fw_version);
    ctx->fallback_active = true;
}

void door_protocol_apply_timeouts(door_protocol_t *ctx, uint64_t now_mono_ms)
{
    if (ctx->cached_profile.active && now_mono_ms >= ctx->cached_profile.expires_at_mono_ms) {
        memset(&ctx->cached_profile, 0, sizeof(ctx->cached_profile));
    }
    if (!ctx->have_pi_heartbeat ||
        now_mono_ms - ctx->last_pi_heartbeat_mono_ms > DOOR_PROTOCOL_HEARTBEAT_TIMEOUT_MS) {
        ctx->fallback_active = true;
    }
}

bool door_protocol_make_uuid_v4(
    char *out,
    size_t out_len,
    door_protocol_random_fill_fn fill_random,
    void *random_user
)
{
    uint8_t bytes[16];
    int written = 0;

    if (out == NULL || out_len < DOOR_PROTOCOL_PRESS_ID_BYTES || fill_random == NULL) {
        return false;
    }
    if (!fill_random(bytes, sizeof(bytes), random_user)) {
        return false;
    }

    bytes[6] = (uint8_t)((bytes[6] & 0x0FU) | 0x40U);
    bytes[8] = (uint8_t)((bytes[8] & 0x3FU) | 0x80U);

    written = snprintf(
        out,
        out_len,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        bytes[0],
        bytes[1],
        bytes[2],
        bytes[3],
        bytes[4],
        bytes[5],
        bytes[6],
        bytes[7],
        bytes[8],
        bytes[9],
        bytes[10],
        bytes[11],
        bytes[12],
        bytes[13],
        bytes[14],
        bytes[15]
    );
    return written == 36;
}

door_protocol_rx_result_t door_protocol_receive_from_pi(
    door_protocol_t *ctx,
    const char *line,
    size_t line_len,
    uint64_t now_mono_ms,
    const char *sender_boot_id,
    char *ack_out,
    size_t ack_out_len
)
{
    char buffer[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    parsed_message_t msg;
    bool duplicate = false;
    const char *dedupe_boot_id = sender_boot_id;

    if (ack_out_len > 0) {
        ack_out[0] = '\0';
    }
    door_protocol_apply_timeouts(ctx, now_mono_ms);

    if (line == NULL || line_len == 0 || line_len > DOOR_PROTOCOL_MAX_FRAME_BYTES) {
        ctx->stats.rx_errors++;
        return DOOR_PROTOCOL_RX_DROPPED;
    }

    while (line_len > 0 && (line[line_len - 1] == '\n' || line[line_len - 1] == '\r')) {
        line_len--;
    }
    if (line_len > DOOR_PROTOCOL_MAX_FRAME_BYTES) {
        ctx->stats.rx_errors++;
        return DOOR_PROTOCOL_RX_DROPPED;
    }
    memcpy(buffer, line, line_len);
    buffer[line_len] = '\0';

    if (!parse_message(buffer, &msg) || msg.version != DOOR_PROTOCOL_VERSION) {
        ctx->stats.rx_errors++;
        return DOOR_PROTOCOL_RX_DROPPED;
    }

    if (msg.type == MSG_ACK) {
        if (msg.has_ack) {
            ack_pending(ctx, msg.ack);
        }
        return DOOR_PROTOCOL_RX_ACCEPTED;
    }

    if ((dedupe_boot_id == NULL || dedupe_boot_id[0] == '\0') && msg.boot_id[0] != '\0') {
        dedupe_boot_id = msg.boot_id;
    }
    duplicate = remember_seen(ctx, dedupe_boot_id, msg.seq);
    if (!duplicate) {
        apply_message_once(ctx, &msg, now_mono_ms);
    }

    if (ack_required(msg.type)) {
        if (!make_ack(ctx, msg.seq, ack_out, ack_out_len)) {
            ctx->stats.rx_errors++;
            return DOOR_PROTOCOL_RX_DROPPED;
        }
        return DOOR_PROTOCOL_RX_ACK_EMITTED;
    }

    return DOOR_PROTOCOL_RX_ACCEPTED;
}

bool door_protocol_start_hello(door_protocol_t *ctx)
{
    uint32_t seq = 0;
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    int written = 0;

    if (!next_seq(ctx, &seq)) {
        return false;
    }
    written = snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":%u,\"t\":\"hello\",\"ack\":null,\"p\":{\"fw_version\":\"%s\","
        "\"proto_v\":1,\"boot_id\":\"%s\"}}\n",
        seq,
        ctx->fw_version,
        ctx->boot_id
    );
    if (written <= 0 || (size_t)written >= sizeof(frame)) {
        return false;
    }
    return start_pending(ctx, seq, frame);
}

bool door_protocol_emit_button_event(
    door_protocol_t *ctx,
    const char *press_id,
    uint64_t pressed_at_mono_ms
)
{
    uint32_t seq = 0;
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    int written = 0;

    door_protocol_apply_timeouts(ctx, pressed_at_mono_ms);
    if (!next_seq(ctx, &seq)) {
        return false;
    }

    if (ctx->cached_profile.active) {
        written = snprintf(
            frame,
            sizeof(frame),
            "{\"v\":1,\"seq\":%u,\"t\":\"button_event\",\"ack\":null,\"p\":{\"press_id\":\"%s\","
            "\"pressed_at_mono_ms\":%llu,\"had_cached_profile\":true,\"profile_id\":\"%s\"}}\n",
            seq,
            press_id,
            (unsigned long long)pressed_at_mono_ms,
            ctx->cached_profile.profile_id
        );
    } else {
        written = snprintf(
            frame,
            sizeof(frame),
            "{\"v\":1,\"seq\":%u,\"t\":\"button_event\",\"ack\":null,\"p\":{\"press_id\":\"%s\","
            "\"pressed_at_mono_ms\":%llu,\"had_cached_profile\":false,\"profile_id\":null}}\n",
            seq,
            press_id,
            (unsigned long long)pressed_at_mono_ms
        );
    }

    if (written <= 0 || (size_t)written >= sizeof(frame)) {
        return false;
    }
    return start_pending(ctx, seq, frame);
}

bool door_protocol_emit_knock_event(
    door_protocol_t *ctx,
    const char *pattern_id,
    double confidence
)
{
    uint32_t seq = 0;
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    int written = 0;

    if (!next_seq(ctx, &seq)) {
        return false;
    }
    written = snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":%u,\"t\":\"knock_event\",\"ack\":null,\"p\":{\"pattern_id\":\"%s\","
        "\"confidence\":%.3f}}\n",
        seq,
        pattern_id,
        confidence
    );
    if (written <= 0 || (size_t)written >= sizeof(frame)) {
        return false;
    }
    return start_pending(ctx, seq, frame);
}

bool door_protocol_next_tx(
    door_protocol_t *ctx,
    uint64_t now_mono_ms,
    char *frame_out,
    size_t frame_out_len
)
{
    if (ctx->pending_count == 0 || frame_out_len == 0) {
        return false;
    }

    size_t index = pending_index(ctx, 0);
    if (!ctx->pending[index].active) {
        remove_pending_at(ctx, 0);
        return false;
    }

    if (!ctx->pending[index].sent_once) {
        copy_text(frame_out, frame_out_len, ctx->pending[index].frame);
        ctx->pending[index].sent_once = true;
        ctx->pending[index].last_tx_mono_ms = now_mono_ms;
        return true;
    }

    if (now_mono_ms - ctx->pending[index].last_tx_mono_ms < DOOR_PROTOCOL_RETRY_SPACING_MS) {
        return false;
    }

    if (ctx->pending[index].retries_sent >= DOOR_PROTOCOL_MAX_RETRIES) {
        remove_pending_at(ctx, 0);
        return false;
    }

    ctx->pending[index].retries_sent++;
    ctx->stats.tx_retries++;
    ctx->pending[index].last_tx_mono_ms = now_mono_ms;
    copy_text(frame_out, frame_out_len, ctx->pending[index].frame);
    return true;
}

bool door_protocol_make_heartbeat(
    door_protocol_t *ctx,
    uint64_t now_mono_ms,
    uint32_t uptime_s,
    char *frame_out,
    size_t frame_out_len
)
{
    uint32_t seq = 0;
    int written = 0;

    door_protocol_apply_timeouts(ctx, now_mono_ms);
    if (!next_seq(ctx, &seq)) {
        return false;
    }

    if (ctx->cached_profile.active) {
        written = snprintf(
            frame_out,
            frame_out_len,
            "{\"v\":1,\"seq\":%u,\"t\":\"heartbeat\",\"ack\":null,\"p\":{\"uptime_s\":%u,"
            "\"fallback_active\":%s,\"cached_profile_id\":\"%s\"}}\n",
            seq,
            uptime_s,
            ctx->fallback_active ? "true" : "false",
            ctx->cached_profile.profile_id
        );
    } else {
        written = snprintf(
            frame_out,
            frame_out_len,
            "{\"v\":1,\"seq\":%u,\"t\":\"heartbeat\",\"ack\":null,\"p\":{\"uptime_s\":%u,"
            "\"fallback_active\":%s,\"cached_profile_id\":null}}\n",
            seq,
            uptime_s,
            ctx->fallback_active ? "true" : "false"
        );
    }

    return written > 0 && (size_t)written < frame_out_len &&
           strlen(frame_out) <= DOOR_PROTOCOL_MAX_FRAME_BYTES;
}

bool door_protocol_has_cached_profile(door_protocol_t *ctx, uint64_t now_mono_ms)
{
    door_protocol_apply_timeouts(ctx, now_mono_ms);
    return ctx->cached_profile.active;
}

const char *door_protocol_cached_profile_id(door_protocol_t *ctx, uint64_t now_mono_ms)
{
    door_protocol_apply_timeouts(ctx, now_mono_ms);
    return ctx->cached_profile.active ? ctx->cached_profile.profile_id : NULL;
}
