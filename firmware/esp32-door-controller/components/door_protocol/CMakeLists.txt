idf_component_register(
    SRCS "door_protocol.c"
    INCLUDE_DIRS "include"
    # effect_play is validated against the pre-flashed effects table before it
    # is counted as applied.
    REQUIRES door_effects
)
