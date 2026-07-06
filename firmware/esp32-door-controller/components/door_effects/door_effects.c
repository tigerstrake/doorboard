#include "door_effects.h"
#include <string.h>
#include <math.h>
#include <stdlib.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static uint32_t pseudo_rand(uint32_t *seed) {
    *seed = (*seed * 1103515245U + 12345U) & 0x7fffffffU;
    return *seed;
}

void door_effects_init_state(door_effect_state_t *state) {
    memset(state, 0, sizeof(*state));
    state->effect_id = DOOR_EFFECT_NONE;
}

door_effect_id_t door_effect_from_name(const char *name) {
    if (name == NULL) return DOOR_EFFECT_NONE;
    if (strcmp(name, "generic_press") == 0) return DOOR_EFFECT_GENERIC_PRESS;
    if (strcmp(name, "fallback") == 0) return DOOR_EFFECT_FALLBACK;
    if (strcmp(name, "boot") == 0) return DOOR_EFFECT_BOOT;
    if (strcmp(name, "privacy_mode") == 0) return DOOR_EFFECT_PRIVACY_MODE;
    if (strcmp(name, "error_admin") == 0) return DOOR_EFFECT_ERROR_ADMIN;
    if (strcmp(name, "blue_wave") == 0) return DOOR_EFFECT_BLUE_WAVE;
    if (strcmp(name, "green_pulse") == 0) return DOOR_EFFECT_GREEN_PULSE;
    if (strcmp(name, "sunrise") == 0) return DOOR_EFFECT_SUNRISE;
    if (strcmp(name, "mint_pulse") == 0) return DOOR_EFFECT_MINT_PULSE;
    if (strcmp(name, "rainbow") == 0) return DOOR_EFFECT_RAINBOW;
    if (strcmp(name, "sparkle") == 0) return DOOR_EFFECT_SPARKLE;
    return DOOR_EFFECT_NONE;
}

const char *door_effect_to_name(door_effect_id_t effect_id) {
    switch (effect_id) {
        case DOOR_EFFECT_GENERIC_PRESS: return "generic_press";
        case DOOR_EFFECT_FALLBACK: return "fallback";
        case DOOR_EFFECT_BOOT: return "boot";
        case DOOR_EFFECT_PRIVACY_MODE: return "privacy_mode";
        case DOOR_EFFECT_ERROR_ADMIN: return "error_admin";
        case DOOR_EFFECT_BLUE_WAVE: return "blue_wave";
        case DOOR_EFFECT_GREEN_PULSE: return "green_pulse";
        case DOOR_EFFECT_SUNRISE: return "sunrise";
        case DOOR_EFFECT_MINT_PULSE: return "mint_pulse";
        case DOOR_EFFECT_RAINBOW: return "rainbow";
        case DOOR_EFFECT_SPARKLE: return "sparkle";
        default: return "none";
    }
}

void door_effects_start(
    door_effect_state_t *state,
    door_effect_id_t effect_id,
    const char *profile_id
) {
    state->effect_id = effect_id;
    state->elapsed_ticks = 0;
    state->seed = 42;
    
    if (profile_id != NULL && profile_id[0] != '\0') {
        door_effect_id_t resolved = door_effect_from_name(profile_id);
        if (resolved != DOOR_EFFECT_NONE) {
            state->effect_id = resolved;
        }
    }
    
    switch (state->effect_id) {
        case DOOR_EFFECT_GENERIC_PRESS:
            state->total_ticks = 10; // 300 ms
            break;
        case DOOR_EFFECT_FALLBACK:
            state->total_ticks = 20; // 600 ms
            break;
        case DOOR_EFFECT_BOOT:
            state->total_ticks = 30; // 900 ms
            break;
        case DOOR_EFFECT_PRIVACY_MODE:
            state->total_ticks = 40; // 1200 ms
            break;
        case DOOR_EFFECT_ERROR_ADMIN:
            state->total_ticks = 20; // 600 ms
            break;
        case DOOR_EFFECT_BLUE_WAVE:
            state->total_ticks = 30; // 900 ms
            break;
        case DOOR_EFFECT_GREEN_PULSE:
            state->total_ticks = 20; // 600 ms
            break;
        case DOOR_EFFECT_SUNRISE:
            state->total_ticks = 40; // 1200 ms
            break;
        case DOOR_EFFECT_MINT_PULSE:
            state->total_ticks = 20; // 600 ms
            break;
        case DOOR_EFFECT_RAINBOW:
            state->total_ticks = 50; // 1500 ms
            break;
        case DOOR_EFFECT_SPARKLE:
            state->total_ticks = 30; // 900 ms
            break;
        default:
            state->total_ticks = 0;
            state->effect_id = DOOR_EFFECT_NONE;
            break;
    }
}

