"""Machine controllers for the TUI — the seam that keeps the UI hardware-free.

The UI talks only to a :class:`MachineController`. Two implementations:

* :class:`RealController` — drives the real machine over BLE (wraps ``XBloomClient``).
* :class:`FakeController` — a pure-software simulator that replays a realistic brew
  from a recipe. This is what makes the whole app testable (and ``--demo``-able)
  with no coffee machine attached.

:meth:`stage` only *loads* a recipe (arming the machine); :meth:`start` is the
separate, explicit step that actually launches the brew (commit + start), mirroring
the app's Brew button. Loading never starts a brew on its own — starting is always
a deliberate call. ⚠️ On real hardware :meth:`start` dispenses near-boiling water.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..recipe import Recipe
from ..telemetry import StatusEvent


def _ev(state: int, name: str, water: float | None = None,
        coffee: float | None = None) -> StatusEvent:
    return StatusEvent(state=state, state_name=name, raw=b"", water_g=water, coffee_g=coffee)


class MachineController(ABC):
    """UI-facing machine interface (async)."""

    address: str | None = None

    @abstractmethod
    async def scan(self) -> list[str]:
        """Return discovered machine addresses."""

    @abstractmethod
    async def connect(self, address: str | None = None) -> None:
        """Connect (scanning if no address given)."""

    @abstractmethod
    async def stage(self, recipe: Recipe) -> StatusEvent:
        """Load a recipe and return once the machine is armed. Never starts a brew."""

    @abstractmethod
    async def start(self) -> StatusEvent:
        """Start the armed brew (commit + start). ⚠️ Dispenses hot water on hardware."""

    @abstractmethod
    def telemetry(self, duration: float = 300.0) -> AsyncIterator[StatusEvent]:
        """Async-iterate status/telemetry events (state, water_g, coffee_g)."""

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel an in-progress brew (sends the ``0x47`` cancel opcode)."""

    @abstractmethod
    async def save_slots(self, recipes: list[Recipe]) -> None:
        """Program the machine's A/B/C dial presets (exactly three recipes)."""

    @abstractmethod
    async def disconnect(self) -> None:
        ...


