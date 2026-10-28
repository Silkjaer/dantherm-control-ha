import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
from hch_passivelink import config_flow  # noqa: E402
from hch_passivelink.const import (  # noqa: E402
    CONF_CONTROLLER_API_ENABLED, CONF_CONTROLLER_HOST, CONF_CONTROLLER_PORT, CONF_CONTROLLER_TOKEN,
)

CONTROLLER = {
    CONF_CONTROLLER_API_ENABLED: True,
    CONF_CONTROLLER_HOST: "127.0.0.1",
    CONF_CONTROLLER_PORT: 8088,
    CONF_CONTROLLER_TOKEN: "secret",
}


def make_flow(options):
    entry = SimpleNamespace(data={"connection_type": "tcp", "host": "192.168.1.200", "port": 4196}, options=options)
    flow = config_flow.PassiveLinkOptionsFlow(entry)
    flow.hass = SimpleNamespace()
    return flow


class FakeClient:
    fail = False

    def __init__(self, *args, **kwargs):
        self.args = args

    async def async_probe(self):
        if FakeClient.fail:
            raise ConnectionError("down")
        return {"mode": "local_auto", "max_level": 4}


def run(coro):
    return asyncio.run(coro)


def test_menu_offers_the_controller_shortcut_once_the_api_is_set_up():
    result = run(make_flow(dict(CONTROLLER)).async_step_init())
    assert result["type"] == "menu"
    assert result["menu_options"] == ["controller_quick", "connection", "controller"]

    result = run(make_flow({}).async_step_init())
    assert result["menu_options"] == ["connection", "controller"]


def test_shortcut_opens_the_controller_menu_with_saved_settings():
    flow = make_flow(dict(CONTROLLER))
    run(flow.async_step_init())
    FakeClient.fail = False
    with patch.object(config_flow, "ControllerApiClient", FakeClient), \
            patch.object(config_flow, "async_get_clientsession", lambda hass: None):
        result = run(flow.async_step_controller_quick())
    assert result["type"] == "menu"
    assert result["step_id"] == "controller_menu"
    # Saving from here keeps the connection that was never shown.
    assert flow._pending["host"] == "192.168.1.200"
    assert flow._pending[CONF_CONTROLLER_TOKEN] == "secret"


def test_shortcut_shows_the_api_page_when_the_saved_key_fails():
    flow = make_flow(dict(CONTROLLER))
    run(flow.async_step_init())
    FakeClient.fail = True
    with patch.object(config_flow, "ControllerApiClient", FakeClient), \
            patch.object(config_flow, "async_get_clientsession", lambda hass: None):
        result = run(flow.async_step_controller_quick())
    assert result["type"] == "form"
    assert result["step_id"] == "controller"
    assert result["errors"] == {"base": "controller_cannot_connect"}
