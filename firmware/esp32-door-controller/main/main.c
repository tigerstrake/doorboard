#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

#include "door_protocol.h"
#include "door_effects.h"
#include "doorboard_pinout.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "driver/i2s_std.h"
#include "led_strip.h"
#include "esp_check.h"
#include "esp_intr_alloc.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define AUDIO_SAMPLE_RATE 22050
#define AUDIO_BUFFER_SIZE 256

typedef struct {
    uint64_t observed_at_mono_ms;
} button_isr_event_t;

typedef struct {
    door_effect_id_t effect_id;
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
} effect_command_t;

typedef struct {
    door_effect_id_t effect_id;
} audio_command_t;

typedef struct {
    char press_id[DOOR_PROTOCOL_PRESS_ID_BYTES];
    uint64_t pressed_at_mono_ms;
} link_button_event_t;

static const char *TAG = "doorboard-esp32";
static QueueHandle_t s_input_queue;
static QueueHandle_t s_effect_queue;
static QueueHandle_t s_link_queue;
static QueueHandle_t s_audio_queue;
static door_protocol_t s_protocol;
static portMUX_TYPE s_protocol_mux = portMUX_INITIALIZER_UNLOCKED;

static led_strip_handle_t s_led_strip = NULL;
static i2s_chan_handle_t s_tx_chan = NULL;

static uint64_t mono_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

