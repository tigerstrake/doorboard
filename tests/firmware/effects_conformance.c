#include "door_effects.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

/* The catalog in docs/hardware/effects-catalog.md, pinned here so the two
 * cannot drift: default tick count and the audio cue's tone sequence. */
typedef struct {
    door_effect_id_t id;
    const char *name;
    uint32_t total_ticks;
    uint32_t num_tones;
    uint32_t frequencies[DOOR_AUDIO_MAX_TONES];
    uint32_t durations[DOOR_AUDIO_MAX_TONES];
} catalog_row_t;

static const catalog_row_t CATALOG[] = {
    { DOOR_EFFECT_GENERIC_PRESS, "generic_press", 10, 1, { 880 }, { 80 } },
    { DOOR_EFFECT_FALLBACK, "fallback", 20, 2, { 220, 180 }, { 150, 150 } },
    { DOOR_EFFECT_BOOT, "boot", 30, 3, { 523, 659, 784 }, { 100, 100, 200 } },
    { DOOR_EFFECT_PRIVACY_MODE, "privacy_mode", 40, 2, { 587, 440 }, { 150, 200 } },
    { DOOR_EFFECT_ERROR_ADMIN, "error_admin", 20, 3, { 330, 0, 330 }, { 100, 50, 100 } },
    { DOOR_EFFECT_BLUE_WAVE, "blue_wave", 30, 4, { 440, 554, 659, 880 }, { 80, 80, 80, 120 } },
    { DOOR_EFFECT_GREEN_PULSE, "green_pulse", 20, 3, { 988, 0, 988 }, { 60, 40, 80 } },
    { DOOR_EFFECT_SUNRISE, "sunrise", 40, 4, { 349, 440, 523, 587 }, { 100, 100, 100, 200 } },
    { DOOR_EFFECT_MINT_PULSE, "mint_pulse", 20, 2, { 880, 1047 }, { 80, 120 } },
    { DOOR_EFFECT_RAINBOW, "rainbow", 50, 6, { 262, 330, 392, 523, 659, 784 }, { 60, 60, 60, 60, 60, 120 } },
    { DOOR_EFFECT_SPARKLE, "sparkle", 30, 3, { 1175, 1318, 1568 }, { 50, 50, 100 } },
};

#define CATALOG_ROWS (sizeof(CATALOG) / sizeof(CATALOG[0]))

static bool frame_is_black(const door_effect_frame_t *frame) {
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        if (frame->leds[i].r || frame->leds[i].g || frame->leds[i].b) {
            return false;
        }
    }
    return true;
}

static uint8_t frame_peak(const door_effect_frame_t *frame) {
    uint8_t peak = 0;
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        if (frame->leds[i].r > peak) peak = frame->leds[i].r;
        if (frame->leds[i].g > peak) peak = frame->leds[i].g;
        if (frame->leds[i].b > peak) peak = frame->leds[i].b;
    }
    return peak;
}

/* Runs an effect to completion: every tick before the last continues, at
 * least one frame lights something, and the final frame is black. Returns the
 * number of ticks it took. */
static uint32_t run_to_completion(door_effect_state_t *state, uint8_t *peak_out) {
    door_effect_frame_t frame;
    uint32_t ticks = 0;
    uint8_t peak = 0;
    bool continues;

    do {
        continues = door_effects_step(state, &frame);
        ticks++;
        if (frame_peak(&frame) > peak) {
            peak = frame_peak(&frame);
        }
        assert(ticks <= state->total_ticks);
        assert(continues == (ticks < state->total_ticks));
    } while (continues);
    assert(frame_is_black(&frame));
    assert(!door_effects_step(state, &frame));
    if (peak_out != NULL) {
        *peak_out = peak;
    }
    return ticks;
}

static void test_effect_name_mapping(void) {
    printf("Running test_effect_name_mapping...\n");
    assert(CATALOG_ROWS == DOOR_EFFECT_COUNT - 1);
    for (size_t row = 0; row < CATALOG_ROWS; row++) {
        assert(door_effect_from_name(CATALOG[row].name) == CATALOG[row].id);
        assert(strcmp(door_effect_to_name(CATALOG[row].id), CATALOG[row].name) == 0);
    }
    assert(door_effect_from_name("unknown_invalid_name") == DOOR_EFFECT_NONE);
    assert(door_effect_from_name(NULL) == DOOR_EFFECT_NONE);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_NONE), "none") == 0);
}

