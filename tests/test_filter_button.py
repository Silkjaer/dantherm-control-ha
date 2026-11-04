import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink import button  # noqa: E402


def filter_button(**coordinator):
    coordinator = SimpleNamespace(available=True, unit_filter_counter=False, client=None,
                                  controller_client=None, data={}, **coordinator)
    batches = []
    entry = SimpleNamespace(runtime_data=coordinator)
    asyncio.run(button.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    [reset] = [e for batch in batches for e in batch if isinstance(e, button.PassiveLinkFilterResetButton)]
    return coordinator, reset


def test_reset_button_works_without_the_unit_counter():
    _, reset = filter_button()
    assert reset.available


def test_reset_button_stands_down_for_the_unit_counter():
    coordinator, reset = filter_button()
    coordinator.unit_filter_counter = True
    assert not reset.available
    coordinator.unit_filter_counter = False
    coordinator.available = False
    assert not reset.available
