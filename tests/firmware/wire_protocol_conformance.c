#include "door_protocol.h"

#include <assert.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void assert_contains(const char *haystack, const char *needle)
{
    assert(strstr(haystack, needle) != NULL);
}

static bool fill_incrementing_random(uint8_t *buffer, size_t len, void *user)
{
    uint8_t *next = (uint8_t *)user;

    for (size_t index = 0; index < len; index++) {
        buffer[index] = *next;
        (*next)++;
    }
    return true;
}

static uint32_t next_boot_word;

static uint32_t counting_rng(void)
{
    return next_boot_word++;
}

static size_t seen_entries_in_use(const door_protocol_t *esp32)
{
    size_t used = 0;

    for (size_t index = 0; index < sizeof(esp32->seen) / sizeof(esp32->seen[0]); index++) {
        if (esp32->seen[index].used) {
            used++;
        }
    }
    return used;
}

static void test_boot_id_is_sixteen_hex_chars_and_differs_per_boot(void)
{
    char first[DOOR_PROTOCOL_BOOT_ID_HEX_CHARS + 1];
    char second[DOOR_PROTOCOL_BOOT_ID_HEX_CHARS + 1];
    char too_small[DOOR_PROTOCOL_BOOT_ID_HEX_CHARS];

    next_boot_word = 0xDEADBEEFU;
    assert(door_protocol_make_boot_id(first, sizeof(first), counting_rng));
    assert(strlen(first) == DOOR_PROTOCOL_BOOT_ID_HEX_CHARS);
    assert(strcmp(first, "deadbeefdeadbef0") == 0);
    for (size_t index = 0; index < DOOR_PROTOCOL_BOOT_ID_HEX_CHARS; index++) {
        assert(strchr("0123456789abcdef", first[index]) != NULL);
    }

    assert(door_protocol_make_boot_id(second, sizeof(second), counting_rng));
    assert(strcmp(first, second) != 0);

    assert(!door_protocol_make_boot_id(too_small, sizeof(too_small), counting_rng));
    assert(!door_protocol_make_boot_id(first, sizeof(first), NULL));
}

static void test_pi_hello_from_a_new_boot_forgets_remembered_frames(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *hello_one =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-one\"}}\n";
    const char *hello_two =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-two\"}}\n";
    const char *profile =
        "{\"v\":1,\"seq\":2,\"t\":\"profile_update\",\"ack\":null,\"p\":{"
        "\"profile_id\":\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_receive_from_pi(
        &esp32, hello_one, strlen(hello_one), 0, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_receive_from_pi(
        &esp32, profile, strlen(profile), 1, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(seen_entries_in_use(&esp32) == 2);
    assert(strcmp(esp32.peer_boot_id, "pi-boot-one") == 0);

    /* A retransmit of the same hello is still a duplicate: same boot, same seq. */
    assert(door_protocol_receive_from_pi(
        &esp32, hello_one, strlen(hello_one), 2, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(seen_entries_in_use(&esp32) == 2);

    /* A hello from a new boot starts a fresh ring holding only that hello. */
    assert(door_protocol_receive_from_pi(
        &esp32, hello_two, strlen(hello_two), 3, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(seen_entries_in_use(&esp32) == 1);
    assert(strcmp(esp32.peer_boot_id, "pi-boot-two") == 0);
    assert(door_protocol_receive_from_pi(
        &esp32, profile, strlen(profile), 4, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == 2);
}

static void test_uuid_v4_press_ids_use_random_version_and_variant_bits(void)
{
    uint8_t next = 0;
    char first[DOOR_PROTOCOL_PRESS_ID_BYTES];
    char second[DOOR_PROTOCOL_PRESS_ID_BYTES];

    assert(door_protocol_make_uuid_v4(first, sizeof(first), fill_incrementing_random, &next));
    assert(strcmp(first, "00010203-0405-4607-8809-0a0b0c0d0e0f") == 0);

    assert(door_protocol_make_uuid_v4(second, sizeof(second), fill_incrementing_random, &next));
    assert(strcmp(second, "10111213-1415-4617-9819-1a1b1c1d1e1f") == 0);
    assert(strcmp(first, second) != 0);
}

static void test_profile_update_ack_and_duplicate_dedupe(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");

    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );
    assert_contains(ack, "\"t\":\"ack\"");
    assert_contains(ack, "\"seq\":1");
    assert_contains(ack, "\"ack\":1");

    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );
    assert(esp32.stats.profile_updates_applied == 1);
    assert(door_protocol_has_cached_profile(&esp32, 0));
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 0), "blue_wave") == 0);
}

static void test_ack_consumes_esp32_sequence_numbers(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"profile_clear\",\"ack\":null,\"p\":{\"reason\":\"test\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );
    assert_contains(ack, "\"seq\":1");

    assert(door_protocol_start_hello(&esp32));
    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert_contains(tx, "\"seq\":2");
    assert_contains(tx, "\"t\":\"hello\"");
}

static void test_retransmit_three_times_at_fifty_ms(void)
{
    door_protocol_t esp32;
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_emit_button_event(
        &esp32,
        "00000000-0000-0000-0000-000000000001",
        0
    ));

    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert_contains(tx, "\"t\":\"button_event\"");
    assert(!door_protocol_next_tx(&esp32, 49, tx, sizeof(tx)));
    assert(door_protocol_next_tx(&esp32, 50, tx, sizeof(tx)));
    assert(door_protocol_next_tx(&esp32, 100, tx, sizeof(tx)));
    assert(door_protocol_next_tx(&esp32, 150, tx, sizeof(tx)));
    assert(esp32.stats.tx_retries == 3);
    assert(!door_protocol_next_tx(&esp32, 200, tx, sizeof(tx)));
}

