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
# Brew lifecycle states: 0x22 starting/grinding, 0x3b brewing. On some machines commit
# auto-proceeds through these; on others the machine waits in awaiting-confirm (0x1e)
# and needs the 0x46 start frame.
STATE_STARTING = 0x22
STATE_BREWING = 0x3B
# Machine-refused states (it checks water/beans right after commit, before pouring).
STATE_NO_WATER = 0x0C
STATE_NO_BEANS = 0x0F
# Slot-save status states (see telemetry): 0x43 saving, 0x25 saved, 0x01 idle.
STATE_IDLE = 0x01
STATE_SLOTS_SAVED = 0x25


class XBloomError(RuntimeError):
    """Raised on BLE / protocol errors in the client."""


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
        # Held-session state (see open_session): once a session is open we keep the
        # ffe2 subscription up so the machine shows "connected", but we only *queue*
        # notifications while an operation is actively consuming them (``_consuming``)
        # — otherwise the machine's continuous idle stream would grow the queue forever.
        self._subscribed = False       # ffe2 notify subscription is active
        self._session_active = False   # hold the subscription across operations
        self._consuming = False        # an operation wants frames queued right now

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        """True while the underlying BLE link is up (for held-connection callers)."""
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        from bleak import BleakClient

        # Idempotent: a held-connection caller (e.g. the TUI) may call connect() to
        # "ensure connected" — if the link is already up, this is a fast no-op rather
        # than leaking a second BleakClient.
        if self.is_connected:
            return
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
        self._subscribed = False
        self._session_active = False
        self._consuming = False

    async def open_session(self, *, settle: float = 0.3) -> None:
        """Register as an app-style session so the machine shows it's **connected**.

        Mirrors exactly what the phone app does the moment it connects (verified from
        the HCI capture): subscribe to ffe2 status notifications, then write the
        ``a4`` session-start frame. The machine responds by streaming status and
        lighting its paired/connected icon, and the session is **held** — the ffe2
        subscription stays up across brews (idle frames are dropped, see
        :meth:`_on_notify`) so the link stays warm and no per-brew re-handshake is
        needed. The app sends no periodic keepalive, so neither do we.

        This is a session handshake, **not** a brew: ``a4`` only opens a session and
        never dispenses water (the brew opcodes ``0x42``/``0x46`` live only in
        :meth:`start`). Safe to call on every connect; idempotent-ish (re-sending
        ``a4`` is harmless).
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")
        self._session_active = True
        await self._ensure_subscribed()
        log.info("→ a4 (open session — machine shows connected)")
        await self._client.write_gatt_char(CHAR_COMMAND, build_session_start(), response=False)
        await asyncio.sleep(settle)

    async def close_session(self) -> None:
        """Drop the held session (stop holding the ffe2 subscription). The BLE link
        itself stays up until :meth:`disconnect`."""
        self._session_active = False
        await self._stop_notify()

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
        if event is None:
            return
        # Only queue while an operation is consuming. During an idle held session the
        # machine streams status continuously (heartbeats, scale, idle-state frames) —
        # dropping those here keeps the queue bounded instead of growing unbounded.
        if self._consuming:
            self._notif_queue.put_nowait(event)

    async def _ensure_subscribed(self) -> None:
        """Subscribe to ffe2 status notifications (idempotent)."""
        assert self._client is not None
        if self._subscribed:
            return
        await self._client.start_notify(CHAR_STATUS, self._on_notify)
        self._subscribed = True

    async def _start_notify(self) -> None:
        # Ensure we're listening, start with a clean queue (drop any idle-session
        # backlog), and mark that this operation wants frames.
        await self._ensure_subscribed()
        while not self._notif_queue.empty():
            self._notif_queue.get_nowait()
        self._consuming = True

    async def _stop_notify(self) -> None:
        # Operation finished consuming. Keep the subscription up if a session is held
        # (so the machine stays "connected"); otherwise tear it down.
        self._consuming = False
        if self._session_active:
            return
        if self._subscribed and self._client is not None and self._client.is_connected:
            try:
                await self._client.stop_notify(CHAR_STATUS)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        self._subscribed = False

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
    async def _drain_for_any(self, states: set[int], timeout: float) -> StatusEvent | None:
        """Return the first status event whose state is in ``states``, or ``None`` on
        timeout. Skips heartbeats; consumes intervening frames."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(self._notif_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if event.is_heartbeat:
                continue
            log.debug("status: %s", event.state_name)
            if event.state in states:
                return event

    async def start(self, *, settle: float = 8.0) -> StatusEvent:
        """Start the currently-armed brew (call :meth:`load_recipe` first).

        Sends commit (``0x42``) and then **adapts to the machine**: after commit some
        machines auto-proceed straight through awaiting-confirm → grinding → brewing,
        while others sit in awaiting-confirm waiting for a start press. So we *watch*
        for up to ``settle`` seconds:

        * If the machine reaches **grinding (0x22)** or **brewing (0x3b)** on its own,
          the brew is underway — we do **not** send ``0x46`` (sending it into a running
          brew aborts it back to armed — verified on hardware).
        * Only if it **stalls in awaiting-confirm** do we send the ``0x46`` start frame
          to nudge it (this is what the vendor app's capture needed).

        Returns best-effort once brewing/grinding is seen; never raises just because a
        state wasn't observed (the caller streams telemetry for the live state).

        ⚠️ This physically dispenses near-boiling water. Only call it when the machine
        is ready (water/beans/cup in) and someone intends to brew.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        await self._start_notify()
        try:
            log.info("→ 0x42 commit (start the brew)")
            await self._client.write_gatt_char(CHAR_COMMAND, build_commit(), response=False)
            # After commit the machine either acts (auto-proceeds to grinding/brewing, or
            # refuses with no-water/no-beans), or just sits in awaiting-confirm. In ANY
            # "acted" case we must NOT send 0x46 — sending it into a running brew aborts
            # it, and it's pointless on a refusal. Only nudge with 0x46 if it stalls.
            acted = {STATE_STARTING, STATE_BREWING, STATE_NO_WATER, STATE_NO_BEANS}
            ev = await self._drain_for_any(acted, settle)
            if ev is not None:
                log.info("machine acted on commit (%s) — not sending 0x46", ev.state_name)
                return ev
            # It stalled in awaiting-confirm — nudge it with the start frame.
            log.info("machine waiting in confirm — → 0x46 start")
            await self._client.write_gatt_char(CHAR_COMMAND, build_start(), response=False)
            ev = await self._drain_for_any(acted, 5.0)
            if ev is not None:
                log.info("brew started (%s)", ev.state_name)
                return ev
            log.info("start sent — streaming telemetry for live state")
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

    # ------------------------------------------------------------------
    # Telemetry streaming
    # ------------------------------------------------------------------
    def _on_aux_notify(self, _sender, data: bytearray) -> None:
        """Log-only handler for the ``ffe3`` aux characteristic (capture/diagnostic).

        The live scale weights the app shows are NOT on ``ffe2`` (that carries only
        state + a pour counter). They may stream on ``ffe3`` — this taps it purely to
        capture the raw bytes at DEBUG so the format can be decoded. It never feeds the
        telemetry event stream and never affects the brew.
        """
        log.debug("←aux %s", bytes(data).hex())

    async def stream_telemetry(
        self,
        on_event: Callable[[StatusEvent], Awaitable[None] | None],
        duration: float = 300.0,
        *,
        stop_on_terminal: bool = True,
        capture_aux: bool = False,
    ) -> None:
        """Subscribe to ``ffe2`` and invoke ``on_event`` for each status event.

        Runs for up to ``duration`` seconds. If ``stop_on_terminal`` is set,
        returns early once a terminal state (complete / idle) is seen.
        ``on_event`` may be a plain or async callable.

        If ``capture_aux`` is set, ALSO subscribe to the ``ffe3`` aux characteristic
        and log its raw frames at DEBUG (diagnostic only — used with ``--debug`` to
        hunt for the live-scale weight stream). This is best-effort: if ``ffe3`` can't
        be subscribed it's logged and ignored, never breaking the brew.
        """
        if self._client is None or not self._client.is_connected:
            raise XBloomError("not connected")

        await self._start_notify()
        aux_on = False
        if capture_aux:
            try:
                await self._client.start_notify(CHAR_AUX, self._on_aux_notify)
                aux_on = True
                log.debug("aux capture on (ffe3) — hunting for the live-weight stream")
            except Exception as exc:  # noqa: BLE001 - diagnostic tap, never fatal
                log.debug("aux capture unavailable: %s", exc)
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
            if aux_on:
                try:
                    await self._client.stop_notify(CHAR_AUX)
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass
            await self._stop_notify()
