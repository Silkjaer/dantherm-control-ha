import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
from aiohttp import ServerDisconnectedError  # noqa: E402
from hch_passivelink import config_flow, controller_api  # noqa: E402
from hch_passivelink.const import (  # noqa: E402
    CONF_CONTROLLER_HOST, CONF_CONTROLLER_PORT, CONF_CONTROLLER_TOKEN,
    CONF_SMART_INPUT_VALID_FOR, CONF_SMART_ROOMS_ENABLED,
)
from hch_passivelink.controller_api import ControllerApiClient  # noqa: E402

STATE = {"mode": "local_auto", "max_level": 4}
HTML = "<html><body><h1>502 Bad Gateway</h1></body></html>"


class Response:
    def __init__(self, status, text):
        self.status, self.text = status, text

    async def json(self, content_type=None):
        # Like aiohttp: an empty body is None, anything else must parse.
        return json.loads(self.text) if self.text.strip() else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    """Answers each request with the next (status, body), or raises it."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = 0

    def request(self, method, url, **kwargs):
        self.requests += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Response(*reply)


def client_for(session, update=lambda _: None):
    return ControllerApiClient(session, "127.0.0.1", 8088, "secret", update)


@pytest.mark.parametrize("status, text", [
    (502, HTML),       # a proxy's error page
    (200, HTML),       # not JSON at all
    (500, "[1, 2]"),   # JSON, but no object to read an error from
    (200, "[1, 2]"),
    (503, ""),
])
def test_a_reply_that_is_no_json_object_is_a_connection_error(status, text):
    client = client_for(Session((status, text)))
    with pytest.raises(ConnectionError):
        asyncio.run(client.async_get_state())
    assert client.connected is False


def test_the_controllers_own_error_text_is_kept():
    client = client_for(Session((401, '{"error": "invalid token"}')))
    with pytest.raises(ConnectionError, match="invalid token"):
        asyncio.run(client.async_get_state())


def test_aiohttp_errors_become_connection_errors():
    client = client_for(Session(ServerDisconnectedError()))
    with pytest.raises(ConnectionError):
        asyncio.run(client.async_get_state())


def test_polling_survives_bad_replies_and_reports_the_lost_controller(monkeypatch):
    states, lost = [], []
    session = Session((200, json.dumps(STATE)), (502, HTML), (200, "[]"), (200, json.dumps(STATE)))
    client = client_for(session, states.append)
    client._on_disconnect = lambda: lost.append(client.connected)
    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        if not session.replies:
            client._stopped = True
        await real_sleep(0)

    monkeypatch.setattr(controller_api.asyncio, "sleep", no_wait)
    asyncio.run(client.run())
    assert session.requests == 4
    # Told once, when it went from connected to lost, and already marked lost.
    assert lost == [False]
    assert states == [STATE, STATE]
    assert client.connected is True


def test_options_flow_shows_cannot_connect_for_an_html_reply():
    entry = SimpleNamespace(data={"connection_type": "tcp", "host": "192.168.1.200", "port": 4196}, options={})
    flow = config_flow.PassiveLinkOptionsFlow(entry)
    flow.hass = SimpleNamespace()
    user_input = {
        CONF_CONTROLLER_HOST: "127.0.0.1",
        CONF_CONTROLLER_PORT: 8088,
        CONF_CONTROLLER_TOKEN: "secret",
        CONF_SMART_ROOMS_ENABLED: False,
        CONF_SMART_INPUT_VALID_FOR: 180,
    }
    with patch.object(config_flow, "async_get_clientsession", lambda hass: Session((502, HTML))):
        result = asyncio.run(flow.async_step_controller(user_input))
    assert result["type"] == "form"
    assert result["step_id"] == "controller"
    assert result["errors"] == {"base": "controller_cannot_connect"}