static void test_ack_clears_pending_retransmit(void)
{
    door_protocol_t esp32;
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *ack_frame = "{\"v\":1,\"seq\":9,\"t\":\"ack\",\"ack\":1,\"p\":{}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_emit_button_event(
        &esp32,
        "00000000-0000-0000-0000-000000000001",
        0
    ));
    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert(
        door_protocol_receive_from_pi(&esp32, ack_frame, strlen(ack_frame), 1, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACCEPTED
    );
    assert(!door_protocol_next_tx(&esp32, 50, tx, sizeof(tx)));
}

static void test_two_button_events_queue_behind_delayed_ack(void)
{
    door_protocol_t esp32;
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *first_ack = "{\"v\":1,\"seq\":90,\"t\":\"ack\",\"ack\":1,\"p\":{}}\n";
    const char *second_ack = "{\"v\":1,\"seq\":91,\"t\":\"ack\",\"ack\":2,\"p\":{}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_emit_button_event(
        &esp32,
        "11111111-1111-4111-8111-111111111111",
        0
    ));
    assert(door_protocol_emit_button_event(
        &esp32,
        "22222222-2222-4222-8222-222222222222",
        10
    ));

    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert_contains(tx, "\"seq\":1");
    assert_contains(tx, "\"press_id\":\"11111111-1111-4111-8111-111111111111\"");

    assert(!door_protocol_next_tx(&esp32, 10, tx, sizeof(tx)));
    assert(
        door_protocol_receive_from_pi(&esp32, first_ack, strlen(first_ack), 20, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACCEPTED
    );

    assert(door_protocol_next_tx(&esp32, 20, tx, sizeof(tx)));
    assert_contains(tx, "\"seq\":2");
    assert_contains(tx, "\"press_id\":\"22222222-2222-4222-8222-222222222222\"");

    assert(
        door_protocol_receive_from_pi(&esp32, second_ack, strlen(second_ack), 30, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACCEPTED
    );
    assert(!door_protocol_next_tx(&esp32, 70, tx, sizeof(tx)));
}

static void test_profile_cache_ttl_expiry_uses_local_monotonic_time(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":1000,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );

    assert(door_protocol_has_cached_profile(&esp32, 999));
    assert(!door_protocol_has_cached_profile(&esp32, 1000));
}

static void test_fallback_starts_after_pi_heartbeat_loss(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *hello =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":\"doorboard-test\","
        "\"proto_v\":1,\"boot_id\":\"pi-test\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, hello, strlen(hello), 0, "pi-test", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );

    door_protocol_apply_timeouts(&esp32, 5000);
    assert(!esp32.fallback_active);
    door_protocol_apply_timeouts(&esp32, 5001);
    assert(esp32.fallback_active);
}

static void test_button_event_reports_cached_profile(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );
    assert(door_protocol_emit_button_event(
        &esp32,
        "00000000-0000-0000-0000-000000000001",
        10
    ));
    assert(door_protocol_next_tx(&esp32, 10, tx, sizeof(tx)));

    assert_contains(tx, "\"t\":\"button_event\"");
    assert_contains(tx, "\"had_cached_profile\":true");
    assert_contains(tx, "\"profile_id\":\"blue_wave\"");
}