bool door_effects_step(
    door_effect_state_t *state,
    door_effect_frame_t *frame
) {
    if (state->effect_id == DOOR_EFFECT_NONE || state->elapsed_ticks >= state->total_ticks) {
        memset(frame, 0, sizeof(*frame));
        return false;
    }

    memset(frame, 0, sizeof(*frame));

    switch (state->effect_id) {
        case DOOR_EFFECT_GENERIC_PRESS: {
            float progress = (float)state->elapsed_ticks / state->total_ticks;
            uint8_t val = (uint8_t)((1.0f - progress) * 255.0f);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = val;
                frame->leds[i].g = val;
                frame->leds[i].b = val;
            }
            break;
        }
        case DOOR_EFFECT_FALLBACK: {
            float angle = ((float)state->elapsed_ticks / state->total_ticks) * (float)M_PI;
            float sin_val = sinf(angle);
            uint8_t r = (uint8_t)(sin_val * 128.0f);
            uint8_t g = (uint8_t)(sin_val * 32.0f);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = r;
                frame->leds[i].g = g;
                frame->leds[i].b = 0;
            }
            break;
        }
        case DOOR_EFFECT_BOOT: {
            if (state->elapsed_ticks < 20) {
                int lead = (state->elapsed_ticks * 2) % DOORBOARD_NUM_LEDS;
                for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                    int diff = (lead - i + DOORBOARD_NUM_LEDS) % DOORBOARD_NUM_LEDS;
                    if (diff == 0) {
                        frame->leds[i].r = 0;
                        frame->leds[i].g = 192;
                        frame->leds[i].b = 192;
                    } else if (diff < 4) {
                        float factor = 1.0f - (float)diff / 4.0f;
                        frame->leds[i].r = 0;
                        frame->leds[i].g = (uint8_t)(192.0f * factor);
                        frame->leds[i].b = (uint8_t)(192.0f * factor);
                    }
                }
            } else {
                float fade = 1.0f - (float)(state->elapsed_ticks - 20) / 10.0f;
                uint8_t val = (uint8_t)(192.0f * fade);
                for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                    frame->leds[i].r = 0;
                    frame->leds[i].g = val;
                    frame->leds[i].b = val;
                }
            }
            break;
        }
        case DOOR_EFFECT_PRIVACY_MODE: {
            float angle = ((float)state->elapsed_ticks / state->total_ticks) * 2.0f * (float)M_PI;
            float intensity = 0.5f + 0.5f * cosf(angle - (float)M_PI);
            uint8_t val = (uint8_t)(intensity * 128.0f);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = val;
                frame->leds[i].g = 0;
                frame->leds[i].b = val;
            }
            break;
        }
        case DOOR_EFFECT_ERROR_ADMIN: {
            bool on = (state->elapsed_ticks / 5) % 2 == 0;
            uint8_t r = on ? 255 : 0;
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = r;
                frame->leds[i].g = 0;
                frame->leds[i].b = 0;
            }
            break;
        }
        case DOOR_EFFECT_BLUE_WAVE: {
            float lead = (float)state->elapsed_ticks * ((float)DOORBOARD_NUM_LEDS / (float)state->total_ticks);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                float dist = fabsf((float)i - lead);
                if (dist > (float)DOORBOARD_NUM_LEDS / 2.0f) {
                    dist = (float)DOORBOARD_NUM_LEDS - dist;
                }
                float val = 0.0f;
                if (dist < 4.0f) {
                    val = 1.0f - dist / 4.0f;
                }
                frame->leds[i].r = 0;
                frame->leds[i].g = 0;
                frame->leds[i].b = (uint8_t)(val * 255.0f);
            }
            break;
        }
        case DOOR_EFFECT_GREEN_PULSE: {
            float angle = ((float)state->elapsed_ticks / state->total_ticks) * 4.0f * (float)M_PI;
            float intensity = 0.5f - 0.5f * cosf(angle);
            uint8_t g = (uint8_t)(intensity * 255.0f);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = 0;
                frame->leds[i].g = g;
                frame->leds[i].b = 0;
            }
            break;
        }
        case DOOR_EFFECT_SUNRISE: {
            float progress = (float)state->elapsed_ticks / state->total_ticks;
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                int dist = abs(i - 8);
                if (dist > 8) dist = 16 - dist;
                float start_time = (float)dist / 8.0f * 0.5f;
                if (progress > start_time) {
                    float age = (progress - start_time) / (1.0f - start_time);
                    if (age < 0.3f) {
                        frame->leds[i].r = (uint8_t)(age / 0.3f * 255.0f);
                        frame->leds[i].g = 0;
                        frame->leds[i].b = 0;
                    } else if (age < 0.6f) {
                        float norm = (age - 0.3f) / 0.3f;
                        frame->leds[i].r = 255;
                        frame->leds[i].g = (uint8_t)(norm * 200.0f);
                        frame->leds[i].b = 0;
                    } else {
                        float norm = 1.0f - (age - 0.6f) / 0.4f;
                        if (norm < 0.0f) norm = 0.0f;
                        frame->leds[i].r = (uint8_t)(norm * 255.0f);
                        frame->leds[i].g = (uint8_t)(norm * 200.0f);
                        frame->leds[i].b = 0;
                    }
                }
            }
            break;
        }
        case DOOR_EFFECT_MINT_PULSE: {
            float angle = ((float)state->elapsed_ticks / state->total_ticks) * 4.0f * (float)M_PI;
            float intensity = 0.5f - 0.5f * cosf(angle);
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                frame->leds[i].r = (uint8_t)(intensity * 30.0f);
                frame->leds[i].g = (uint8_t)(intensity * 255.0f);
                frame->leds[i].b = (uint8_t)(intensity * 150.0f);
            }
            break;
        }
        case DOOR_EFFECT_RAINBOW: {
            for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                float hue = (float)(i + state->elapsed_ticks) / 16.0f * 6.0f;
                int section = (int)hue;
                float fract = hue - (float)section;
                uint8_t p = 0;
                uint8_t q = (uint8_t)((1.0f - fract) * 255.0f);
                uint8_t t = (uint8_t)(fract * 255.0f);
                
                switch (section % 6) {
                    case 0:
                        frame->leds[i].r = 255;
                        frame->leds[i].g = t;
                        frame->leds[i].b = p;
                        break;
                    case 1:
                        frame->leds[i].r = q;
                        frame->leds[i].g = 255;
                        frame->leds[i].b = p;
                        break;
                    case 2:
                        frame->leds[i].r = p;
                        frame->leds[i].g = 255;
                        frame->leds[i].b = t;
                        break;
                    case 3:
                        frame->leds[i].r = p;
                        frame->leds[i].g = q;
                        frame->leds[i].b = 255;
                        break;
                    case 4:
                        frame->leds[i].r = t;
                        frame->leds[i].g = p;
                        frame->leds[i].b = 255;
                        break;
                    default:
                        frame->leds[i].r = 255;
                        frame->leds[i].g = p;
                        frame->leds[i].b = q;
                        break;
                }
            }
            break;
        }
        case DOOR_EFFECT_SPARKLE: {
            uint32_t seed_run = 42;
            for (uint32_t step = 0; step <= state->elapsed_ticks; step++) {
                for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
                    frame->leds[i].r = (uint8_t)(frame->leds[i].r * 0.7f);
                    frame->leds[i].g = (uint8_t)(frame->leds[i].g * 0.7f);
                    frame->leds[i].b = (uint8_t)(frame->leds[i].b * 0.7f);
                }
                if (step < 25) {
                    int p1 = pseudo_rand(&seed_run) % DOORBOARD_NUM_LEDS;
                    int p2 = pseudo_rand(&seed_run) % DOORBOARD_NUM_LEDS;
                    frame->leds[p1].r = 255;
                    frame->leds[p1].g = 220;
                    frame->leds[p1].b = 120;
                    frame->leds[p2].r = 255;
                    frame->leds[p2].g = 220;
                    frame->leds[p2].b = 120;
                }
            }
            break;
        }
        default:
            break;
    }

    state->elapsed_ticks++;
    bool continues = state->elapsed_ticks < state->total_ticks;
    if (!continues) {
        memset(frame, 0, sizeof(*frame));
    }
    return continues;
}

