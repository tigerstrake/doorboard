#include "door_button.h"

#include <assert.h>
#include <stdio.h>

#define DEBOUNCE_MS 40

static void hold_high(door_button_debounce_t *debounce, uint64_t from_ms, uint64_t to_ms)
{
    for (uint64_t at = from_ms; at <= to_ms; at += 10) {
        door_button_debounce_sample(debounce, true, at);
    }
}

static void test_edge_while_still_pressed_is_one_press(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    assert(door_button_debounce_armed(&debounce));

    /* The audit's case: a press at 0, then a second falling edge at 45 ms with
     * the pin still low (an edge-only debounce accepted this as a new press). */
    assert(door_button_debounce_edge(&debounce, 0));
    assert(!door_button_debounce_armed(&debounce));
    door_button_debounce_sample(&debounce, false, 10);
    door_button_debounce_sample(&debounce, false, 40);
    assert(!door_button_debounce_edge(&debounce, 45));
    assert(!door_button_debounce_armed(&debounce));
    assert(debounce.last_press_mono_ms == 0);
}

static void test_rearms_only_after_the_pin_reads_high_for_the_interval(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    assert(door_button_debounce_edge(&debounce, 0));

    /* Held for 100 ms, then released: HIGH from 110. */
    door_button_debounce_sample(&debounce, false, 50);
    door_button_debounce_sample(&debounce, false, 100);
    hold_high(&debounce, 110, 140);
    assert(!door_button_debounce_armed(&debounce)); /* 30 ms high so far */
    door_button_debounce_sample(&debounce, true, 149);
    assert(!door_button_debounce_armed(&debounce)); /* 39 ms */
    door_button_debounce_sample(&debounce, true, 150);
    assert(door_button_debounce_armed(&debounce)); /* 40 ms */

    assert(door_button_debounce_edge(&debounce, 160));
    assert(debounce.last_press_mono_ms == 160);
}

static void test_release_bounce_restarts_the_release_timer(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    assert(door_button_debounce_edge(&debounce, 0));

    /* HIGH from 110, a LOW blip at 130 (contact bounce), HIGH again from 140. */
    hold_high(&debounce, 110, 120);
    door_button_debounce_sample(&debounce, false, 130);
    hold_high(&debounce, 140, 170);
    assert(!door_button_debounce_armed(&debounce)); /* 30 ms since 140 */
    door_button_debounce_sample(&debounce, true, 180);
    assert(door_button_debounce_armed(&debounce));
}

static void test_isr_edge_during_release_bounce_also_restarts_the_timer(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    assert(door_button_debounce_edge(&debounce, 0));

    hold_high(&debounce, 110, 130);
    /* The bounce was fast enough to raise a falling-edge interrupt rather than
     * show up in a sample: it still means the pin went low. */
    assert(!door_button_debounce_edge(&debounce, 135));
    hold_high(&debounce, 140, 170);
    assert(!door_button_debounce_armed(&debounce));
    door_button_debounce_sample(&debounce, true, 180);
    assert(door_button_debounce_armed(&debounce));
}

static void test_samples_never_disarm(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    /* A LOW reading while armed is a press whose edge is already queued; the
     * sample must not eat it. */
    door_button_debounce_sample(&debounce, false, 5);
    door_button_debounce_sample(&debounce, true, 15);
    assert(door_button_debounce_armed(&debounce));
    assert(door_button_debounce_edge(&debounce, 20));
}

static void test_two_distinct_presses_forty_ms_apart_both_count(void)
{
    door_button_debounce_t debounce;

    door_button_debounce_init(&debounce, DEBOUNCE_MS);
    assert(door_button_debounce_edge(&debounce, 0));
    hold_high(&debounce, 10, 50);
    assert(door_button_debounce_armed(&debounce));
    assert(door_button_debounce_edge(&debounce, 55));
    hold_high(&debounce, 65, 105);
    assert(door_button_debounce_edge(&debounce, 110));
}

int main(void)
{
    test_edge_while_still_pressed_is_one_press();
    test_rearms_only_after_the_pin_reads_high_for_the_interval();
    test_release_bounce_restarts_the_release_timer();
    test_isr_edge_during_release_bounce_also_restarts_the_timer();
    test_samples_never_disarm();
    test_two_distinct_presses_forty_ms_apart_both_count();
    puts("button debounce conformance passed");
    return 0;
}