static void test_malformed_wire_message_counts_rx_error(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *bad = "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, bad, strlen(bad), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_DROPPED
    );
    assert(esp32.stats.rx_errors == 1);
}

static void test_protocol_version_rejection_counts_rx_error(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *bad = "{\"v\":99,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(
        door_protocol_receive_from_pi(&esp32, bad, strlen(bad), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_DROPPED
    );
    assert(esp32.stats.rx_errors == 1);
}

static void test_oversized_frame_counts_rx_error(void)
{
    door_protocol_t esp32;
    char oversized[DOOR_PROTOCOL_MAX_FRAME_BYTES + 2];
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];

    door_protocol_init(&esp32, "esp32-test", "host-test");
    memset(oversized, 'x', sizeof(oversized));
    assert(
        door_protocol_receive_from_pi(&esp32, oversized, sizeof(oversized), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_DROPPED
    );
    assert(esp32.stats.rx_errors == 1);
}

static void test_peer_reboot_resets_dedupe_identity_for_frames_without_boot_id(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *hello_one =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-one\"}}\n";
    const char *hello_two =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-two\"}}\n";
    const char *profile =
        "{\"v\":1,\"seq\":2,\"t\":\"profile_update\",\"ack\":null,\"p\":{"
        "\"profile_id\":\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_receive_from_pi(
        &esp32, hello_one, strlen(hello_one), 0, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_receive_from_pi(
        &esp32, profile, strlen(profile), 1, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == 1);

    assert(door_protocol_receive_from_pi(
        &esp32, hello_two, strlen(hello_two), 2, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_receive_from_pi(
        &esp32, profile, strlen(profile), 3, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == 2);
}

static void test_pending_queue_overflow_counts_tx_dropped(void)
{
    door_protocol_t esp32;
    char press_id[DOOR_PROTOCOL_PRESS_ID_BYTES];

    door_protocol_init(&esp32, "esp32-test", "host-test");
    for (int index = 0; index < DOOR_PROTOCOL_PENDING_DEPTH; index++) {
        snprintf(press_id, sizeof(press_id), "00000000-0000-4000-8000-%012d", index);
        assert(door_protocol_emit_button_event(&esp32, press_id, (uint64_t)index));
    }
    assert(esp32.pending_count == DOOR_PROTOCOL_PENDING_DEPTH);
    assert(esp32.stats.tx_dropped == 0);

    assert(!door_protocol_emit_button_event(&esp32, "00000000-0000-4000-8000-000000000099", 99));
    assert(esp32.stats.tx_dropped == 1);
    assert(esp32.pending_count == DOOR_PROTOCOL_PENDING_DEPTH);
}

static void test_seen_ring_evicts_after_sixteen_frames(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const size_t ring = sizeof(esp32.seen) / sizeof(esp32.seen[0]);

    door_protocol_init(&esp32, "esp32-test", "host-test");
    for (size_t seq = 1; seq <= ring; seq++) {
        snprintf(
            frame,
            sizeof(frame),
            "{\"v\":1,\"seq\":%zu,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
            "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n",
            seq
        );
        assert(door_protocol_receive_from_pi(
            &esp32, frame, strlen(frame), seq, "pi-boot", ack, sizeof(ack)
        ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    }
    assert(esp32.stats.profile_updates_applied == ring);

    /* seq 1 is still remembered: the ring is exactly full. */
    snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n"
    );
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 20, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == ring);

    /* One more distinct frame evicts the oldest entry (seq 1) ... */
    snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":%zu,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n",
        ring + 1
    );
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 21, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == ring + 1);

    /* ... so a replay of seq 1 is applied again. */
    snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":1,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n"
    );
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 22, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == ring + 2);
}

static char cb_effect_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
static uint32_t cb_duration_ms;
static int cb_calls;

