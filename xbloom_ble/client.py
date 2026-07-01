"""Async Bluetooth LE client for the xBloom Studio (via ``bleak``).

This is the only module that touches hardware. It discovers the machine,
connects, writes the LOAD frames, and streams status telemetry.

⚠️ Safety: :meth:`XBloomClient.load_recipe` only ever *loads* a recipe. It
writes the four LOAD frames (``a4, a6, a8, 41``) and returns once the machine
reports STATE ``0x1f`` (armed). It then waits for the human to approve the brew
on the machine. There is no method that sends ``0x42``/``0x46``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .protocol import build_load_frames
from .recipe import Recipe
from .telemetry import StatusEvent, parse_notification

log = logging.getLogger("xbloom_ble")

# Vendor GATT identifiers.
SERVICE_UUID = "0000e0ff-3c17-d293-8e48-14fe2e4da212"
CHAR_COMMAND = "0000ffe1-0000-1000-8000-00805f9b34fb"  # ffe1 — write
CHAR_STATUS = "0000ffe2-0000-1000-8000-00805f9b34fb"   # ffe2 — notify
CHAR_AUX = "0000ffe3-0000-1000-8000-00805f9b34fb"      # ffe3 — aux
NAME_PREFIX = "XBLOOM"

# State byte that means "recipe loaded / armed".
STATE_ARMED = 0x1F


class XBloomError(RuntimeError):
    """Raised on BLE / protocol errors in the client."""


def _short_uuid(uuid: str) -> str:
    """Return the 16-bit short form of a Bluetooth-base UUID, else the input."""
    u = uuid.lower()
    if u.endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
        return u[4:8]
    return u


async def scan(timeout: float = 8.0):
    """Discover xBloom machines.

    Returns a list of ``bleak.backends.device.BLEDevice`` whose advertisement
    exposes the vendor service UUID *or* whose name starts with ``XBLOOM``.
    """
    from bleak import BleakScanner

    log.info("scanning for xBloom machines (%.0fs)…", timeout)
    found: dict[str, object] = {}
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for address, (device, adv) in devices.items():
        name = (adv.local_name or getattr(device, "name", None) or "") or ""
        service_uuids = {u.lower() for u in (adv.service_uuids or [])}
        if SERVICE_UUID.lower() in service_uuids or name.upper().startswith(NAME_PREFIX):
            found[address] = device
            log.info("found %s (%s)", name or "?", address)
    return list(found.values())


class XBloomClient:
    """A connected session with one xBloom Studio.

    Use as an async context manager::

        async with XBloomClient(address) as client:
            await client.load_recipe(recipe)
            await client.stream_telemetry(on_event, duration=300)
    """

    def __init__(self, address: str, *, ack_timeout: float = 10.0):
        self.address = address
        self.ack_timeout = ack_timeout
        self._client = None
        self._notif_queue: "asyncio.Queue[StatusEvent]" = asyncio.Queue()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        from bleak import BleakClient

        log.info("connecting to %s…", self.address)
        self._client = BleakClient(self.address)
        await self._client.connect()
        if not self._client.is_connected:
            raise XBloomError(f"failed to connect to {self.address}")
        log.info("connected")

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
            log.info("disconnected")
        self._client = None

    async def __aenter__(self) -> "XBloomClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _on_notify(self, _sender, data: bytearray) -> None:
        event = parse_notification(bytes(data))
        if event is not None:
            self._notif_queue.put_nowait(event)

    async def _start_notify(self) -> None:
        assert self._client is not None
        await self._client.start_notify(CHAR_STATUS, self._on_notify)

    async def _stop_notify(self) -> None:
        if self._client is not None and self._client.is_connected:
            try:
                await self._client.stop_notify(CHAR_STATUS)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    async def _drain_until_state(self, state: int, timeout: float) -> StatusEvent:
        """Wait for a status event whose state byte equals ``state``."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise XBloomError(
                    f"timed out waiting for state 0x{state:02x} after {timeout:.0f}s"
                )
            try:
                event = await asyncio.wait_for(self._notif_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                raise XBloomError(
                    f"timed out waiting for state 0x{state:02x} after {timeout:.0f}s"
                ) from None
            if event.is_heartbeat:
                continue
            log.debug("status: %s (raw=%s)", event.state_name, event.raw.hex())
            if event.state == state:
                return event

    # ------------------------------------------------------------------
    # Loading a recipe  (the ONLY write capability — never starts a brew)
    # ------------------------------------------------------------------
    async def load_recipe(self, recipe: Recipe) -> StatusEvent:
        """Load ``recipe`` onto the machine and return once it is armed.

        Writes the four LOAD frames (``a4, a6, a8, 41``) to ``ffe1`` one at a
        time, waiting for each ACK on ``ffe2`` (the machine echoes the command),
        and returns the ``StatusEvent`` once the machine reaches STATE ``0x1f``
        (armed / loaded). **This never starts a brew** — the human approves on
        the machine.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        recipe.validate()
        frames = build_load_frames(recipe.to_protocol_dict())

        await self._start_notify()
        try:
            for i, frame in enumerate(frames):
                cmd = frame[3]
                log.info("→ load frame %d/%d (cmd=0x%02x)", i + 1, len(frames), cmd)
                # The machine's command characteristic (ffe1) accepts ONLY a Write Command
                # (write-without-response, ATT 0x52) — verified from the vendor app's own HCI
                # capture, which never uses a Write Request on ffe1. A Write Request (response=True)
                # is rejected by the firmware with GATT "Unlikely Error". The ACK arrives instead as
                # a notification on ffe2 (echoing the command byte), which we await below.
                await self._client.write_gatt_char(CHAR_COMMAND, frame, response=False)
                # Wait for the echoed ACK of this command on ffe2.
                await self._await_ack(cmd)
            # The final 0x41 drives the machine to the armed state; confirm it.
            armed = await self._drain_until_state(STATE_ARMED, self.ack_timeout)
            log.info("recipe loaded — machine armed (awaiting human approval)")
            return armed
        finally:
            await self._stop_notify()

    async def _await_ack(self, cmd: int) -> None:
        """Wait for the ACK frame echoing ``cmd`` (best-effort, tolerant)."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.ack_timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                log.warning("no explicit ACK for cmd 0x%02x; continuing", cmd)
                return
            try:
                event = await asyncio.wait_for(self._notif_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                log.warning("no explicit ACK for cmd 0x%02x; continuing", cmd)
                return
            if event.is_heartbeat:
                continue
            # An ACK echoes the command byte at offset 3.
            if len(event.raw) > 3 and event.raw[3] == cmd:
                log.debug("← ACK 0x%02x", cmd)
                return
            # A status frame arriving early (e.g. armed) also counts as progress;
            # put it back so the caller's state wait can see it.
            self._notif_queue.put_nowait(event)
            return

    # ------------------------------------------------------------------
    # Telemetry streaming
    # ------------------------------------------------------------------
    async def stream_telemetry(
        self,
        on_event: Callable[[StatusEvent], Optional[Awaitable[None]]],
        duration: float = 300.0,
        *,
        stop_on_terminal: bool = True,
    ) -> None:
        """Subscribe to ``ffe2`` and invoke ``on_event`` for each status event.

        Runs for up to ``duration`` seconds. If ``stop_on_terminal`` is set,
        returns early once a terminal state (complete / idle) is seen.
        ``on_event`` may be a plain or async callable.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        await self._start_notify()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + duration
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    log.info("telemetry duration elapsed")
                    return
                try:
                    event = await asyncio.wait_for(self._notif_queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    log.info("telemetry duration elapsed")
                    return
                if event.is_heartbeat:
                    continue
                result = on_event(event)
                if asyncio.iscoroutine(result):
                    await result
                if stop_on_terminal and event.is_terminal:
                    log.info("terminal state '%s' reached", event.state_name)
                    return
        finally:
            await self._stop_notify()
