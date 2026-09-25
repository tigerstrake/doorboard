#include "door_button.h"

#include <string.h>

void door_button_debounce_init(door_button_debounce_t *debounce, uint32_t debounce_ms)
{
    memset(debounce, 0, sizeof(*debounce));
    debounce->debounce_ms = debounce_ms;
    debounce->armed = true;
}

bool door_button_debounce_edge(door_button_debounce_t *debounce, uint64_t now_mono_ms)
{
    if (!debounce->armed) {
        /* The pin just went low again (a release bounce, or a second press
         * before the first was fully released): the release must be observed
         * afresh before anything counts. */
        debounce->released_seen = false;
        return false;
    }
    debounce->armed = false;
    debounce->released_seen = false;
    debounce->last_press_mono_ms = now_mono_ms;
    return true;
}

void door_button_debounce_sample(
    door_button_debounce_t *debounce,
    bool level_high,
    uint64_t now_mono_ms
)
{
    if (debounce->armed) {
        return;
    }
    if (!level_high) {
        debounce->released_seen = false;
        return;
    }
    if (!debounce->released_seen) {
        debounce->released_seen = true;
        debounce->released_since_mono_ms = now_mono_ms;
        return;
    }
    if (now_mono_ms - debounce->released_since_mono_ms >= debounce->debounce_ms) {
        debounce->armed = true;
        debounce->released_seen = false;
    }
}

bool door_button_debounce_armed(const door_button_debounce_t *debounce)
{
    return debounce->armed;
}