static void record_effect_play(const char *effect_id, uint32_t duration_ms, void *user)
{
    int *calls = (int *)user;
    snprintf(cb_effect_id, sizeof(cb_effect_id), "%s", effect_id);
    cb_duration_ms = duration_ms;
    (*calls)++;
}

static void test_effect_play_reaches_callback_once_and_is_collectable_without_one(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char taken_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
    uint32_t taken_duration = 0;
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"effect_play\",\"ack\":null,\"p\":{\"effect_id\":\"sparkle\","
        "\"duration_ms\":900}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    esp32.effect_play_cb = record_effect_play;
    esp32.effect_play_cb_user = &cb_calls;
    cb_calls = 0;

    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(cb_calls == 1);
    assert(strcmp(cb_effect_id, "sparkle") == 0);
    assert(cb_duration_ms == 900);
    assert(esp32.stats.effect_plays_applied == 1);
    /* Delivered through the callback: nothing left to collect. */
    assert(!door_protocol_take_effect_play(&esp32, taken_id, sizeof(taken_id), &taken_duration));

    /* A retransmit is acked but not replayed. */
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 1, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(cb_calls == 1);

    /* Without a callback (the device path, which holds a lock around apply) the
     * request waits in the context for the caller to collect it. */
    esp32.effect_play_cb = NULL;
    door_protocol_message_t msg;
    const char *second =
        "{\"v\":1,\"seq\":2,\"t\":\"effect_play\",\"ack\":null,\"p\":{\"effect_id\":\"rainbow\","
        "\"duration_ms\":1500}}\n";
    assert(door_protocol_parse_line(second, strlen(second), &msg));
    assert(msg.type == DOOR_PROTOCOL_MSG_EFFECT_PLAY);
    assert(door_protocol_apply_message(&esp32, &msg, 2, "pi-boot", ack, sizeof(ack)) ==
           DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_take_effect_play(&esp32, taken_id, sizeof(taken_id), &taken_duration));
    assert(strcmp(taken_id, "rainbow") == 0);
    assert(taken_duration == 1500);
    assert(!door_protocol_take_effect_play(&esp32, taken_id, sizeof(taken_id), &taken_duration));
    assert(cb_calls == 1);
}

