"""Wire-level constants for the TPI command plane."""


class ClientConst:
    """Constants for ZenClient / ZenTcpClient."""

    COMMAND_MAGIC = 0x04
    DEFAULT_TIMEOUT = 1.5
    MIN_TIMEOUT = 0.01
    MAX_TIMEOUT = 10.0
    # UDP datagram retries (lost packets / brief network blips); separate from QUEUE_FAILURE
    DEFAULT_RETRIES = 1
    # TPI ERROR payload: controller DALI command queue briefly full
    QUEUE_FAILURE = 0xB3
    QUEUE_FAILURE_RETRIES = 3
    QUEUE_FAILURE_BASE_DELAY = 0.05  # doubles each attempt: 50/100/200ms
