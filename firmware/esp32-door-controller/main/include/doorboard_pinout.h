#ifndef DOORBOARD_PINOUT_H
#define DOORBOARD_PINOUT_H

/*
 * Doorboard ESP32-S3 development pinout.
 *
 * These assignments are bench defaults for T-101. The M1 hardware bring-up may
 * revise them when the final ESP32-S3 board is selected, but all firmware code
 * must use this header rather than scattered GPIO literals.
 */

#define DOORBOARD_PIN_BUTTON_GPIO 4
#define DOORBOARD_PIN_LED_DATA_GPIO 18
#define DOORBOARD_PIN_AUDIO_BCLK_GPIO 16
#define DOORBOARD_PIN_AUDIO_WS_GPIO 17
#define DOORBOARD_PIN_AUDIO_DOUT_GPIO 15
#define DOORBOARD_PIN_UART_TX_GPIO 43
#define DOORBOARD_PIN_UART_RX_GPIO 44
#define DOORBOARD_PIN_PIEZO_ADC_CHANNEL 3

#define DOORBOARD_UART_PORT_NUM 1
#define DOORBOARD_UART_BAUD_RATE 115200
#define DOORBOARD_BUTTON_DEBOUNCE_MS 40
#define DOORBOARD_EFFECT_QUEUE_DEPTH 8
#define DOORBOARD_LINK_QUEUE_DEPTH 8
#define DOORBOARD_INPUT_QUEUE_DEPTH 8

#endif