static void test_pi_hello_after_peer_is_known_is_answered_with_a_hello(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *hello_one =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-one\"}}\n";
    const char *hello_two =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":"
        "\"doorboard-test\",\"proto_v\":1,\"boot_id\":\"pi-boot-two\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_receive_from_pi(
        &esp32, hello_one, strlen(hello_one), 0, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(esp32.peer_boot_id, "pi-boot-one") == 0);
    /* The ack was built after our hello was queued, so it carries the next seq. */
    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert_contains(tx, "\"t\":\"hello\"");
    assert_contains(tx, "\"seq\":1");
    assert_contains(ack, "\"seq\":2");
    door_protocol_message_t pi_ack;
    const char *ack_frame = "{\"v\":1,\"seq\":2,\"t\":\"ack\",\"ack\":1,\"p\":{}}\n";
    assert(door_protocol_parse_line(ack_frame, strlen(ack_frame), &pi_ack));
    assert(door_protocol_apply_message(&esp32, &pi_ack, 1, NULL, ack, sizeof(ack)) ==
           DOOR_PROTOCOL_RX_ACCEPTED);
    assert(esp32.pending_count == 0);

    /* A retransmitted hello: acked, no second handshake from our side. */
    assert(door_protocol_receive_from_pi(
        &esp32, hello_one, strlen(hello_one), 2, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.pending_count == 0);

    /* The Pi restarted: new boot_id, and we introduce ourselves again. */
    assert(door_protocol_receive_from_pi(
        &esp32, hello_two, strlen(hello_two), 3, NULL, ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(esp32.peer_boot_id, "pi-boot-two") == 0);
    assert(esp32.pending_count == 1);
    assert(door_protocol_next_tx(&esp32, 3, tx, sizeof(tx)));
    assert_contains(tx, "\"t\":\"hello\"");
    assert_contains(tx, "\"boot_id\":\"esp32-test\"");
}

static size_t feed_bytes(door_protocol_t *esp32, const char *bytes, uint64_t now_mono_ms)
{
    size_t line_len = 0;

    for (const char *cursor = bytes; *cursor != '\0'; cursor++) {
        size_t ready = door_protocol_rx_byte(esp32, (uint8_t)*cursor, now_mono_ms);
        if (ready > 0) {
            line_len = ready;
        }
    }
    return line_len;
}

static void test_partial_frame_is_discarded_after_silence_and_on_hello(void)
{
    door_protocol_t esp32;
    door_protocol_message_t msg;
    const char *heartbeat =
        "{\"v\":1,\"seq\":7,\"t\":\"heartbeat\",\"ack\":null,\"p\":{\"uptime_s\":1,"
        "\"fallback_active\":false}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");

    /* Half a frame, then a second of silence: the stale prefix must not be
     * glued onto the next frame. */
    assert(feed_bytes(&esp32, "{\"v\":1,\"seq\":", 100) == 0);
    assert(esp32.rx.len > 0);
    size_t line_len = feed_bytes(&esp32, heartbeat, 1100);
    assert(line_len == strlen(heartbeat));
    assert(esp32.stats.rx_errors == 1);
    assert(door_protocol_parse_line(esp32.rx.line, line_len, &msg));
    assert(msg.type == DOOR_PROTOCOL_MSG_HEARTBEAT);
    assert(msg.seq == 7);

    /* Within the timeout a frame may still arrive in pieces. */
    assert(feed_bytes(&esp32, "{\"v\":1,\"seq\":8,", 2000) == 0);
    line_len = feed_bytes(&esp32, "\"t\":\"heartbeat\",\"ack\":null,\"p\":{}}\n", 2500);
    assert(line_len > 0);
    assert(door_protocol_parse_line(esp32.rx.line, line_len, &msg));
    assert(msg.seq == 8);
    assert(esp32.stats.rx_errors == 1);

    /* A hello (link restart) drops whatever was half-received. */
    assert(feed_bytes(&esp32, "{\"v\":1,", 3000) == 0);
    assert(door_protocol_start_hello(&esp32));
    assert(esp32.rx.len == 0);
    assert(esp32.stats.rx_errors == 2);

    /* A bare newline is not a frame and not an error. */
    assert(feed_bytes(&esp32, "\n", 3001) == 0);
    assert(esp32.stats.rx_errors == 2);
}

static void test_oversize_line_is_skipped_to_its_newline(void)
{
    door_protocol_t esp32;
    door_protocol_message_t msg;
    char oversize[DOOR_PROTOCOL_MAX_FRAME_BYTES + 2 + 1];
    char at_cap[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1 + 1];
    const char *heartbeat =
        "{\"v\":1,\"seq\":9,\"t\":\"heartbeat\",\"ack\":null,\"p\":{\"uptime_s\":1,"
        "\"fallback_active\":false}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");

    /* 513 payload bytes: over the cap the Pi applies (512 excluding '\n'). Nothing
     * of it -- not even its tail -- may surface as a line, and it counts once. */
    memset(oversize, 'x', sizeof(oversize) - 1);
    oversize[sizeof(oversize) - 2] = '\n';
    oversize[sizeof(oversize) - 1] = '\0';
    assert(feed_bytes(&esp32, oversize, 100) == 0);
    assert(esp32.stats.rx_errors == 1);
    assert(!esp32.rx.discarding);

    size_t line_len = feed_bytes(&esp32, heartbeat, 200);
    assert(line_len == strlen(heartbeat));
    assert(door_protocol_parse_line(esp32.rx.line, line_len, &msg));
    assert(msg.seq == 9);
    assert(esp32.stats.rx_errors == 1);

    /* Exactly 512 payload bytes is a line (the parser then rejects it as JSON). */
    memset(at_cap, 'y', sizeof(at_cap) - 1);
    at_cap[sizeof(at_cap) - 2] = '\n';
    at_cap[sizeof(at_cap) - 1] = '\0';
    line_len = feed_bytes(&esp32, at_cap, 300);
    assert(line_len == DOOR_PROTOCOL_MAX_FRAME_BYTES + 1);
    assert(!door_protocol_parse_line(esp32.rx.line, line_len, &msg));
    assert(esp32.stats.rx_errors == 1);

    /* An oversize line whose sender goes quiet mid-line: after the partial-frame
     * timeout the next bytes are a fresh line, not more of the junk. */
    memset(oversize, 'z', sizeof(oversize) - 1);
    oversize[sizeof(oversize) - 2] = 'z';
    oversize[sizeof(oversize) - 1] = '\0';
    assert(feed_bytes(&esp32, oversize, 400) == 0);
    assert(esp32.rx.discarding);
    assert(esp32.stats.rx_errors == 2);
    line_len = feed_bytes(&esp32, heartbeat, 1500);
    assert(line_len == strlen(heartbeat));
    assert(door_protocol_parse_line(esp32.rx.line, line_len, &msg));
    assert(msg.seq == 9);
}

