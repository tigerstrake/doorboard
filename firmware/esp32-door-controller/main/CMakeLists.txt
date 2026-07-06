idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_timer esp_task_wdt door_protocol door_effects led_strip
)