class FakeController(MachineController):
    """A simulated machine. Replays a realistic brew for the staged recipe:
    idle → armed → (human 'approves') → water ramps through the pours → complete.

    ``speed`` scales wall-clock time (1.0 = realtime, small = fast for tests).
    The brew begins when :meth:`start` is called; ``auto_start`` is a fallback
    delay after which the sim starts pouring even if ``start`` was never called
    (so telemetry-only tests still see a brew).
    """

    def __init__(self, *, speed: float = 1.0, auto_start: float = 1.5) -> None:
        self.speed = speed
        self.auto_start = auto_start
        self.address = "FA:KE:00:00:00:00"
        self._recipe: Recipe | None = None
        self._cancelled = False
        self.started = False
        self._start_event: asyncio.Event | None = None
        self.saved_slots: list[Recipe] | None = None

    def _started_event(self) -> asyncio.Event:
        # Created lazily so it binds to the running loop (not import-time).
        if self._start_event is None:
            self._start_event = asyncio.Event()
        return self._start_event

    async def scan(self) -> list[str]:
        await asyncio.sleep(0.05 * self.speed)
        return [self.address]

    async def connect(self, address: str | None = None) -> None:
        await asyncio.sleep(0.1 * self.speed)

    async def stage(self, recipe: Recipe) -> StatusEvent:
        recipe.validate()
        self._recipe = recipe
        self._cancelled = False
        self.started = False
        self._started_event().clear()
        await asyncio.sleep(0.15 * self.speed)
        return _ev(0x1F, "armed", water=0.0, coffee=float(recipe.dose_g))

    async def start(self) -> StatusEvent:
        self.started = True
        self._started_event().set()
        await asyncio.sleep(0.1 * self.speed)
        return _ev(0x3B, "brewing", water=0.0,
                   coffee=float(self._recipe.dose_g) if self._recipe else 0.0)

    async def cancel(self) -> None:
        self._cancelled = True

    async def save_slots(self, recipes: list[Recipe]) -> None:
        if len(recipes) != 3:
            raise ValueError("need exactly 3 recipes for slots A/B/C")
        for r in recipes:
            r.validate()
        self.saved_slots = list(recipes)      # record for inspection/tests
        await asyncio.sleep(0.15 * self.speed)

    async def disconnect(self) -> None:
        pass

    async def telemetry(self, duration: float = 300.0) -> AsyncIterator[StatusEvent]:
        r = self._recipe
        if r is None:
            return
        dose = float(r.dose_g)
        step = 0.2
        yield _ev(0x1F, "armed", water=0.0, coffee=dose)
        # Wait for an explicit start() (the app-style Brew), or fall back to
        # auto_start so telemetry-only callers still see a brew begin.
        try:
            await asyncio.wait_for(self._started_event().wait(), timeout=self.auto_start)
        except asyncio.TimeoutError:
            pass
        if self._cancelled:
            yield _ev(0x01, "cancelled", water=0.0, coffee=dose)
            return
        water = 0.0
        for p in r.pours:
            dur = max(0.4, int(p.ml) / max(0.1, float(p.flow_ml_s)))
            n = max(1, int(dur / step))
            for k in range(1, n + 1):
                if self._cancelled:
                    yield _ev(0x01, "cancelled", water=round(water, 1), coffee=dose)
                    return
                await asyncio.sleep(step * self.speed)
                yield _ev(0x1E, "brewing", water=round(water + int(p.ml) * k / n, 1), coffee=dose)
            water += int(p.ml)
            # a compressed pause between pours (so the graph shows the plateau)
            for _ in range(min(int(p.pause_s), 8)):
                if self._cancelled:
                    yield _ev(0x01, "cancelled", water=round(water, 1), coffee=dose)
                    return
                await asyncio.sleep(step * self.speed)
                yield _ev(0x1E, "brewing", water=round(water, 1), coffee=dose)
        yield _ev(0x41, "complete", water=round(water, 1), coffee=dose)


class RealController(MachineController):
    """Drives the real machine over BLE via ``XBloomClient``."""

    def __init__(self, address: str | None = None) -> None:
        self._addr = address
        self._client = None

    async def scan(self) -> list[str]:
        from ..client import scan
        return [d.address for d in await scan()]

    async def connect(self, address: str | None = None) -> None:
        from ..client import XBloomClient, scan
        addr = address or self._addr
        if not addr:
            found = await scan()
            if not found:
                raise RuntimeError("no xBloom machine found")
            addr = found[0].address
        self.address = addr
        self._client = XBloomClient(addr)
        await self._client.connect()

    async def stage(self, recipe: Recipe) -> StatusEvent:
        if self._client is None:
            raise RuntimeError("not connected")
        return await self._client.load_recipe(recipe)

    async def start(self) -> StatusEvent:
        if self._client is None:
            raise RuntimeError("not connected")
        return await self._client.start()

    async def telemetry(self, duration: float = 300.0) -> AsyncIterator[StatusEvent]:
        if self._client is None:
            raise RuntimeError("not connected")
        queue: asyncio.Queue[StatusEvent] = asyncio.Queue()
        task = asyncio.create_task(
            self._client.stream_telemetry(queue.put_nowait, duration=duration)
        )
        try:
            while not task.done() or not queue.empty():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
        finally:
            task.cancel()

    async def cancel(self) -> None:
        if self._client is None:
            raise RuntimeError("not connected")
        await self._client.cancel_brew()

    async def save_slots(self, recipes: list[Recipe]) -> None:
        if self._client is None:
            raise RuntimeError("not connected")
        await self._client.save_slots(recipes)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
