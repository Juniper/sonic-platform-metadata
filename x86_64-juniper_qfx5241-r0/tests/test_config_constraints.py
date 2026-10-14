"""Unit tests for QFX5241 config_constraints.py plugin."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import patch, MagicMock

import pytest


PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


def _stub_dependencies():
    # Stub swsscommon
    fake_swss = types.ModuleType("swsscommon")
    fake_swss_inner = types.ModuleType("swsscommon.swsscommon")

    class FakeConfigDBConnector:
        def __init__(self):
            pass

        def connect(self):
            pass

        def get_table(self, table_name):
            return {}

    fake_swss_inner.ConfigDBConnector = FakeConfigDBConnector
    fake_swss.swsscommon = fake_swss_inner
    sys.modules["swsscommon"] = fake_swss
    sys.modules["swsscommon.swsscommon"] = fake_swss_inner

    # Stub sonic_platform
    fake_sp = types.ModuleType("sonic_platform")
    fake_sp_platform = types.ModuleType("sonic_platform.platform")

    class FakeChassis:
        def get_hwsku(self):
            return "Juniper-QFX5241-64-OD"

    class FakePlatform:
        def get_chassis(self):
            return FakeChassis()

    fake_sp_platform.Platform = FakePlatform
    fake_sp.platform = fake_sp_platform
    sys.modules["sonic_platform"] = fake_sp
    sys.modules["sonic_platform.platform"] = fake_sp_platform


def _load_config_constraints_module():
    _stub_dependencies()
    spec = importlib.util.spec_from_file_location(
        "qfx5241_config_constraints", str(PLUGIN_DIR / "config_constraints.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHelperFunctions:
    def setup_method(self):
        self.mod = _load_config_constraints_module()

    def test_get_fp_port(self):
        assert self.mod.get_fp_port("Ethernet0") == "0"
        assert self.mod.get_fp_port("Ethernet48") == "48"
        assert self.mod.get_fp_port("Ethernet512") == "512"

    def test_get_port_pair_OD(self):
        # Port 0 (fp_port=0/8=0, 0%4=0) -> pair=0+3=3 -> Ethernet24
        assert self.mod.get_port_pair_OD("Ethernet0") == "Ethernet24"
        # Port 24 (fp_port=24/8=3, 3%4=3) -> pair=3-3=0 -> Ethernet0
        assert self.mod.get_port_pair_OD("Ethernet24") == "Ethernet0"
        # Port 8 (fp_port=8/8=1, 1%4=1) -> pair=1+1=2 -> Ethernet16
        assert self.mod.get_port_pair_OD("Ethernet8") == "Ethernet16"
        # Port 16 (fp_port=16/8=2, 2%4=2) -> pair=2-1=1 -> Ethernet8
        assert self.mod.get_port_pair_OD("Ethernet16") == "Ethernet8"

    def test_get_port_pair_QD(self):
        # Port 0 (fp_port=0, 0%4=0) -> pair=0+1=1 -> Ethernet8
        assert self.mod.get_port_pair_QD("Ethernet0") == "Ethernet8"
        # Port 8 (fp_port=1, 1%4=1) -> pair=1-1=0 -> Ethernet0
        assert self.mod.get_port_pair_QD("Ethernet8") == "Ethernet0"
        # Port 16 (fp_port=2, 2%4=2) -> pair=2+1=3 -> Ethernet24
        assert self.mod.get_port_pair_QD("Ethernet16") == "Ethernet24"
        # Port 24 (fp_port=3, 3%4=3) -> pair=3-1=2 -> Ethernet16
        assert self.mod.get_port_pair_QD("Ethernet24") == "Ethernet16"


class TestBreakout:
    def setup_method(self):
        self.mod = _load_config_constraints_module()

    def test_breakout_returns_false_when_no_breakout_cfg(self):
        """Empty BREAKOUT_CFG table should return False."""
        with patch.object(
            sys.modules["swsscommon.swsscommon"].ConfigDBConnector,
            "get_table", return_value={}
        ):
            result = self.mod.breakout("Ethernet0", "2x200G", None)
            assert result is False

    def test_breakout_allowed_when_sum_under_limit(self):
        """Sum of target + pair breakout lanes <= 10 should return True."""
        fake_table = {
            "Ethernet24": {"brkout_mode": "2x200G"},
        }
        with patch.object(
            sys.modules["swsscommon.swsscommon"].ConfigDBConnector,
            "get_table", return_value=fake_table
        ):
            # target=4x100G (4) + pair=2x200G (2) = 6 <= 10
            result = self.mod.breakout("Ethernet0", "4x100G", None)
            assert result is True

    def test_breakout_denied_when_sum_exceeds_limit(self):
        """Sum of target + pair breakout lanes > 10 should return False."""
        fake_table = {
            "Ethernet24": {"brkout_mode": "8x50G"},
        }
        with patch.object(
            sys.modules["swsscommon.swsscommon"].ConfigDBConnector,
            "get_table", return_value=fake_table
        ):
            # target=8x25G (8) + pair=8x50G (8) = 16 > 10
            result = self.mod.breakout("Ethernet0", "8x25G", None)
            assert result is False

    def test_breakout_uses_qd_pairing_for_qd_hwsku(self):
        """When hwsku is QD, should use get_port_pair_QD."""
        # Override chassis to return QD hwsku
        class FakeChassisQD:
            def get_hwsku(self):
                return "Juniper-QFX5241-64-QD"

        class FakePlatformQD:
            def get_chassis(self):
                return FakeChassisQD()

        sys.modules["sonic_platform.platform"].Platform = FakePlatformQD

        fake_table = {
            # QD pair for Ethernet0 is Ethernet8
            "Ethernet8": {"brkout_mode": "1x400G"},
        }
        with patch.object(
            sys.modules["swsscommon.swsscommon"].ConfigDBConnector,
            "get_table", return_value=fake_table
        ):
            # target=1x400G (1) + pair=1x400G (1) = 2 <= 10
            result = self.mod.breakout("Ethernet0", "1x400G", None)
            assert result is True
