"""
High-level interface: ``ZenControl`` composition root and entity models.

Prefer ``ZenControl`` for applications (commands + events + discovery).
``EntityContext`` is the advanced/command-only surface used when you own the
command client yourself and do not need the event session.
"""

from .context import ControllerRuntimeStatus, EntityContext, ZenCallbacks
from .entities import (
    ZenAbsoluteInput,
    ZenButton,
    ZenControlGear,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenProfile,
    ZenSystemVariable,
)
from .interface import ZenControl

__all__ = [
    # High-level client
    "ZenControl",
    "ZenCallbacks",
    "ControllerRuntimeStatus",
    # Advanced: command-only (no event session)
    "EntityContext",

    # High-level models
    "ZenController",
    "ZenProfile",
    "ZenControlGear",
    "ZenLight",
    "ZenGroup",
    "ZenButton",
    "ZenAbsoluteInput",
    "ZenMotionSensor",
    "ZenSystemVariable",
]
