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
from collections.abc import Awaitable, Callable, Mapping, Sequence

from .protocol import build_load_frames, build_save_slot, build_session_start
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
# Slot-save status states (see telemetry): 0x43 saving, 0x25 saved, 0x01 idle.
STATE_IDLE = 0x01
STATE_SLOTS_SAVED = 0x25


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
        self._notif_queue: asyncio.Queue[StatusEvent] = asyncio.Queue()

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

    async def __aenter__(self) -> XBloomClient:
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

    async def save_slots(
        self,
        recipes: Sequence[Recipe] | Mapping[object, Recipe],
        *,
        scale: bool | Sequence[bool] = True,
    ) -> None:
        """Program the machine's three Easy-Mode preset slots (A, B, C) in one batch.

        ``recipes`` is either a sequence of **exactly three** :class:`Recipe`
        (slots A, B, C in order) or a mapping keyed by ``0/1/2`` or ``"A"/"B"/"C"``.
        The slots let you brew hands-free from the machine's dial later. **This
        never brews** — every frame is a ``0x2CF6`` slot write, never a brew-start
        opcode.

        ``scale`` toggles the on-brew scale in each stored preset: a single bool
        applies to all three, or pass a 3-element sequence for per-slot control.

        Why all three at once: the machine only *stores* the presets after it has
        received the whole A/B/C set (it then saves atomically — status
        ``0x43`` → ``0x25`` → idle). Writing a single slot leaves it hung and it
        shows **RETRY**, so this always writes the full trio; there is no commit
        frame.

        ⚠️ These presets live **on the machine**. Opening the xBloom app and
        reassigning a slot will push the app's own choices over BLE and overwrite
        what you set here — so program the slots when you intend to drive the
        machine from its dial, not the app.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        ordered = self._normalize_slots(recipes)
        scales = self._normalize_scale(scale)
        frames = []
        for i, recipe in enumerate(ordered):
            recipe.validate()
            frames.append(build_save_slot(recipe.to_protocol_dict(), i, scale=scales[i]))

        await self._start_notify()
        try:
            # 1. Open a session (a4) and let the machine settle to idle/ready.
            await self._client.write_gatt_char(
                CHAR_COMMAND, build_session_start(), response=False
            )
            try:
                await self._drain_until_state(STATE_IDLE, self.ack_timeout)
            except XBloomError:
                log.warning("machine idle not confirmed after session start; proceeding")
            await asyncio.sleep(1.0)

            # 2. Write all three slot frames back-to-back (NO commit). The machine
            #    acks each with a c2d204 notify; it stores the set once complete.
            for i, frame in enumerate(frames):
                log.info("→ save slot %s (scale=%s)", "ABC"[i], scales[i])
                await self._client.write_gatt_char(CHAR_COMMAND, frame, response=False)
                await asyncio.sleep(0.5)

            # 3. Confirm the save: the machine reports 0x25 (slots_saved). If it
            #    hangs at 0x43 (saving) and never reaches 0x25, the save failed.
            await self._drain_until_state(STATE_SLOTS_SAVED, self.ack_timeout)
            log.info("presets stored to slots A/B/C")
        finally:
            await self._stop_notify()

    @staticmethod
    def _normalize_slots(
        recipes: Sequence[Recipe] | Mapping[object, Recipe],
    ) -> list[Recipe]:
        """Return recipes as an ordered [A, B, C] list, requiring all three."""
        keymap = {0: 0, 1: 1, 2: 2, "a": 0, "b": 1, "c": 2, "A": 0, "B": 1, "C": 2}
        if isinstance(recipes, Mapping):
            out: list[Recipe | None] = [None, None, None]
            for key, recipe in recipes.items():
                idx = keymap.get(key if not isinstance(key, str) else key.lower())
                if idx is None:
                    raise XBloomError(f"unknown slot key {key!r} (use 0/1/2 or A/B/C)")
                out[idx] = recipe
            if any(r is None for r in out):
                raise XBloomError("save_slots needs all three slots (A, B and C)")
            return [r for r in out if r is not None]
        seq = list(recipes)
        if len(seq) != 3:
            raise XBloomError(f"save_slots needs exactly 3 recipes (A, B, C); got {len(seq)}")
        return seq

    @staticmethod
    def _normalize_scale(scale: bool | Sequence[bool]) -> list[bool]:
        """Expand ``scale`` to a per-slot [A, B, C] list of bools."""
        if isinstance(scale, bool):
            return [scale, scale, scale]
        vals = [bool(s) for s in scale]
        if len(vals) != 3:
            raise XBloomError(f"scale sequence must have 3 entries; got {len(vals)}")
        return vals

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
        on_event: Callable[[StatusEvent], Awaitable[None] | None],
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