static void test_key_lookup_ignores_matching_string_values(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    /* Keys sorted the way the Pi's json.dumps(sort_keys=True) emits them: the
     * payload's "profile_id":"t" comes before the top-level "t" key, and a
     * priority of "seq" sits before "seq" -- an unanchored search matched the
     * values. */
    const char *frame =
        "{\"ack\":null,\"p\":{\"priority\":\"seq\",\"profile_id\":\"t\",\"ttl_ms\":2500},"
        "\"seq\":11,\"t\":\"profile_update\",\"v\":1}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert_contains(ack, "\"ack\":11");
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 0), "t") == 0);
    assert(strcmp(esp32.cached_profile.priority, "seq") == 0);
    assert(esp32.stats.rx_errors == 0);
}

static void test_fallback_edge_fires_once_and_requeues_hello(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *hello =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":\"doorboard-test\","
        "\"proto_v\":1,\"boot_id\":\"pi-test\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    /* Boot state is fallback but is not an edge: no animation, no extra hello. */
    assert(!door_protocol_fallback_edge(&esp32, 0));
    assert(esp32.pending_count == 0);

    assert(door_protocol_receive_from_pi(
        &esp32, hello, strlen(hello), 100, "pi-test", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    /* Drain the hello we answered with. */
    assert(door_protocol_next_tx(&esp32, 100, tx, sizeof(tx)));
    for (uint64_t at = 150; at <= 300; at += 50) {
        (void)door_protocol_next_tx(&esp32, at, tx, sizeof(tx));
    }
    assert(!door_protocol_next_tx(&esp32, 400, tx, sizeof(tx)));
    assert(esp32.pending_count == 0);

    assert(!door_protocol_fallback_edge(&esp32, 5100));
    assert(door_protocol_fallback_edge(&esp32, 5101));
    assert(esp32.fallback_active);
    assert(esp32.pending_count == 1);
    assert(door_protocol_next_tx(&esp32, 5101, tx, sizeof(tx)));
    assert_contains(tx, "\"t\":\"hello\"");
    /* Still in fallback a second later: the edge does not repeat. */
    assert(!door_protocol_fallback_edge(&esp32, 6101));
}

static void test_press_followup_is_profile_then_fallback_then_nothing(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
    const char *hello =
        "{\"v\":1,\"seq\":1,\"t\":\"hello\",\"ack\":null,\"p\":{\"sw_version\":\"doorboard-test\","
        "\"proto_v\":1,\"boot_id\":\"pi-test\"}}\n";
    const char *profile =
        "{\"v\":1,\"seq\":2,\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"blue_wave\",\"ttl_ms\":2500,\"priority\":\"normal\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    /* Fresh from boot the Pi is unknown: a press shows the fallback animation. */
    assert(door_protocol_press_followup(&esp32, 0, profile_id, sizeof(profile_id)) ==
           DOOR_PROTOCOL_PRESS_FALLBACK);
    assert(profile_id[0] == '\0');

    assert(door_protocol_receive_from_pi(
        &esp32, hello, strlen(hello), 100, "pi-test", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_press_followup(&esp32, 200, profile_id, sizeof(profile_id)) ==
           DOOR_PROTOCOL_PRESS_GENERIC_ONLY);

    assert(door_protocol_receive_from_pi(
        &esp32, profile, strlen(profile), 300, "pi-test", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(door_protocol_press_followup(&esp32, 400, profile_id, sizeof(profile_id)) ==
           DOOR_PROTOCOL_PRESS_PROFILE);
    assert(strcmp(profile_id, "blue_wave") == 0);

    /* The profile expires at 2800; the Pi is lost at 5101. */
    assert(door_protocol_press_followup(&esp32, 2800, profile_id, sizeof(profile_id)) ==
           DOOR_PROTOCOL_PRESS_GENERIC_ONLY);
    assert(door_protocol_press_followup(&esp32, 5101, profile_id, sizeof(profile_id)) ==
           DOOR_PROTOCOL_PRESS_FALLBACK);
}

static void test_unknown_effect_id_is_acked_but_counted_as_rejected(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char taken_id[DOOR_PROTOCOL_EFFECT_ID_BYTES];
    const char *unknown =
        "{\"v\":1,\"seq\":1,\"t\":\"effect_play\",\"ack\":null,\"p\":{\"effect_id\":\"disco\","
        "\"duration_ms\":900}}\n";
    const char *known =
        "{\"v\":1,\"seq\":2,\"t\":\"effect_play\",\"ack\":null,\"p\":{\"effect_id\":\"mint_pulse\","
        "\"duration_ms\":0}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_receive_from_pi(
        &esp32, unknown, strlen(unknown), 0, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert_contains(ack, "\"ack\":1");
    assert(esp32.stats.effect_plays_applied == 0);
    assert(esp32.stats.effect_plays_rejected == 1);
    assert(!door_protocol_take_effect_play(&esp32, taken_id, sizeof(taken_id), NULL));

    assert(door_protocol_receive_from_pi(
        &esp32, known, strlen(known), 1, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.effect_plays_applied == 1);
    assert(esp32.stats.effect_plays_rejected == 1);
    assert(door_protocol_take_effect_play(&esp32, taken_id, sizeof(taken_id), NULL));
    assert(strcmp(taken_id, "mint_pulse") == 0);
}

static door_protocol_rx_result_t push_profile(
    door_protocol_t *esp32,
    uint32_t seq,
    const char *profile_id,
    const char *priority,
    uint64_t now_mono_ms
)
{
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];

    snprintf(
        frame,
        sizeof(frame),
        "{\"v\":1,\"seq\":%" PRIu32 ",\"t\":\"profile_update\",\"ack\":null,\"p\":{\"profile_id\":"
        "\"%s\",\"ttl_ms\":2500,\"priority\":\"%s\"}}\n",
        seq,
        profile_id,
        priority
    );
    return door_protocol_receive_from_pi(esp32, frame, strlen(frame), now_mono_ms, "pi-boot", ack, sizeof(ack));
}

static void test_high_priority_profile_holds_the_cache_against_a_normal_push(void)
{
    door_protocol_t esp32;

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(push_profile(&esp32, 1, "blue_wave", "high", 0) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 0), "blue_wave") == 0);

    /* A throttled normal refresh for someone else cannot take the light back. */
    assert(push_profile(&esp32, 2, "green_pulse", "normal", 100) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 100), "blue_wave") == 0);
    assert(esp32.stats.profile_updates_applied == 1);
    assert(esp32.stats.profile_updates_rejected == 1);

    /* A normal refresh of the same profile extends the expiry and keeps high. */
    assert(push_profile(&esp32, 3, "blue_wave", "normal", 2000) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_applied == 2);
    assert(esp32.cached_profile.expires_at_mono_ms == 4500);
    assert(strcmp(esp32.cached_profile.priority, "high") == 0);
    assert(push_profile(&esp32, 4, "green_pulse", "normal", 2100) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(esp32.stats.profile_updates_rejected == 2);

    /* A high push of a different profile (a new recognition) replaces it. */
    assert(push_profile(&esp32, 5, "sunrise", "high", 2200) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 2200), "sunrise") == 0);
    assert(esp32.stats.profile_updates_applied == 3);

    /* Once it has expired, a normal push of anything is applied. */
    assert(push_profile(&esp32, 6, "green_pulse", "normal", 4700) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 4700), "green_pulse") == 0);
    assert(strcmp(esp32.cached_profile.priority, "normal") == 0);
    assert(esp32.stats.profile_updates_applied == 4);

    /* Normal over normal replaces freely. */
    assert(push_profile(&esp32, 7, "rainbow", "normal", 4800) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 4800), "rainbow") == 0);
    assert(esp32.stats.profile_updates_rejected == 2);
}

