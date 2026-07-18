#include "door_protocol.h"

#include <assert.h>
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

int main(void)
{
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
    puts("wire protocol conformance passed");
    return 0;
}