// Static definition of audio cues as tone sequences to minimize flash size.
static const door_audio_cue_t s_audio_cues[] = {
    [DOOR_EFFECT_NONE] = { .num_tones = 0 },
    [DOOR_EFFECT_GENERIC_PRESS] = {
        .num_tones = 1,
        .tones = {
            { 880, 80 }
        }
    },
    [DOOR_EFFECT_FALLBACK] = {
        .num_tones = 2,
        .tones = {
            { 220, 150 },
            { 180, 150 }
        }
    },
    [DOOR_EFFECT_BOOT] = {
        .num_tones = 3,
        .tones = {
            { 523, 100 },
            { 659, 100 },
            { 784, 200 }
        }
    },
    [DOOR_EFFECT_PRIVACY_MODE] = {
        .num_tones = 2,
        .tones = {
            { 587, 150 },
            { 440, 200 }
        }
    },
    [DOOR_EFFECT_ERROR_ADMIN] = {
        .num_tones = 3,
        .tones = {
            { 330, 100 },
            { 0, 50 },
            { 330, 100 }
        }
    },
    [DOOR_EFFECT_BLUE_WAVE] = {
        .num_tones = 4,
        .tones = {
            { 440, 80 },
            { 554, 80 },
            { 659, 80 },
            { 880, 120 }
        }
    },
    [DOOR_EFFECT_GREEN_PULSE] = {
        .num_tones = 3,
        .tones = {
            { 988, 60 },
            { 0, 40 },
            { 988, 80 }
        }
    },
    [DOOR_EFFECT_SUNRISE] = {
        .num_tones = 4,
        .tones = {
            { 349, 100 },
            { 440, 100 },
            { 523, 100 },
            { 587, 200 }
        }
    },
    [DOOR_EFFECT_MINT_PULSE] = {
        .num_tones = 2,
        .tones = {
            { 880, 80 },
            { 1047, 120 }
        }
    },
    [DOOR_EFFECT_RAINBOW] = {
        .num_tones = 6,
        .tones = {
            { 262, 60 },
            { 330, 60 },
            { 392, 60 },
            { 523, 60 },
            { 659, 60 },
            { 784, 120 }
        }
    },
    [DOOR_EFFECT_SPARKLE] = {
        .num_tones = 3,
        .tones = {
            { 1175, 50 },
            { 1318, 50 },
            { 1568, 100 }
        }
    }
};

const door_audio_cue_t *door_effects_get_audio_cue(door_effect_id_t effect_id) {
    if (effect_id >= sizeof(s_audio_cues)/sizeof(s_audio_cues[0])) {
        return &s_audio_cues[DOOR_EFFECT_NONE];
    }
    return &s_audio_cues[effect_id];
}
