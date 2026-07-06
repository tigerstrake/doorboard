#include "door_effects.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void test_effect_name_mapping(void) {
    printf("Running test_effect_name_mapping...\n");
    assert(door_effect_from_name("generic_press") == DOOR_EFFECT_GENERIC_PRESS);
    assert(door_effect_from_name("fallback") == DOOR_EFFECT_FALLBACK);
    assert(door_effect_from_name("boot") == DOOR_EFFECT_BOOT);
    assert(door_effect_from_name("privacy_mode") == DOOR_EFFECT_PRIVACY_MODE);
    assert(door_effect_from_name("error_admin") == DOOR_EFFECT_ERROR_ADMIN);
    assert(door_effect_from_name("blue_wave") == DOOR_EFFECT_BLUE_WAVE);
    assert(door_effect_from_name("green_pulse") == DOOR_EFFECT_GREEN_PULSE);
    assert(door_effect_from_name("sunrise") == DOOR_EFFECT_SUNRISE);
    assert(door_effect_from_name("mint_pulse") == DOOR_EFFECT_MINT_PULSE);
    assert(door_effect_from_name("rainbow") == DOOR_EFFECT_RAINBOW);
    assert(door_effect_from_name("sparkle") == DOOR_EFFECT_SPARKLE);
    assert(door_effect_from_name("unknown_invalid_name") == DOOR_EFFECT_NONE);
    assert(door_effect_from_name(NULL) == DOOR_EFFECT_NONE);

    assert(strcmp(door_effect_to_name(DOOR_EFFECT_GENERIC_PRESS), "generic_press") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_FALLBACK), "fallback") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_BOOT), "boot") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_PRIVACY_MODE), "privacy_mode") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_ERROR_ADMIN), "error_admin") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_BLUE_WAVE), "blue_wave") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_GREEN_PULSE), "green_pulse") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_SUNRISE), "sunrise") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_MINT_PULSE), "mint_pulse") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_RAINBOW), "rainbow") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_SPARKLE), "sparkle") == 0);
    assert(strcmp(door_effect_to_name(DOOR_EFFECT_NONE), "none") == 0);
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

static void test_audio_cues(void) {
    printf("Running test_audio_cues...\n");
    // Verify that boot has 3 tones
    const door_audio_cue_t *boot_cue = door_effects_get_audio_cue(DOOR_EFFECT_BOOT);
    assert(boot_cue->num_tones == 3);
    assert(boot_cue->tones[0].frequency_hz == 523);
    assert(boot_cue->tones[1].frequency_hz == 659);
    assert(boot_cue->tones[2].frequency_hz == 784);

    // Verify fallback has 2 tones
    const door_audio_cue_t *fallback_cue = door_effects_get_audio_cue(DOOR_EFFECT_FALLBACK);
    assert(fallback_cue->num_tones == 2);
    assert(fallback_cue->tones[0].frequency_hz == 220);
    assert(fallback_cue->tones[1].frequency_hz == 180);

    // Verify error_admin has 3 tones (beep, silence, beep)
    const door_audio_cue_t *error_cue = door_effects_get_audio_cue(DOOR_EFFECT_ERROR_ADMIN);
    assert(error_cue->num_tones == 3);
    assert(error_cue->tones[0].frequency_hz == 330);
    assert(error_cue->tones[1].frequency_hz == 0); // silence
    assert(error_cue->tones[2].frequency_hz == 330);
}

int main(void) {
    test_effect_name_mapping();
    test_generic_press_animation();
    test_green_pulse_animation();
    test_audio_cues();
    printf("door effects library conformance passed\n");
    return 0;
}
