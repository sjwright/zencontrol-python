"""
Interface-layer constants — session lifecycle and entity policy.

"""


class Const:
    
    # ZenControl.start() waits this long for the first successful event-listener connect
    START_TIMEOUT = 30.0

    # Event-listener reconnect (ZenControl supervisor)
    RECONNECT_MIN_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0
    RECONNECT_HEALTHY_SECONDS = 60.0

    # Periodic emit-state check - controllers that reboot while our listener
    # stays up lose TPI event config until we re-assert it.
    EVENT_KEEPALIVE_INTERVAL = 30.0

    # Colour-temp fallbacks when QUERY_DALI_COLOUR_TEMP_LIMITS fails
    DEFAULT_WARMEST_TEMP = 2700
    DEFAULT_COOLEST_TEMP = 6500

    # RGBWAF channel counts used when classifying light colour features
    RGB_CHANNELS = 3
    RGBW_CHANNELS = 4
    RGBWW_CHANNELS = 5

    # Button / motion entity policy
    LONG_PRESS_COUNT = 2
    DEFAULT_HOLD_TIME = 60
