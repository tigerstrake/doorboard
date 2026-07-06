#ifndef DOOR_EFFECTS_H
#define DOOR_EFFECTS_H

#include <stdint.h>
#include <stdbool.h>

#define DOORBOARD_NUM_LEDS 16

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} door_rgb_t;

typedef struct {
    door_rgb_t leds[DOORBOARD_NUM_LEDS];
} door_effect_frame_t;

typedef enum {
    DOOR_EFFECT_NONE = 0,
    DOOR_EFFECT_GENERIC_PRESS,
    DOOR_EFFECT_FALLBACK,
    DOOR_EFFECT_BOOT,
    DOOR_EFFECT_PRIVACY_MODE,
    DOOR_EFFECT_ERROR_ADMIN,
    DOOR_EFFECT_BLUE_WAVE,
    DOOR_EFFECT_GREEN_PULSE,
    DOOR_EFFECT_SUNRISE,
    DOOR_EFFECT_MINT_PULSE,
    DOOR_EFFECT_RAINBOW,
    DOOR_EFFECT_SPARKLE
} door_effect_id_t;

typedef struct {
    door_effect_id_t effect_id;
    uint32_t elapsed_ticks;
    uint32_t total_ticks;
    uint32_t seed; // Seed for pseudo-random effects like sparkle
} door_effect_state_t;

typedef struct {
    uint32_t frequency_hz;
    uint32_t duration_ms;
} door_audio_tone_t;

#define DOOR_AUDIO_MAX_TONES 16

typedef struct {
    door_audio_tone_t tones[DOOR_AUDIO_MAX_TONES];
    uint32_t num_tones;
} door_audio_cue_t;

void door_effects_init_state(door_effect_state_t *state);
door_effect_id_t door_effect_from_name(const char *name);
const char *door_effect_to_name(door_effect_id_t effect_id);

void door_effects_start(
    door_effect_state_t *state,
    door_effect_id_t effect_id,
    const char *profile_id
);

// Updates state by one tick. Fills frame. Returns true if the animation continues, false if completed.
bool door_effects_step(
    door_effect_state_t *state,
    door_effect_frame_t *frame
);

const door_audio_cue_t *door_effects_get_audio_cue(door_effect_id_t effect_id);

#endif // DOOR_EFFECTS_H
