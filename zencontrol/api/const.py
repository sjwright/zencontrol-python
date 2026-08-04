"""DALI / TPI vocabulary limits and defaults.

Interface-layer session and entity policy live in zencontrol.interface.const.
"""


class Const:
    """Wire-facing limits shared by commands, models, and event decode."""

    # DALI limits
    MAX_ECG = 64  # 0-63
    MAX_ECD = 64  # 0-63
    MAX_INSTANCE = 32  # 0-31
    MAX_GROUP = 16  # 0-15
    MAX_SCENE = 12  # DALI protocol is 16 (0-15) but zencontrol cloud is soft-limited to 12 (0-11)
    MAX_SYSVAR = 148  # 0-147
    MAX_LEVEL = 254  # highest dimming arc
    MASK_LEVEL = 255  # DAPC mask (no change / stop fade on blinds)
    MIN_KELVIN = 1000
    MAX_KELVIN = 20000
    # DALI_COLOUR colour-data field width (unused channels are 0xFF)
    COLOUR_DATA_LEN = 7