static void test_default_brightness_is_sixty_percent(void) {
    printf("Running test_default_brightness_is_sixty_percent...\n");
    door_effect_state_t state;
    door_effect_frame_t frame;

    assert(door_effects_brightness_pct() == DOOR_EFFECTS_DEFAULT_BRIGHTNESS_PCT);
    assert(DOOR_EFFECTS_DEFAULT_BRIGHTNESS_PCT == 60);
    door_effects_init_state(&state);
    door_effects_start(&state, DOOR_EFFECT_GENERIC_PRESS, NULL);
    assert(door_effects_step(&state, &frame));
    /* Full white at 60 %: (255 * 60 + 50) / 100 = 153. */
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        assert(frame.leds[i].r == 153);
        assert(frame.leds[i].g == 153);
        assert(frame.leds[i].b == 153);
    }

    door_effects_set_brightness_pct(0);
    assert(door_effects_brightness_pct() == 1);
    door_effects_set_brightness_pct(250);
    assert(door_effects_brightness_pct() == 100);
    door_effects_set_brightness_pct(25);
    door_effects_start(&state, DOOR_EFFECT_ERROR_ADMIN, NULL);
    assert(door_effects_step(&state, &frame));
    assert(frame.leds[0].r == 64); /* (255 * 25 + 50) / 100 */
    assert(frame.leds[0].g == 0);
}

static void test_generic_press_animation(void) {
    printf("Running test_generic_press_animation...\n");
    door_effect_state_t state;
    door_effect_frame_t frame;

    door_effects_init_state(&state);
    door_effects_start(&state, DOOR_EFFECT_GENERIC_PRESS, NULL);
    assert(state.effect_id == DOOR_EFFECT_GENERIC_PRESS);
    assert(state.total_ticks == 10);
    assert(state.elapsed_ticks == 0);

    // Step 0: Should be fully bright white (255, 255, 255)
    bool continues = door_effects_step(&state, &frame);
    assert(continues == true);
    assert(state.elapsed_ticks == 1);
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        assert(frame.leds[i].r == 255);
        assert(frame.leds[i].g == 255);
        assert(frame.leds[i].b == 255);
    }

    // Step to the end
    for (int step = 1; step < 9; step++) {
        continues = door_effects_step(&state, &frame);
        assert(continues == true);
        // Verify value is decreasing
        assert(frame.leds[0].r < 255);
    }

    // Final step
    continues = door_effects_step(&state, &frame);
    assert(continues == false); // Should finish on the last tick
    assert(state.elapsed_ticks == 10);
    // At/after completion, the frame should be cleared (black)
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        assert(frame.leds[i].r == 0);
        assert(frame.leds[i].g == 0);
        assert(frame.leds[i].b == 0);
    }
}

static void test_green_pulse_animation(void) {
    printf("Running test_green_pulse_animation...\n");
    door_effect_state_t state;
    door_effect_frame_t frame;

    door_effects_init_state(&state);
    door_effects_start(&state, DOOR_EFFECT_GREEN_PULSE, NULL);
    assert(state.total_ticks == 20);

    // We expect it to pulse green twice.
    // The intensity function is 0.5 - 0.5 * cos(angle), where angle goes 0..4*PI over 20 ticks.
    // At tick = 0: angle = 0, cos(0) = 1, intensity = 0.
    // At tick = 5: angle = PI, cos(PI) = -1, intensity = 1.0 (peak green = 255).
    // At tick = 10: angle = 2*PI, cos(2*PI) = 1, intensity = 0 (low green = 0).
    // At tick = 15: angle = 3*PI, cos(3*PI) = -1, intensity = 1.0 (peak green = 255).
    // At tick = 20: angle = 4*PI, cos(4*PI) = 1, intensity = 0.

    for (int step = 0; step < 20; step++) {
        bool continues = door_effects_step(&state, &frame);
        assert(continues == (step < 19));

        if (step == 0) {
            assert(frame.leds[0].g == 0);
        } else if (step == 5) {
            assert(frame.leds[0].g == 255);
        } else if (step == 10) {
            assert(frame.leds[0].g == 0);
        } else if (step == 15) {
            assert(frame.leds[0].g == 255);
        }

        // Red and Blue should always be 0
        for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
            assert(frame.leds[i].r == 0);
            assert(frame.leds[i].b == 0);
        }
    }
}

static void test_every_animation_runs_its_catalog_length_and_lights_up(void) {
    printf("Running test_every_animation_runs_its_catalog_length_and_lights_up...\n");
    for (size_t row = 0; row < CATALOG_ROWS; row++) {
        door_effect_state_t state;
        uint8_t peak = 0;

        door_effects_init_state(&state);
        door_effects_start(&state, CATALOG[row].id, NULL);
        assert(state.effect_id == CATALOG[row].id);
        assert(state.total_ticks == CATALOG[row].total_ticks);
        assert(run_to_completion(&state, &peak) == CATALOG[row].total_ticks);
        assert(peak > 0);

        /* The same effect reached through a profile_id. */
        door_effects_start(&state, DOOR_EFFECT_GENERIC_PRESS, CATALOG[row].name);
        assert(state.effect_id == CATALOG[row].id);
        assert(state.total_ticks == CATALOG[row].total_ticks);
    }

    door_effect_state_t none;
    door_effect_frame_t frame;
    door_effects_init_state(&none);
    door_effects_start(&none, DOOR_EFFECT_NONE, NULL);
    assert(none.total_ticks == 0);
    assert(!door_effects_step(&none, &frame));
    assert(frame_is_black(&frame));
    /* An unknown profile name falls back to the effect that was asked for. */
    door_effects_start(&none, DOOR_EFFECT_BLUE_WAVE, "nobody");
    assert(none.effect_id == DOOR_EFFECT_BLUE_WAVE);
}

