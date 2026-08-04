"""Wire-level request/response envelopes for the TPI command plane."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from .const import ClientConst


class ZenRequestType(IntEnum):
    """Types of requests that can be sent"""
    BASIC = 0x01
    DYNAMIC = 0x02
    DALI_COLOUR = 0x03
    COMMAND = 0x04


@dataclass(slots=True)
class ZenRequest:
    """Represents a request to be sent to the controller"""
    command: int
    data: bytes | list[int]
    request_type: ZenRequestType = ZenRequestType.BASIC
    seq: int | None = None
    raw_sent: bytes | None = None
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.timestamp = time.time()
        # If data is a list, convert it to a bytes object
        if isinstance(self.data, list):
            self.data = bytes([d & 0xFF for d in self.data])
        # Length of data
        n = len(self.data)
        # Validate request type
        match self.request_type:
            case ZenRequestType.BASIC:
                # Pad data to 4 bytes if it's less than 4 bytes
                self.data = self.data + bytes([0x00] * (4 - n)) if n < 4 else self.data
                if len(self.data) != 4:
                    raise ValueError("ZenRequest.data must be exactly 4 bytes when request type is BASIC")
            case ZenRequestType.DALI_COLOUR:
                pass
            case ZenRequestType.DYNAMIC:
                # Prepend data length to data
                self.data = bytes([n]) + self.data
            case ZenRequestType.COMMAND:
                # No padding for command type
                pass

    def to_bytes(self, checksum: Callable[[bytes], int]) -> bytes:
        """Convert request to wire format"""
        if self.seq is None:
            raise ValueError("ZenRequest.seq must be set before calling to_bytes")
        data = self.data if isinstance(self.data, bytes) else bytes([d & 0xFF for d in self.data])
        req = bytes([ClientConst.COMMAND_MAGIC, self.seq & 0xFF, self.command & 0xFF]) + data
        cs = bytes([checksum(req) & 0xFF])
        self.raw_sent = req + cs
        return req + cs


class ZenResponseType(IntEnum):
    """Types of responses from the controller"""
    OK = 0xA0
    ANSWER = 0xA1
    NO_ANSWER = 0xA2
    ERROR = 0xA3
    TIMEOUT = 0xAE
    INVALID = 0xAF


@dataclass(slots=True)
class ZenResponse:
    response_type: ZenResponseType
    seq: int | None = None
    data: bytes | None = None  # empty for TIMEOUT and INVALID
    raw_rcvd: bytes | None = None
    request: ZenRequest | None = None
    addr: tuple[str, int] | None = None
    timestamp: float = field(default_factory=time.time)
