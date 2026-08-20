idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "include"
    # No esp_task_wdt here: it is not a component in IDF 5.x. `esp_task_wdt.h`
    # ships inside esp_system, which is already one of the common requirements
    # every component receives, so naming it made the build fail to resolve a
    # component that does not exist. Same for esp_random.h (esp_hw_support) and
    # esp_check.h (esp_common) — both common, both deliberately absent below.
    #
    # led_strip comes from the component registry; see main/idf_component.yml.
    REQUIRES driver esp_timer door_protocol door_effects led_strip
)

