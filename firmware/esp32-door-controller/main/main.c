#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "door_protocol.h"
#include "doorboard_pinout.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_check.h"
#include "esp_intr_alloc.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

typedef enum {
    EFFECT_GENERIC_PRESS = 0,
    EFFECT_FALLBACK,
    EFFECT_PROFILE_BLUE_WAVE,
    EFFECT_PROFILE_GREEN_PULSE,
} effect_id_t;

typedef struct {
    uint64_t observed_at_mono_ms;
} button_isr_event_t;

typedef struct {
    effect_id_t effect_id;
    char profile_id[DOOR_PROTOCOL_PROFILE_ID_BYTES];
} effect_command_t;

typedef struct {
    char press_id[DOOR_PROTOCOL_PRESS_ID_BYTES];
    uint64_t pressed_at_mono_ms;
} link_button_event_t;

static const char *TAG = "doorboard-esp32";
static QueueHandle_t s_input_queue;
static QueueHandle_t s_effect_queue;
static QueueHandle_t s_link_queue;
static door_protocol_t s_protocol;
static portMUX_TYPE s_protocol_mux = portMUX_INITIALIZER_UNLOCKED;

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

static effect_id_t profile_effect_for_id(const char *profile_id)
{
    if (profile_id != NULL && strcmp(profile_id, "green_pulse") == 0) {
        return EFFECT_PROFILE_GREEN_PULSE;
    }
    if (profile_id != NULL && profile_id[0] != '\0') {
        return EFFECT_PROFILE_BLUE_WAVE;
    }
    return EFFECT_GENERIC_PRESS;
}

static void enqueue_effect(effect_id_t effect_id, const char *profile_id)
{
    effect_command_t command = {
        .effect_id = effect_id,
    };
    if (profile_id != NULL) {
        snprintf(command.profile_id, sizeof(command.profile_id), "%s", profile_id);
    }
    (void)xQueueSend(s_effect_queue, &command, 0);
}

static void make_press_id(uint64_t press_counter, char *out, size_t out_len)
{
    snprintf(out, out_len, "00000000-0000-0000-0000-%012llu", (unsigned long long)press_counter);
}

static void input_task(void *arg)
{
    (void)arg;
    button_isr_event_t event;
    uint64_t last_press_mono_ms = 0;
    uint64_t press_counter = 0;

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
        press_counter++;

        /*
         * The local effect is scheduled before the link event is queued. This
         * preserves button feedback when the Pi, UART, or control plane is down.
         */
        enqueue_effect(EFFECT_GENERIC_PRESS, NULL);

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
        make_press_id(press_counter, link_event.press_id, sizeof(link_event.press_id));
        (void)xQueueSend(s_link_queue, &link_event, 0);
        esp_task_wdt_reset();
    }
}

static void effects_task(void *arg)
{
    (void)arg;
    effect_command_t command;

    (void)esp_task_wdt_add(NULL);
    for (;;) {
        if (xQueueReceive(s_effect_queue, &command, pdMS_TO_TICKS(1000)) == pdTRUE) {
            switch (command.effect_id) {
                case EFFECT_GENERIC_PRESS:
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 1);
                    vTaskDelay(pdMS_TO_TICKS(20));
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 0);
                    break;
                case EFFECT_FALLBACK:
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 1);
                    vTaskDelay(pdMS_TO_TICKS(80));
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 0);
                    break;
                case EFFECT_PROFILE_BLUE_WAVE:
                case EFFECT_PROFILE_GREEN_PULSE:
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 1);
                    vTaskDelay(pdMS_TO_TICKS(50));
                    gpio_set_level(DOORBOARD_PIN_LED_DATA_GPIO, 0);
                    break;
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
        ESP_LOGW(TAG, "button_event dropped because protocol tx slot is busy");
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
                enqueue_effect(EFFECT_FALLBACK, NULL);
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
    const gpio_config_t led_config = {
        .pin_bit_mask = 1ULL << DOORBOARD_PIN_LED_DATA_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    ESP_ERROR_CHECK(gpio_config(&button_config));
    ESP_ERROR_CHECK(gpio_config(&led_config));
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM));
    ESP_ERROR_CHECK(gpio_isr_handler_add(DOORBOARD_PIN_BUTTON_GPIO, button_isr_handler, NULL));
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

void app_main(void)
{
    configure_watchdog();
    configure_uart();

    s_input_queue = xQueueCreate(DOORBOARD_INPUT_QUEUE_DEPTH, sizeof(button_isr_event_t));
    s_effect_queue = xQueueCreate(DOORBOARD_EFFECT_QUEUE_DEPTH, sizeof(effect_command_t));
    s_link_queue = xQueueCreate(DOORBOARD_LINK_QUEUE_DEPTH, sizeof(link_button_event_t));
    ESP_ERROR_CHECK(s_input_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    ESP_ERROR_CHECK(s_effect_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    ESP_ERROR_CHECK(s_link_queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);

    door_protocol_init(&s_protocol, "esp32-door-controller", "t-101-0.1.0");
    configure_gpio();

    xTaskCreate(input_task, "input", 4096, NULL, 12, NULL);
    xTaskCreate(effects_task, "effects", 4096, NULL, 10, NULL);
    xTaskCreate(link_task, "link", 6144, NULL, 8, NULL);
    xTaskCreate(sensors_task, "sensors", 4096, NULL, 5, NULL);
}