static void IRAM_ATTR button_isr_handler(void *arg)
{
    (void)arg;
    const button_isr_event_t event = {
        .observed_at_mono_ms = (uint64_t)xTaskGetTickCountFromISR() * portTICK_PERIOD_MS,
    };
    BaseType_t higher_priority_task_woken = pdFALSE;
    (void)xQueueSendFromISR(s_input_queue, &event, &higher_priority_task_woken);
    if (higher_priority_task_woken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

static door_effect_id_t profile_effect_for_id(const char *profile_id)
{
    if (profile_id == NULL || profile_id[0] == '\0') {
        return DOOR_EFFECT_GENERIC_PRESS;
    }
    door_effect_id_t eff = door_effect_from_name(profile_id);
    if (eff != DOOR_EFFECT_NONE) {
        return eff;
    }
    return DOOR_EFFECT_BLUE_WAVE;
}

static void enqueue_effect(door_effect_id_t effect_id, const char *profile_id)
{
    effect_command_t command = {
        .effect_id = effect_id,
    };
    if (profile_id != NULL) {
        snprintf(command.profile_id, sizeof(command.profile_id), "%s", profile_id);
    }
    (void)xQueueSend(s_effect_queue, &command, 0);
}

static bool fill_press_random(uint8_t *buffer, size_t len, void *user)
{
    (void)user;
    size_t offset = 0;

    while (offset < len) {
        uint32_t random_word = esp_random();
        size_t remaining = len - offset;
        size_t copy_len = remaining < sizeof(random_word) ? remaining : sizeof(random_word);
        memcpy(&buffer[offset], &random_word, copy_len);
        offset += copy_len;
    }
    return true;
}

static bool make_press_id(char *out, size_t out_len)
{
    return door_protocol_make_uuid_v4(out, out_len, fill_press_random, NULL);
}

static void input_task(void *arg)
{
    (void)arg;
    button_isr_event_t event;
    uint64_t last_press_mono_ms = 0;

    (void)esp_task_wdt_add(NULL);
    for (;;) {
        if (xQueueReceive(s_input_queue, &event, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        if (last_press_mono_ms != 0 &&
            event.observed_at_mono_ms - last_press_mono_ms < DOORBOARD_BUTTON_DEBOUNCE_MS) {
            esp_task_wdt_reset();
            continue;
        }
        last_press_mono_ms = event.observed_at_mono_ms;

        /*
         * The local effect is scheduled before the link event is queued. This
         * preserves button feedback when the Pi, UART, or control plane is down.
         */
        enqueue_effect(DOOR_EFFECT_GENERIC_PRESS, NULL);

        portENTER_CRITICAL(&s_protocol_mux);
        const char *profile_id = door_protocol_cached_profile_id(&s_protocol, event.observed_at_mono_ms);
        char cached_profile[DOOR_PROTOCOL_PROFILE_ID_BYTES] = {0};
        if (profile_id != NULL) {
            snprintf(cached_profile, sizeof(cached_profile), "%s", profile_id);
        }
        portEXIT_CRITICAL(&s_protocol_mux);

        if (cached_profile[0] != '\0') {
            enqueue_effect(profile_effect_for_id(cached_profile), cached_profile);
        }

        link_button_event_t link_event = {
            .pressed_at_mono_ms = event.observed_at_mono_ms,
        };
        if (make_press_id(link_event.press_id, sizeof(link_event.press_id))) {
            (void)xQueueSend(s_link_queue, &link_event, 0);
        } else {
            ESP_LOGE(TAG, "button_event dropped because press_id generation failed");
        }
        esp_task_wdt_reset();
    }
}

static void write_leds(const door_effect_frame_t *frame)
{
    if (s_led_strip == NULL) return;
    for (int i = 0; i < DOORBOARD_NUM_LEDS; i++) {
        led_strip_set_pixel(s_led_strip, i, frame->leds[i].r, frame->leds[i].g, frame->leds[i].b);
    }
    led_strip_refresh(s_led_strip);
}

static void clear_leds(void)
{
    if (s_led_strip == NULL) return;
    led_strip_clear(s_led_strip);
}

static void play_tone(uint32_t freq_hz, uint32_t duration_ms)
{
    if (s_tx_chan == NULL) return;

    audio_command_t peek_cmd;
    if (freq_hz == 0) {
        int16_t silence_buf[AUDIO_BUFFER_SIZE] = {0};
        uint32_t total_samples = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
        uint32_t samples_written = 0;
        while (samples_written < total_samples) {
            if (xQueuePeek(s_audio_queue, &peek_cmd, 0) == pdTRUE) {
                break;
            }
            uint32_t chunk_samples = (total_samples - samples_written) > AUDIO_BUFFER_SIZE ? 
                                     AUDIO_BUFFER_SIZE : 
                                     (total_samples - samples_written);
            size_t bytes_written = 0;
            i2s_channel_write(s_tx_chan, silence_buf, chunk_samples * sizeof(int16_t), &bytes_written, portMAX_DELAY);
            if (bytes_written == 0) {
                break;
            }
            samples_written += bytes_written / sizeof(int16_t);
        }
        return;
    }

    int16_t tone_buf[AUDIO_BUFFER_SIZE];
    uint32_t total_samples = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
    uint32_t samples_written = 0;
    float phase = 0.0f;
    float phase_increment = (2.0f * (float)M_PI * (float)freq_hz) / (float)AUDIO_SAMPLE_RATE;

    while (samples_written < total_samples) {
        if (xQueuePeek(s_audio_queue, &peek_cmd, 0) == pdTRUE) {
            break;
        }
        uint32_t chunk_samples = (total_samples - samples_written) > AUDIO_BUFFER_SIZE ? 
                                 AUDIO_BUFFER_SIZE : 
                                 (total_samples - samples_written);
        for (uint32_t i = 0; i < chunk_samples; i++) {
            tone_buf[i] = (int16_t)(sinf(phase) * 10000.0f);
            phase += phase_increment;
            if (phase >= 2.0f * (float)M_PI) {
                phase -= 2.0f * (float)M_PI;
            }
        }
        size_t bytes_written = 0;
        i2s_channel_write(s_tx_chan, tone_buf, chunk_samples * sizeof(int16_t), &bytes_written, portMAX_DELAY);
        if (bytes_written == 0) {
            break;
        }
        samples_written += bytes_written / sizeof(int16_t);
    }
}

static void audio_task(void *arg)
{
    (void)arg;
    audio_command_t cmd;

    (void)esp_task_wdt_add(NULL);
    for (;;) {
        if (xQueueReceive(s_audio_queue, &cmd, portMAX_DELAY) == pdTRUE) {
            const door_audio_cue_t *cue = door_effects_get_audio_cue(cmd.effect_id);
            for (uint32_t i = 0; i < cue->num_tones; i++) {
                audio_command_t peek_cmd;
                if (xQueuePeek(s_audio_queue, &peek_cmd, 0) == pdTRUE) {
                    break;
                }
                play_tone(cue->tones[i].frequency_hz, cue->tones[i].duration_ms);
            }
        }
        esp_task_wdt_reset();
    }
}

static void effects_task(void *arg)
{
    (void)arg;
    effect_command_t command;
    door_effect_state_t state;
    door_effects_init_state(&state);
    bool animation_active = false;

    (void)esp_task_wdt_add(NULL);
    for (;;) {
        TickType_t wait_ticks = animation_active ? pdMS_TO_TICKS(30) : portMAX_DELAY;

        if (xQueueReceive(s_effect_queue, &command, wait_ticks) == pdTRUE) {
            door_effects_start(&state, command.effect_id, command.profile_id[0] != '\0' ? command.profile_id : NULL);
            animation_active = (state.effect_id != DOOR_EFFECT_NONE);

            audio_command_t audio_cmd = {
                .effect_id = state.effect_id,
            };
            (void)xQueueSend(s_audio_queue, &audio_cmd, 0);
        }

        if (animation_active) {
            door_effect_frame_t frame;
            bool continues = door_effects_step(&state, &frame);
            write_leds(&frame);
            if (!continues) {
                animation_active = false;
                clear_leds();
            }
        }
        esp_task_wdt_reset();
    }
}

static void uart_write_frame(const char *frame)
{
    if (frame != NULL && frame[0] != '\0') {
        (void)uart_write_bytes(DOORBOARD_UART_PORT_NUM, frame, strlen(frame));
    }
}

static void protocol_send_due(uint64_t now_mono_ms)
{
    char frame[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];

    portENTER_CRITICAL(&s_protocol_mux);
    bool have_frame = door_protocol_next_tx(&s_protocol, now_mono_ms, frame, sizeof(frame));
    portEXIT_CRITICAL(&s_protocol_mux);
    if (have_frame) {
        uart_write_frame(frame);
    }
}

static void handle_uart_rx(uint64_t now_mono_ms)
{
    static char line[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
    static size_t line_len;
    uint8_t byte = 0;
    char ack[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];

    while (uart_read_bytes(DOORBOARD_UART_PORT_NUM, &byte, 1, 0) == 1) {
        if (line_len >= DOOR_PROTOCOL_MAX_FRAME_BYTES) {
            line_len = 0;
        }
        line[line_len++] = (char)byte;
        if (byte != '\n') {
            continue;
        }

        portENTER_CRITICAL(&s_protocol_mux);
        door_protocol_rx_result_t result = door_protocol_receive_from_pi(
            &s_protocol,
            line,
            line_len,
            now_mono_ms,
            NULL,
            ack,
            sizeof(ack)
        );
        portEXIT_CRITICAL(&s_protocol_mux);
        if (result == DOOR_PROTOCOL_RX_ACK_EMITTED) {
            uart_write_frame(ack);
        }
        line_len = 0;
    }
}

static void handle_link_button_event(const link_button_event_t *event)
{
    portENTER_CRITICAL(&s_protocol_mux);
    bool queued = door_protocol_emit_button_event(
        &s_protocol,
        event->press_id,
        event->pressed_at_mono_ms
    );
    portEXIT_CRITICAL(&s_protocol_mux);
    if (!queued) {
        ESP_LOGW(TAG, "button_event dropped because protocol tx queue is full");
    }
}

static void link_task(void *arg)
{
    (void)arg;
    link_button_event_t button_event;
    uint64_t last_heartbeat_mono_ms = 0;
    bool fallback_was_active = true;

    (void)esp_task_wdt_add(NULL);

    portENTER_CRITICAL(&s_protocol_mux);
    (void)door_protocol_start_hello(&s_protocol);
    portEXIT_CRITICAL(&s_protocol_mux);

    for (;;) {
        uint64_t now = mono_ms();

        while (xQueueReceive(s_link_queue, &button_event, 0) == pdTRUE) {
            handle_link_button_event(&button_event);
        }

        handle_uart_rx(now);
        protocol_send_due(now);

        if (now - last_heartbeat_mono_ms >= 1000) {
            char heartbeat[DOOR_PROTOCOL_MAX_FRAME_BYTES + 1];
            portENTER_CRITICAL(&s_protocol_mux);
            bool have_heartbeat = door_protocol_make_heartbeat(
                &s_protocol,
                now,
                (uint32_t)(now / 1000U),
                heartbeat,
                sizeof(heartbeat)
            );
            bool fallback_active = s_protocol.fallback_active;
            portEXIT_CRITICAL(&s_protocol_mux);
            if (have_heartbeat) {
                uart_write_frame(heartbeat);
            }
            if (fallback_active && !fallback_was_active) {
                enqueue_effect(DOOR_EFFECT_FALLBACK, NULL);
            }
            fallback_was_active = fallback_active;
            last_heartbeat_mono_ms = now;
        }

        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

static void sensors_task(void *arg)
{
    (void)arg;
    (void)esp_task_wdt_add(NULL);
    for (;;) {
#if CONFIG_DOORBOARD_ENABLE_KNOCK_DETECTION
        const bool threshold_crossed = false;
        if (threshold_crossed) {
            portENTER_CRITICAL(&s_protocol_mux);
            (void)door_protocol_emit_knock_event(&s_protocol, "threshold", 0.5);
            portEXIT_CRITICAL(&s_protocol_mux);
        }
#endif
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

static void configure_gpio(void)
{
    const gpio_config_t button_config = {
        .pin_bit_mask = 1ULL << DOORBOARD_PIN_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };

    ESP_ERROR_CHECK(gpio_config(&button_config));
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM));
    ESP_ERROR_CHECK(gpio_isr_handler_add(DOORBOARD_PIN_BUTTON_GPIO, button_isr_handler, NULL));
}

static void configure_leds(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = DOORBOARD_PIN_LED_DATA_GPIO,
        .max_leds = DOORBOARD_NUM_LEDS,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false,
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .flags.with_dma = false,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_led_strip));
    led_strip_clear(s_led_strip);
}

static void configure_audio(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &s_tx_chan, NULL));

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = DOORBOARD_PIN_AUDIO_BCLK_GPIO,
            .ws = DOORBOARD_PIN_AUDIO_WS_GPIO,
            .dout = DOORBOARD_PIN_AUDIO_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_tx(s_tx_chan, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx_chan));
}

static void configure_uart(void)
{
    const uart_config_t uart_config = {
        .baud_rate = DOORBOARD_UART_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(DOORBOARD_UART_PORT_NUM, 2048, 2048, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(DOORBOARD_UART_PORT_NUM, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(
        DOORBOARD_UART_PORT_NUM,
        DOORBOARD_PIN_UART_TX_GPIO,
        DOORBOARD_PIN_UART_RX_GPIO,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE
    ));
}

static void configure_watchdog(void)
{
    const esp_task_wdt_config_t twdt_config = {
        .timeout_ms = 4000,
        .idle_core_mask = 0,
        .trigger_panic = true,
    };
    esp_err_t err = esp_task_wdt_init(&twdt_config);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_ERROR_CHECK(err);
    }
}

static void on_effect_play_received(const char *effect_id, uint32_t duration_ms, void *user)
{
    (void)duration_ms;
    (void)user;
    door_effect_id_t id = door_effect_from_name(effect_id);
    if (id != DOOR_EFFECT_NONE) {
        enqueue_effect(id, NULL);
    }
}

void app_main(void)
{
    configure_watchdog();
    configure_uart();

    s_input_queue = xQueueCreate(DOORBOARD_INPUT_QUEUE_DEPTH, sizeof(button_isr_event_t));
    s_effect_queue = xQueueCreate(DOORBOARD_EFFECT_QUEUE_DEPTH, sizeof(effect_command_t));
    s_link_queue = xQueueCreate(DOORBOARD_LINK_QUEUE_DEPTH, sizeof(link_button_event_t));
    s_audio_queue = xQueueCreate(DOORBOARD_EFFECT_QUEUE_DEPTH, sizeof(audio_command_t));

    ESP_ERROR_CHECK(s_input_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    ESP_ERROR_CHECK(s_effect_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    ESP_ERROR_CHECK(s_link_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    ESP_ERROR_CHECK(s_audio_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);

    door_protocol_init(&s_protocol, "esp32-door-controller", "t-101-0.1.0");
    s_protocol.effect_play_cb = on_effect_play_received;
    s_protocol.effect_play_cb_user = NULL;

    configure_gpio();
    configure_leds();
    configure_audio();

    xTaskCreate(input_task, "input", 4096, NULL, 12, NULL);
    xTaskCreate(effects_task, "effects", 4096, NULL, 10, NULL);
    xTaskCreate(audio_task, "audio", 4096, NULL, 11, NULL);
    xTaskCreate(link_task, "link", 6144, NULL, 8, NULL);
    xTaskCreate(sensors_task, "sensors", 4096, NULL, 5, NULL);

    enqueue_effect(DOOR_EFFECT_BOOT, NULL);
}
