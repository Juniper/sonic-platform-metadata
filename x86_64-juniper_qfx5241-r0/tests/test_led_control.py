"""Unit tests for QFX5241 led_control.py plugin (sysfs-based, 64 OSFP + 2 SFP)."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import patch, mock_open

import pytest


PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


def _stub_dependencies():
    fake_led_pkg = types.ModuleType("sonic_led")
    fake_led_sub = types.ModuleType("sonic_led.led_control_base")

    class FakeLedControlBase:
        pass

    fake_led_sub.LedControlBase = FakeLedControlBase
    fake_led_pkg.led_control_base = fake_led_sub
    sys.modules["sonic_led"] = fake_led_pkg
    sys.modules["sonic_led.led_control_base"] = fake_led_sub


def _load_led_module():
    _stub_dependencies()
    spec = importlib.util.spec_from_file_location(
        "qfx5241_led_control", str(PLUGIN_DIR / "led_control.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLedControl:
    def setup_method(self):
        self.mod = _load_led_module()
        self.ctrl = self.mod.LedControl()

    def test_led_color_constants(self):
        assert self.mod.LedControl.LED_COLOR_OFF == 0
        assert self.mod.LedControl.LED_COLOR_GREEN == 2
        assert self.mod.LedControl.LED_COLOR_RED == 1
        assert self.mod.LedControl.LED_COLOR_AMBER == 3

    def test_osfp_port_mapping(self):
        # 64 OSFP ports, 8 lanes each = Ethernet0-511
        assert self.ctrl.portMapping["Ethernet0"] == 0
        assert self.ctrl.portMapping["Ethernet7"] == 0
        assert self.ctrl.portMapping["Ethernet8"] == 1
        assert self.ctrl.portMapping["Ethernet504"] == 63

    def test_sfp_port_mapping(self):
        # SFP ports at Ethernet512 and Ethernet513
        assert "Ethernet512" in self.ctrl.portMapping
        assert "Ethernet513" in self.ctrl.portMapping
        assert self.ctrl.portMapping["Ethernet512"] == 64
        assert self.ctrl.portMapping["Ethernet513"] == 65

    def test_total_port_count(self):
        # 64*8 OSFP lanes + 2 SFP = 514 port entries
        assert len(self.ctrl.portMapping) == 64 * 8 + 2

    def test_sysfs_path_format(self):
        assert self.mod.LedControl.LED_SYSFS_PATH == "/sys/class/leds/fpc0-port{0}/brightness"

    def test_port_link_state_change_up_osfp(self):
        m = mock_open()
        with patch("builtins.open", m):
            self.ctrl.port_link_state_change("Ethernet0", "up")
        m().write.assert_called_with(str(self.mod.LedControl.LED_COLOR_GREEN))

    def test_port_link_state_change_down(self):
        m = mock_open()
        with patch("builtins.open", m):
            self.ctrl.port_link_state_change("Ethernet0", "down")
        m().write.assert_called_with(str(self.mod.LedControl.LED_COLOR_OFF))

    def test_port_link_state_change_sfp_green(self):
        """SFP ports (Ethernet512/513) use LED_COLOR_SFP_GREEN."""
        m = mock_open()
        with patch("builtins.open", m):
            self.ctrl.port_link_state_change("Ethernet512", "up")
        m().write.assert_called_with(str(self.mod.LedControl.LED_COLOR_SFP_GREEN))

    def test_unknown_port_ignored(self):
        m = mock_open()
        with patch("builtins.open", m):
            self.ctrl.port_link_state_change("Ethernet9999", "up")
        m.assert_not_called()
