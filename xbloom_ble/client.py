"""Async Bluetooth LE client for the xBloom Studio (via ``bleak``).

This is the only module that touches hardware. It discovers the machine,
connects, writes the LOAD frames, and streams status telemetry.

Safety model: loading and starting are **separate, explicit** operations.
:meth:`XBloomClient.load_recipe` only *loads* (writes ``a4, a6, a8, 41`` and
returns once the machine is armed at STATE ``0x1f``) — it never starts a brew, so
a load can never brew by accident. :meth:`XBloomClient.start` is the deliberate
"go": it sends commit (``0x42``) + start (``0x46``) to launch the brew remotely,
exactly like the app's Brew button. :meth:`XBloomClient.brew` is the convenience
that loads then starts. :meth:`XBloomClient.cancel_brew` aborts (``0x47``).

⚠️ Starting a brew physically dispenses near-boiling water — only call
:meth:`start`/:meth:`brew` when the machine is ready and someone intends to brew.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

from .protocol import (
    build_cancel,
    build_commit,
    build_load_frames,
    build_save_slot,
    build_session_start,
    build_set_mode,
    build_start,
    build_status_query,
)
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
# Brew lifecycle states: 0x1e awaiting-confirm (after commit), 0x3b brewing.
STATE_AWAITING_CONFIRM = 0x1E
STATE_BREWING = 0x3B
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
        raw = bytes(data)
        event = parse_notification(raw)
        # Full raw chatter at DEBUG (enable with `--debug`) — this is how we capture
        # the brew-record frames we don't parse yet, so they can be decoded later.
        log.debug("← %s%s", raw.hex(), f"  [{event.state_name}]" if event is not None else "")
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
    async def load_recipe(self, recipe: Recipe, *, settle: float = 2.0) -> StatusEvent:
        """Load ``recipe`` onto the machine and return once it is armed.

        Writes the LOAD frames to ``ffe1`` — ``a4`` (session start), a ``0x56``
        status handshake, then ``a6`` (dose), ``a8`` (temps) and the pours frame
        (``0x41``, or ``0x44`` for a no-grind recipe) — waiting for each ACK on
        ``ffe2``, and returns the ``StatusEvent`` once the machine reaches STATE
        ``0x1f`` (armed / loaded). **This never starts a brew** — the human
        approves on the machine.

        ``settle`` (seconds) is the pause after ``a4``+``0x56`` to let the machine
        leave its post-connect transitional state before staging. On a fresh
        connection the machine will not arm if the dose/temps/pours frames are sent
        immediately — it needs the handshake + this settle first (verified on
        hardware; this is the fix for the previous "loads never arm" issue).
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        recipe.validate()
        # frames == [a4, a6, a8, pours]; the pours opcode is chosen by build_load_frames.
        frames = build_load_frames(recipe.to_protocol_dict())
        a4, load_frames = frames[0], frames[1:]

        await self._start_notify()
        try:
            # The command characteristic (ffe1) accepts ONLY a Write Command (ATT
            # 0x52, write-without-response); ACKs and status arrive as ffe2
            # notifications, which accumulate in self._notif_queue and are read by
            # _drain_until_state below. We pace the writes with small fixed delays
            # rather than round-tripping each ACK: the machine needs the frames
            # spaced out, and consuming ACKs off the queue here would race the state
            # wait. (Verified on hardware — this is the fix for "loads never arm".)
            # 1. Session start + status handshake, then let the machine settle out of
            #    its transitional post-connect state before staging.
            log.info("→ a4 (session start) + 0x56 (handshake), then settle %.1fs", settle)
            await self._client.write_gatt_char(CHAR_COMMAND, a4, response=False)
            await asyncio.sleep(0.5)
            await self._client.write_gatt_char(CHAR_COMMAND, build_status_query(), response=False)
            await asyncio.sleep(settle)
            # 2. Dose, temps, pours — the pours frame drives the machine to armed.
            for i, frame in enumerate(load_frames):
                log.info("→ load frame %d/%d (cmd=0x%02x)", i + 2, len(load_frames) + 1, frame[3])
                await self._client.write_gatt_char(CHAR_COMMAND, frame, response=False)
                await asyncio.sleep(0.4)
            armed = await self._drain_until_state(STATE_ARMED, self.ack_timeout)
            log.info("recipe loaded — machine armed (awaiting human approval)")
            return armed
        finally:
            await self._stop_notify()

    # ------------------------------------------------------------------
    # Starting / cancelling a brew  (explicit — dispenses hot water)
    # ------------------------------------------------------------------
    async def start(self, *, settle: float = 6.0) -> StatusEvent:
        """Start the currently-armed brew: commit (``0x42``) then start (``0x46``).

        The machine must already be armed (call :meth:`load_recipe` first). Sends
        commit, waits (up to ``settle``) for the machine to reach ``0x1e``
        (awaiting-confirm, with its countdown) as the app does, then sends start.

        It then makes a **best-effort** attempt to observe ``0x3b`` (brewing), but
        **never raises if it doesn't** — once commit+start are on the wire the brew
        is running, and the machine typically blows straight past ``0x3b`` into
        brew-record frames. The caller should stream telemetry for the live state;
        blocking here (and failing) would abandon a brew that is actually underway.

        ⚠️ This physically dispenses near-boiling water. Only call it when the
        machine is ready (water tank filled, dripper/cup in place) and someone
        intends to brew — starting is never triggered as a side effect of loading.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        await self._start_notify()
        try:
            log.info("→ 0x42 commit (arming the brew)")
            await self._client.write_gatt_char(CHAR_COMMAND, build_commit(), response=False)
            # Wait for awaiting-confirm before the start frame (the app does too).
            try:
                await self._drain_until_state(STATE_AWAITING_CONFIRM, settle)
            except XBloomError:
                log.info("awaiting-confirm not observed; sending start anyway")
            await asyncio.sleep(0.5)
            log.info("→ 0x46 start (brew go)")
            await self._client.write_gatt_char(CHAR_COMMAND, build_start(), response=False)
            # Best-effort observe brewing — but the brew is already commanded, so a
            # miss here is NOT an error (do not abandon a running brew).
            try:
                brewing = await self._drain_until_state(STATE_BREWING, 4.0)
                log.info("brew started (brewing)")
                return brewing
            except XBloomError:
                log.info("brew started (commit+start sent) — streaming telemetry for live state")
                return StatusEvent(state=STATE_BREWING, state_name="brewing", raw=b"")
        finally:
            await self._stop_notify()

    async def brew(self, recipe: Recipe, *, settle: float = 2.0) -> StatusEvent:
        """Load ``recipe`` and immediately start brewing (load + :meth:`start`).

        Convenience for the app-style "tap and brew" flow: it stages the recipe
        (arming the machine) and then sends commit + start. ⚠️ Same hot-water
        caveat as :meth:`start` — it brews for real.
        """
        await self.load_recipe(recipe, settle=settle)
        return await self.start()

    async def cancel_brew(self) -> None:
        """Abort a committed/running brew (``0x47`` cancel), returning toward idle."""
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")
        log.info("→ 0x47 cancel (aborting brew)")
        await self._client.write_gatt_char(CHAR_COMMAND, build_cancel(), response=False)

    async def save_slots(
        self,
        recipes: Sequence[Recipe] | Mapping[object, Recipe],
        *,
        scale: bool | Sequence[bool] = True,
        ensure_pro: bool = True,
        end_in_auto: bool = True,
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
            # 1. Open a session (a4), then force PRO mode. Slot writes are ONLY accepted in
            #    PRO mode: AUTO mode (the on-machine A/B/C selector) parks the machine in
            #    status 0x41 and rejects writes (RETRY); PRO mode drops it to 0x01 (idle),
            #    where saves land. Sending PRO is what makes the idle wait below reliable.
            await self._client.write_gatt_char(
                CHAR_COMMAND, build_session_start(), response=False
            )
            if ensure_pro:
                log.info("→ set PRO mode (slot writes require it)")
                await self._client.write_gatt_char(
                    CHAR_COMMAND, build_set_mode(pro=True), response=False
                )
            try:
                await self._drain_until_state(STATE_IDLE, self.ack_timeout)
            except XBloomError:
                log.warning("machine idle not confirmed; proceeding (is it in AUTO mode?)")
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

            # 4. Return the machine to AUTO mode so the freshly-written A/B/C presets are
            #    ready to pick on the dial (that's how they're brewed).
            if end_in_auto:
                log.info("→ back to AUTO mode (presets ready on the dial)")
                await self._client.write_gatt_char(
                    CHAR_COMMAND, build_set_mode(pro=False), response=False
                )
                await asyncio.sleep(0.3)
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
