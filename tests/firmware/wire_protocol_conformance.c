#include "door_protocol.h"

#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

static void assert_contains(const char *haystack, const char *needle)
{
    assert(strstr(haystack, needle) != NULL);
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
    assert_contains(ack, "\"ack\":1");

    assert(
        door_protocol_receive_from_pi(&esp32, frame, strlen(frame), 0, "pi-boot", ack, sizeof(ack)) ==
        DOOR_PROTOCOL_RX_ACK_EMITTED
    );
    assert(esp32.stats.profile_updates_applied == 1);
    assert(door_protocol_has_cached_profile(&esp32, 0));
    assert(strcmp(door_protocol_cached_profile_id(&esp32, 0), "blue_wave") == 0);
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

int main(void)
{
    test_profile_update_ack_and_duplicate_dedupe();
    test_retransmit_three_times_at_fifty_ms();
    test_ack_clears_pending_retransmit();
    test_profile_cache_ttl_expiry_uses_local_monotonic_time();
    test_fallback_starts_after_pi_heartbeat_loss();
    test_button_event_reports_cached_profile();
    test_malformed_wire_message_counts_rx_error();
    test_oversized_frame_counts_rx_error();
    puts("wire protocol conformance passed");
    return 0;
}
