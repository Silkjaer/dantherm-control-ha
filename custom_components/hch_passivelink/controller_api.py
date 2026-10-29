"""HTTP client for the Raspberry Pi HCH controller.

Home Assistant is a remote control and sensor source only. The Pi remains the
controller/source of truth and is the only component that may write Modbus.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)


class ControllerApiClient:
    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        token: str,
        update: Callable[[dict[str, object]], None],
        *,
        poll_seconds: float = 5.0,
    ) -> None:
        self.session = session
        self.base_url = f"http://{host}:{int(port)}"
        self.token = token
        self._update = update
        self.poll_seconds = max(2.0, float(poll_seconds))
        self.connected = False
        self._stopped = False
        self.last_error: str | None = None
        # Called when polling loses the controller, so its entities can turn
        # unavailable at once instead of with the next unrelated update.
        self._on_disconnect: Callable[[], None] | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _json(self, method: str, path: str, payload: dict | None = None) -> dict[str, object]:
        # Every failure, including an HTML error page from a proxy, ends up as
        # ConnectionError, so callers and the poll loop handle one kind.
        try:
            async with self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                json=payload,
                timeout=10,
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
                if response.status >= 400:
                    error = body.get("error") if isinstance(body, dict) else None
                    raise ConnectionError(error or f"Controller HTTP {response.status}")
                if not isinstance(body, dict):
                    raise ConnectionError("Controller returned invalid JSON")
                return body
        except ClientError as error:
            raise ConnectionError(f"Controller request failed: {error}") from error

    async def async_get_state(self) -> dict[str, object]:
        state = await self._json("GET", "/api/controller/state")
        self.connected = True
        self.last_error = None
        self._update(state)
        return state

    async def async_command(self, patch: dict[str, object]) -> dict[str, object]:
        if "enabled" in patch:
            raise ValueError("Pi controller cannot be disabled")
        state = await self._json("POST", "/api/controller/command", patch)
        self.connected = True
        self.last_error = None
        self._update(state)
        return state

    async def async_send_rooms(self, rooms: dict[str, dict[str, object]], valid_for_s: int = 180) -> dict[str, object]:
        state = await self._json(
            "POST",
            "/api/controller/inputs",
            {"source": "home_assistant", "valid_for_s": int(valid_for_s), "rooms": rooms},
        )
        self.connected = True
        self.last_error = None
        self._update(state)
        return state

    async def async_send_signals(self, signals: dict[str, object], valid_for_s: int = 300) -> dict[str, object]:
        """Leased external switches, e.g. the automatic fireplace signal."""
        state = await self._json(
            "POST",
            "/api/controller/signals",
            {**signals, "valid_for_s": int(valid_for_s)},
        )
        self.connected = True
        self.last_error = None
        self._update(state)
        return state

    async def async_probe(self) -> dict[str, object]:
        return await self.async_get_state()

    async def run(self) -> None:
        delay = 1.0
        while not self._stopped:
            try:
                await self.async_get_state()
                delay = 1.0
                await asyncio.sleep(self.poll_seconds)
            except (OSError, ClientError, ConnectionError, asyncio.TimeoutError) as error:
                was_connected, self.connected = self.connected, False
                self.last_error = str(error)
                _LOGGER.debug("Controller API reconnect: %s", error)
                if was_connected and self._on_disconnect is not None:
                    self._on_disconnect()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def stop(self) -> None:
        self._stopped = True