static void test_sequence_numbers_wrap_to_one_like_the_pi(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    const char *frame =
        "{\"v\":1,\"seq\":1,\"t\":\"profile_clear\",\"ack\":null,\"p\":{\"reason\":\"test\"}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    esp32.next_seq = UINT32_MAX - 1U;
    assert(door_protocol_receive_from_pi(
        &esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_ACK_EMITTED);
    assert_contains(ack, "\"seq\":4294967295,");

    assert(door_protocol_start_hello(&esp32));
    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert_contains(tx, "\"seq\":1,");
    assert(esp32.next_seq == 1);
}

static void test_heartbeat_carries_the_controllers_counters(void)
{
    door_protocol_t esp32;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char heartbeat[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char tx[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    char press_id[DOOR_PROTOCOL_PRESS_ID_BYTES];
    const char *bad = "{\"v\":1,\"seq\":1,\"t\":\"nonsense\",\"ack\":null,\"p\":{}}\n";

    door_protocol_init(&esp32, "esp32-test", "host-test");
    assert(door_protocol_make_heartbeat(&esp32, 0, 0, heartbeat, sizeof(heartbeat)));
    assert_contains(heartbeat, "\"rx_errors\":0,\"tx_retries\":0,\"tx_dropped\":0}}");

    assert(door_protocol_receive_from_pi(
        &esp32, bad, strlen(bad), 0, "pi-boot", ack, sizeof(ack)
    ) == DOOR_PROTOCOL_RX_DROPPED);
    for (int index = 0; index <= DOOR_PROTOCOL_PENDING_DEPTH; index++) {
        snprintf(press_id, sizeof(press_id), "00000000-0000-4000-8000-%012d", index);
        (void)door_protocol_emit_button_event(&esp32, press_id, (uint64_t)index);
    }
    assert(door_protocol_next_tx(&esp32, 0, tx, sizeof(tx)));
    assert(door_protocol_next_tx(&esp32, 50, tx, sizeof(tx)));
    assert(door_protocol_next_tx(&esp32, 100, tx, sizeof(tx)));

    assert(door_protocol_make_heartbeat(&esp32, 100, 0, heartbeat, sizeof(heartbeat)));
    assert_contains(heartbeat, "\"rx_errors\":1,\"tx_retries\":2,\"tx_dropped\":1}}");
    assert(strlen(heartbeat) <= DOOR_PROTOCOL_MAX_FRAME_BYTES);
}

int main(void)
{
    test_boot_id_is_sixteen_hex_chars_and_differs_per_boot();
    test_pi_hello_from_a_new_boot_forgets_remembered_frames();
    test_uuid_v4_press_ids_use_random_version_and_variant_bits();
    test_profile_update_ack_and_duplicate_dedupe();
    test_ack_consumes_esp32_sequence_numbers();
    test_retransmit_three_times_at_fifty_ms();
    test_ack_clears_pending_retransmit();
    test_two_button_events_queue_behind_delayed_ack();
    test_profile_cache_ttl_expiry_uses_local_monotonic_time();
    test_fallback_starts_after_pi_heartbeat_loss();
    test_button_event_reports_cached_profile();
    test_malformed_wire_message_counts_rx_error();
    test_protocol_version_rejection_counts_rx_error();
    test_oversized_frame_counts_rx_error();
    test_peer_reboot_resets_dedupe_identity_for_frames_without_boot_id();
    test_pending_queue_overflow_counts_tx_dropped();
    test_seen_ring_evicts_after_sixteen_frames();
    test_effect_play_reaches_callback_once_and_is_collectable_without_one();
    test_pi_hello_after_peer_is_known_is_answered_with_a_hello();
    test_partial_frame_is_discarded_after_silence_and_on_hello();
    test_oversize_line_is_skipped_to_its_newline();
    test_key_lookup_ignores_matching_string_values();
    test_fallback_edge_fires_once_and_requeues_hello();
    test_press_followup_is_profile_then_fallback_then_nothing();
    test_unknown_effect_id_is_acked_but_counted_as_rejected();
    test_high_priority_profile_holds_the_cache_against_a_normal_push();
    test_sequence_numbers_wrap_to_one_like_the_pi();
    test_heartbeat_carries_the_controllers_counters();
    puts("wire protocol conformance passed");
    return 0;
}