static void test_boot_spins_then_fades(void) {
    printf("Running test_boot_spins_then_fades...\n");
    door_effect_state_t state;
    door_effect_frame_t frame;

    door_effects_init_state(&state);
    door_effects_start(&state, DOOR_EFFECT_BOOT, NULL);
    /* Spin phase: exactly one pixel at the lead colour (0, 192, 192). */
    assert(door_effects_step(&state, &frame));
    int leads = 0;
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        if (frame.leds[i].g == 192 && frame.leds[i].b == 192) leads++;
        assert(frame.leds[i].r == 0);
    }
    assert(leads == 1);
    /* Fade phase starts at tick 20 of 30 with every pixel equal and declining. */
    while (state.elapsed_ticks < 20) {
        assert(door_effects_step(&state, &frame));
    }
    assert(door_effects_step(&state, &frame));
    uint8_t first = frame.leds[0].g;
    assert(first == 192); /* the first fade tick: fade = 1.0 */
    for (int i = 1; i < DOORBOARD_NUM_LEDS; i++) {
        assert(frame.leds[i].g == first && frame.leds[i].b == first);
    }
    assert(door_effects_step(&state, &frame));
    assert(frame.leds[0].g < first); /* fade = 0.9 */
    assert(frame.leds[0].g > 150);
}

static void test_duration_rescales_the_animation(void) {
    printf("Running test_duration_rescales_the_animation...\n");
    door_effect_state_t state;
    uint8_t peak = 0;

    door_effects_init_state(&state);
    /* The DoorPad asks for 900 ms of generic_press; it used to get 300. */
    door_effects_start_timed(&state, DOOR_EFFECT_GENERIC_PRESS, NULL, 900);
    assert(state.total_ticks == 900 / DOOR_EFFECTS_TICK_MS);
    assert(run_to_completion(&state, &peak) == 30);
    assert(peak == 255);

    /* 0 keeps the effect's own length. */
    door_effects_start_timed(&state, DOOR_EFFECT_RAINBOW, NULL, 0);
    assert(state.total_ticks == 50);

    /* Shorter than a tick still renders one frame; longer than the cap is
     * clamped; the effect's phases follow the new length (boot's fade could
     * run negative before it was made proportional). */
    door_effects_start_timed(&state, DOOR_EFFECT_SPARKLE, NULL, 1);
    assert(state.total_ticks == 1);
    assert(run_to_completion(&state, &peak) == 1);
    door_effects_start_timed(&state, DOOR_EFFECT_BOOT, NULL, 60000);
    assert(state.total_ticks == DOOR_EFFECTS_MAX_DURATION_MS / DOOR_EFFECTS_TICK_MS);
    assert(run_to_completion(&state, &peak) == state.total_ticks);
    assert(peak == 192);
    for (size_t row = 0; row < CATALOG_ROWS; row++) {
        door_effects_start_timed(&state, CATALOG[row].id, NULL, 3000);
        assert(state.total_ticks == 100);
        assert(run_to_completion(&state, &peak) == 100);
        assert(peak > 0);
    }

    /* NONE ignores the duration. */
    door_effects_start_timed(&state, DOOR_EFFECT_NONE, NULL, 3000);
    assert(state.total_ticks == 0);
}

static void test_audio_cues(void) {
    printf("Running test_audio_cues...\n");
    assert(door_effects_get_audio_cue(DOOR_EFFECT_NONE)->num_tones == 0);
    for (size_t row = 0; row < CATALOG_ROWS; row++) {
        const door_audio_cue_t *cue = door_effects_get_audio_cue(CATALOG[row].id);
        assert(cue->num_tones == CATALOG[row].num_tones);
        assert(cue->num_tones <= DOOR_AUDIO_MAX_TONES);
        for (uint32_t tone = 0; tone < cue->num_tones; tone++) {
            assert(cue->tones[tone].frequency_hz == CATALOG[row].frequencies[tone]);
            assert(cue->tones[tone].duration_ms == CATALOG[row].durations[tone]);
        }
    }
    /* Out of range falls back to silence rather than reading past the table. */
    assert(door_effects_get_audio_cue((door_effect_id_t)DOOR_EFFECT_COUNT)->num_tones == 0);
    assert(door_effects_get_audio_cue((door_effect_id_t)999)->num_tones == 0);
}

int main(void) {
    test_effect_name_mapping();
    test_default_brightness_is_sixty_percent();
    /* The remaining tests assert catalog colours, which are listed at 100 %. */
    door_effects_set_brightness_pct(100);
    test_generic_press_animation();
    test_green_pulse_animation();
    test_every_animation_runs_its_catalog_length_and_lights_up();
    test_boot_spins_then_fades();
    test_duration_rescales_the_animation();
    test_audio_cues();
    printf("door effects library conformance passed\n");
    return 0;
}
