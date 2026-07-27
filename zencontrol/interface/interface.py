from __future__ import annotations

import asyncio
import logging
from typing import Any, Self

from ..api import (
    ZenAddress,
    ZenInstanceType,
)
from ..api.commands import ZenCommandClient
from ..api.event_decode import ZenDecodedEvent
from ..api.event_router import EventHealth, ZenEventReceiver
from ..api.models import DiscoveredController
from ..api.types import Const, Transport, ZenEventMode
from .context import ControllerRuntimeStatus, EntityContext, ZenCallbacks
from .discovery import ControllerDiscovery
from .dispatch import EventDispatcher
from .entities import (
    ZenAbsoluteInput,
    ZenButton,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenProfile,
    ZenSystemVariable,
    _assign_light_sub_labels,
)
from .session import ZenSession

"""
===================================================================================
High-level interface: ZenControl composition root.
===================================================================================
"""


class ZenControl:
    def __init__(
        self,
        logger: logging.Logger | None = None,
        print_traffic: bool = False,
        unicast: bool = False,
        listen_ip: str | None = None,
        listen_port: int | None = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        # Preferred TPI event emit transport (not a socket bind).
        self.unicast = unicast
        self.commands: ZenCommandClient = ZenCommandClient(
            logger=self.logger,
            print_traffic=print_traffic,
        )
        self.context = EntityContext(commands=self.commands, logger=self.logger)
        self.controllers: list[ZenController] = []
        listen_ip_val = listen_ip if listen_ip else "0.0.0.0"
        self.event_receiver = ZenEventReceiver(
            logger=self.logger,
            unicast_listen_ip=listen_ip_val if unicast else "0.0.0.0",
            unicast_port=(listen_port if listen_port else 0) if unicast else 0,
        )
        self.identities = self.event_receiver.identities
        self.identities.on_discovered = self._forward_discovered
        self._enrich_locks: dict[str, asyncio.Lock] = {}
        self.reconnect_min_delay = Const.RECONNECT_MIN_DELAY
        self.reconnect_max_delay = Const.RECONNECT_MAX_DELAY
        self.reconnect_healthy_seconds = Const.RECONNECT_HEALTHY_SECONDS
        self.event_keepalive_interval = Const.EVENT_KEEPALIVE_INTERVAL

        self._dispatcher = EventDispatcher(self.context, self.logger)
        self._discovery = ControllerDiscovery(self)
        self._session = ZenSession(self, event_handler=self._on_controller_event)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    @property
    def session(self) -> ZenSession:
        """Event-plane session (wiring, supervisor, keepalive)."""
        return self._session

    @property
    def event_task(self) -> asyncio.Task[None] | None:
        return self._session.event_task

    def is_event_monitoring_active(self) -> bool:
        return self._session.is_event_monitoring_active()

    def is_session_running(self) -> bool:
        """True when the session supervisor is alive (may still be binding)."""
        task = self._session.supervisor_task
        return task is not None and not task.done()

    async def notify_disconnect(self) -> None:
        await self._session.notify_disconnect()

    async def start(self) -> None:
        await self._session.start()

    async def stop(self) -> None:
        await self._session.stop()

    async def aclose(self) -> None:
        await self._session.aclose()

    async def _on_controller_event(self, controller: ZenController, ev: ZenDecodedEvent) -> None:
        await self._dispatcher.handle(controller, ev)

    def _forget_event_dispatch(self, name: str) -> None:
        self._dispatcher.forget(name)

    def _forget_event_dispatch_all(self) -> None:
        self._dispatcher.clear()

    async def _on_resync_callback(self) -> None:
        callback = self.callbacks.on_resync
        if not callable(callback):
            return
        try:
            await callback()
        except Exception as err:
            self.logger.error(f"on_resync error: {err}", exc_info=True)

    async def enrich_discovered(self, discovered: DiscoveredController) -> DiscoveredController:
        return await self._discovery.enrich_discovered(discovered)

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        return await self._discovery.discover(timeout=timeout)

    def clear_entity_caches(self) -> None:
        """Clear entity singleton registries for this ZenControl instance."""
        self.context.clear_entity_caches()

    @property
    def callbacks(self) -> ZenCallbacks:
        """Application callback registry (same object as ``context.callbacks``)."""
        return self.context.callbacks

    @property
    def discovered_controllers(self) -> list[DiscoveredController]:
        """Controllers identified from multicast but not yet registered."""
        return list(self.identities.discovered)

    def event_health_for(self, controller: ZenController | str) -> EventHealth | None:
        """Per-binding event-plane health, or None if the controller is not attached."""
        if self._session.wiring is None:
            return None
        binding = self._session.wiring.get(controller)
        return None if binding is None else binding.event_health

    async def _forward_discovered(self, discovered: DiscoveredController) -> None:
        callback = self.context.callbacks.controller_discovered
        if not callable(callback):
            return
        try:
            await callback(discovered)
        except Exception as err:
            self.logger.error("controller_discovered callback error: %s", err, exc_info=err)

    async def _notify_controller_identified(self, controller: ZenController, mac: str) -> None:
        callback = self.context.callbacks.controller_identified
        if not callable(callback):
            return
        try:
            await callback(controller, mac)
        except Exception as err:
            self.logger.error("controller_identified callback error: %s", err, exc_info=err)

    def add_controller(
        self, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False
    ) -> ZenController:
        controller = ZenController(
            ctx=self.context, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering
        )
        self.controllers.append(controller)
        self.identities.forget(host=host, mac=mac)
        return controller

    async def remove_controller(self, controller: ZenController | str) -> None:
        """Detach a controller and close its command client.

        Safe to call while event monitoring is running. Does not stop the shared
        listener; callers that own the last controller should ``aclose()``.
        """
        name = controller if isinstance(controller, str) else controller.name
        removed = [c for c in self.controllers if c.name == name]
        self.controllers = [c for c in self.controllers if c.name != name]
        if self._session.wiring is not None:
            await self._session.wiring.detach(name)
        self._forget_event_dispatch(name)
        self.context.purge_controller_entities(name)
        for ctrl in removed:
            await self.commands._invalidate_client(ctrl)

    def _event_mode_for(self, controller: ZenController) -> ZenEventMode:
        return ZenEventMode(
            enabled=True,
            filtering=controller.filtering,
            transport=(Transport.UNICAST if self.unicast else Transport.MULTICAST),
        )

    async def configure_controller_events(self, controller: ZenController) -> bool:
        """Enable TPI event emit for one controller using this client's listen mode.

        Call after ``add_controller`` when event monitoring is already running so a
        newly attached controller joins the shared listener. Returns True when the
        emit-enable command succeeds.
        """
        if self._session.wiring is None:
            return False
        mode = self._event_mode_for(controller)
        try:
            if self._session.wiring.get(controller) is not None:
                await self._session.wiring.rearm(controller)
            else:
                await self._session.wiring.attach(controller, mode)
            return True
        except Exception as err:
            self.logger.debug(
                "configure_controller_events failed for %s: %s",
                controller.name,
                err,
            )
            return False

    async def assert_controller_events(self, controller: ZenController) -> bool:
        """Ping event emit state and re-assert config if the controller lost it.

        Controllers that reboot while our listener stays up typically come back
        with events disabled (or with a stale unicast target). Returns True when
        the controller is reachable and events are confirmed/enabled, False when
        the ping timed out / failed or re-assert could not enable emit.

        Never re-asserts while ``is_controller_ready()`` is false — the startup
        sequence can take several minutes after a reboot.
        """
        if not self.is_event_monitoring_active():
            return False
        if self._session.wiring is not None and self._session.wiring.get(controller) is None:
            # Binding was dropped (e.g. MAC promotion conflict) — do not keep
            # confirming emit into a route that no longer exists.
            self.logger.debug(
                "No event binding for %s — skipping emit keepalive",
                controller.name,
            )
            return False

        ready = await controller.is_controller_ready()
        if ready is None:
            self.logger.debug(
                "No response from %s during event keepalive ping",
                controller.name,
            )
            await self._notify_controller_status(controller, "unreachable")
            return False
        if ready is not True:
            self.logger.debug(
                "Controller %s still starting — deferring event re-assert",
                controller.name,
            )
            await self._notify_controller_status(controller, "starting")
            return True

        unicast = self.unicast
        needs_reassert = False
        info = await self.commands.query_tpi_event_unicast_address(controller)
        if info is not None:
            mode = info["mode"]
            if not mode.enabled or bool(mode.unicast) != unicast:
                needs_reassert = True
            elif unicast and self._unicast_target_mismatch(controller, info):
                needs_reassert = True
        else:
            enabled = await self.commands.query_tpi_event_emit_state(controller)
            if enabled is None:
                self.logger.debug(
                    "No response from %s during event keepalive ping",
                    controller.name,
                )
                await self._notify_controller_status(controller, "unreachable")
                return False
            needs_reassert = not enabled

        if needs_reassert:
            self.logger.info(
                "Controller %s TPI events not correctly enabled — re-asserting",
                controller.name,
            )
            if not await self.configure_controller_events(controller):
                self.logger.warning(
                    "Failed to re-assert TPI events for %s",
                    controller.name,
                )
                await self._notify_controller_status(controller, "unreachable")
                return False
        await self._notify_controller_status(controller, "online")
        return True

    def _unicast_target_mismatch(self, controller: ZenController, info: dict[str, Any]) -> bool:
        """True when the controller's programmed unicast target is wrong for it.

        Compares against that controller's binding advertise (per-``toward``).
        Without a live advertise there is nothing to compare — return False.
        """
        if self._session.wiring is None:
            return False
        binding = self._session.wiring.get(controller)
        advertise = None if binding is None else binding.lease.advertise
        if advertise is None:
            return False
        expected_ip, expected_port = advertise
        return info.get("port") != expected_port or info.get("ip") != expected_ip

    async def _notify_controller_status(self, controller: ZenController, status: ControllerRuntimeStatus) -> None:
        """Notify listeners of online / starting / unreachable."""
        callback = self.callbacks.controller_status_change
        if not callable(callback):
            return
        try:
            await callback(controller, status)
        except Exception as err:
            self.logger.debug(
                "controller_status_change error for %s: %s",
                controller.name,
                err,
            )

    async def get_profiles(self, controller: ZenController | None = None) -> set[ZenProfile]:
        """Return a set of all profiles."""
        profiles: set[ZenProfile] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            numbers = await self.commands.query_profile_numbers(controller=ctrl)
            if numbers is None:
                continue
            for number in numbers:
                profile = await ZenProfile.create(ctx=self.context, controller=ctrl, number=number)
                profiles.add(profile)
        return profiles

    async def get_groups(self, controller: ZenController | None = None) -> set[ZenGroup]:
        """Return a set of all groups (optionally for one controller)."""
        groups: set[ZenGroup] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_group_numbers(controller=ctrl)
            for address in addresses:
                group = await ZenGroup.create(ctx=self.context, address=address)
                groups.add(group)
        return groups
    
    async def get_lights(self, controller: ZenController | None = None) -> set[ZenLight]:
        """Return a set of all lights available (optionally for one controller)."""
        lights: set[ZenLight] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_control_gear_dali_addresses(controller=ctrl)
            for address in addresses:
                light = await ZenLight.create(ctx=self.context, address=address)
                lights.add(light)
        # Second pass: labels are known; split shared comma-labels into sub_labels.
        _assign_light_sub_labels(lights)
        return lights
    
    async def _get_addresses_with_instances(self, controller: ZenController) -> list[ZenAddress]:
        """Return all DALI addresses that have instances (full address-space scan)."""
        return await self.commands.query_dali_addresses_with_instances(controller)

    async def get_buttons(self, controller: ZenController | None = None) -> set[ZenButton]:
        """Return a set of all buttons available (optionally for one controller)."""
        buttons: set[ZenButton] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.PUSH_BUTTON:
                        button = await ZenButton.create(ctx=self.context, instance=instance)
                        buttons.add(button)
        return buttons
    
    async def get_motion_sensors(self, controller: ZenController | None = None) -> set[ZenMotionSensor]:
        """Return a set of all motion sensors available (optionally for one controller)."""
        motion_sensors: set[ZenMotionSensor] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.OCCUPANCY_SENSOR:
                        motion_sensor = await ZenMotionSensor.create(ctx=self.context, instance=instance)
                        motion_sensors.add(motion_sensor)
        return motion_sensors

    async def get_absolute_inputs(self, controller: ZenController | None = None) -> set[ZenAbsoluteInput]:
        """Return absolute (numerical) ECD instances (optionally for one controller)."""
        absolute_inputs: set[ZenAbsoluteInput] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.ABSOLUTE_INPUT:
                        absolute_input = await ZenAbsoluteInput.create(ctx=self.context, instance=instance)
                        absolute_inputs.add(absolute_input)
        return absolute_inputs

    async def get_system_variables(
        self,
        give_up_after: int = 10,
        controller: ZenController | None = None,
    ) -> set[ZenSystemVariable]:
        """Return labelled system variables (optionally for one controller)."""
        sysvars: set[ZenSystemVariable] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            failed_attempts = 0
            for variable in range(Const.MAX_SYSVAR):
                label = await self.commands.query_system_variable_name(controller=ctrl, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = await ZenSystemVariable.create(ctx=self.context, controller=ctrl, id=variable, label=label)
                    sysvars.add(sysvar)
                else:
                    failed_attempts += 1
                    if failed_attempts >= give_up_after:
                        break
        return sysvars


__all__ = ["ZenControl", "ControllerRuntimeStatus"]
