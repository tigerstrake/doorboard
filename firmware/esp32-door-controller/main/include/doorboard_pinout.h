#ifndef DOORBOARD_PINOUT_H
#define DOORBOARD_PINOUT_H

/*
 * Doorboard ESP32-S3 pinout — the single source of truth for the harness.
 *
 * These began as bench defaults for T-101. M1 hardware bring-up may revise them,
 * but all firmware code must use this header rather than scattered GPIO literals,
 * and docs/hardware/wiring.md must agree with it.
 *
 * Electrical behaviour these values imply (see wiring.md for the harness):
 *   - BUTTON is configured input, internal pull-up, negative-edge interrupt, so
 *     the switch goes between the pin and GND. Active LOW. No external pull-up
 *     needed; the RC debounce in wiring.md is still wanted for contact bounce.
 *   - LED_DATA drives WS2812 (GRB) via RMT, non-inverted, 3.3 V logic.
 *   - AUDIO_* are I2S standard mode, MSB slot, 16-bit mono. MCLK and DIN unused.
 */

#define DOORBOARD_PIN_BUTTON_GPIO 4
#define DOORBOARD_PIN_LED_DATA_GPIO 18
#define DOORBOARD_PIN_AUDIO_BCLK_GPIO 16
#define DOORBOARD_PIN_AUDIO_WS_GPIO 17
#define DOORBOARD_PIN_AUDIO_DOUT_GPIO 15
#define DOORBOARD_PIN_UART_TX_GPIO 43
#define DOORBOARD_PIN_UART_RX_GPIO 44

/*
 * Piezo knock input.
 *
 * NOT YET READ BY ANY CODE — the sensors task is a stub (see
 * CONFIG_DOORBOARD_ENABLE_KNOCK_DETECTION), so wiring a piezo here does nothing
 * until knock detection is implemented.
 *
 * The channel number alone used to be ambiguous and, read as ADC1, actively
 * wrong: on the ESP32-S3, ADC1 channel 3 *is* GPIO 4 — the bell button. Anyone
 * implementing knock detection against ADC1 would have collided with the button
 * ISR, and the symptom (phantom or dead button presses) would have looked like a
 * wiring fault rather than a pin clash. Both the unit and the GPIO are now
 * explicit, and the assertion below makes a future collision a build error rather
 * than a soldering-iron problem.
 *
 * ADC1 channel 6 = GPIO 7 on the S3: general-purpose, not a strapping pin, not
 * used by USB, flash, or PSRAM.
 */
#define DOORBOARD_PIN_PIEZO_ADC_UNIT 1
#define DOORBOARD_PIN_PIEZO_ADC_CHANNEL 6
#define DOORBOARD_PIN_PIEZO_GPIO 7

_Static_assert(
    DOORBOARD_PIN_PIEZO_GPIO != DOORBOARD_PIN_BUTTON_GPIO,
    "piezo and button cannot share a GPIO: the button owns an edge-triggered ISR"
);

#define DOORBOARD_UART_PORT_NUM 1
#define DOORBOARD_UART_BAUD_RATE 115200
#define DOORBOARD_BUTTON_DEBOUNCE_MS 40
#define DOORBOARD_EFFECT_QUEUE_DEPTH 8
#define DOORBOARD_LINK_QUEUE_DEPTH 8
#define DOORBOARD_INPUT_QUEUE_DEPTH 8

#endif
