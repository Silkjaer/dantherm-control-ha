"""Display names after the rename to Dantherm Control: device names and entry titles."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))

import const  # noqa: E402


def test_main_device_is_named_after_the_modbus_model():
    assert const.main_device_name(SimpleNamespace(model="RCV320 P2")) == "Dantherm RCV320 P2"
    assert const.main_device_name(SimpleNamespace(model="HCH 5 MKII")) == "Dantherm HCH 5 MKII"
    assert const.main_device_name(SimpleNamespace(model="Dantherm type 99")) == "Dantherm type 99"
    assert const.main_device_model(SimpleNamespace(model="RCV320 P2")) == "RCV320 P2"


def test_mk1_main_device_keeps_its_name():
    assert const.main_device_name(None) == "Dantherm HCH5 Control"
    assert const.main_device_model(None) == "HCH5 MK1 + HAC1"


def test_only_untouched_default_titles_are_renamed():
    assert const.DEFAULT_NAME == "Dantherm Control"
    assert const.renamed_entry_title("Dantherm HCH5 Control") == "Dantherm Control"
    assert const.renamed_entry_title("Dantherm HCH PassiveLink") == "Dantherm Control"
    assert const.renamed_entry_title("Dantherm HCH5 Control (Dantherm type 21)", "RCV320 P2") == "Dantherm Control (RCV320 P2)"
    assert const.renamed_entry_title("Dantherm HCH5 Control (HCH 5 MKII)", "HCH 5 MKII") == "Dantherm Control (HCH 5 MKII)"
    # Already renamed, or chosen by the user.
    assert const.renamed_entry_title("Dantherm Control") is None
    assert const.renamed_entry_title("Dantherm Control (RCV320 P2)", "RCV320 P2") is None
    assert const.renamed_entry_title("Ventilation") is None
    assert const.renamed_entry_title("Dantherm HCH5 Control (attic)", "RCV320 P2") is None
    assert const.renamed_entry_title("Dantherm HCH5 Control (attic)") is None


def test_existing_devices_keep_their_registry_name():
    assert const.kept_device_name("Dantherm HCH5 Control", "Dantherm RCV320 P2") == "Dantherm HCH5 Control"
    assert const.kept_device_name("Dantherm HCH5 controller", const.CONTROLLER_DEVICE_NAME) == "Dantherm HCH5 controller"
    assert const.kept_device_name(None, "Dantherm RCV320 P2") == "Dantherm RCV320 P2"
    assert const.kept_device_name(None, const.CONTROLLER_DEVICE_NAME) == "Dantherm Control controller"
