#ifndef DOOR_BUTTON_H
#define DOOR_BUTTON_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Bell button debounce with a level re-check.
 *
 * The button is active-LOW behind an internal pull-up and an RC filter; the
 * ISR fires on the falling edge. Edge-only debounce ("ignore edges within
 * 40 ms of the last accepted one") lets a release bounce a millisecond long,
 * arriving after the human has held the button for 50-200 ms, register as a
 * second press: second chime, second flash, second button_event.
 *
 * This state machine accepts one falling edge and then stays disarmed until
 * the pin has read HIGH continuously for the debounce interval, which is only
 * true once the release -- bounce included -- is over. Samples never disarm:
 * a LOW sample while armed means a press whose edge is already on its way.
 *
 * Pure C, no RTOS or driver headers: the task feeds it ISR edge timestamps
 * and periodic gpio_get_level() readings, and host tests feed it scripts.
 */
typedef struct {
    uint32_t debounce_ms;
    bool armed;
    bool released_seen;
    uint64_t released_since_mono_ms;
    uint64_t last_press_mono_ms;
} door_button_debounce_t;

void door_button_debounce_init(door_button_debounce_t *debounce, uint32_t debounce_ms);

/* A falling edge observed by the ISR at now_mono_ms. True when it counts as a
 * press; false while disarmed (the edge also restarts the release timer, since
 * a falling edge means the pin was low). */
bool door_button_debounce_edge(door_button_debounce_t *debounce, uint64_t now_mono_ms);

/* A level reading (level_high: the pin reads released) at now_mono_ms. Re-arms
 * once the pin has read HIGH for at least debounce_ms without a LOW in between. */
void door_button_debounce_sample(
    door_button_debounce_t *debounce,
    bool level_high,
    uint64_t now_mono_ms
);

/* True when the next falling edge will be accepted (no sampling needed). */
bool door_button_debounce_armed(const door_button_debounce_t *debounce);

#ifdef __cplusplus
}
#endif

#endif
