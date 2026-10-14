"""Unit tests for QFX5241 psuutil.py plugin (TMC-based)."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import mock_open, patch

import pytest


PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


def _stub_sonic_psu():
    fake_pkg = types.ModuleType("sonic_psu")
    fake_sub = types.ModuleType("sonic_psu.psu_base")

    class FakePsuBase:
        def __init__(self):
            pass

    fake_sub.PsuBase = FakePsuBase
    fake_pkg.psu_base = fake_sub
    sys.modules["sonic_psu"] = fake_pkg
    sys.modules["sonic_psu.psu_base"] = fake_sub


def _load_psuutil_module():
    _stub_sonic_psu()
    spec = importlib.util.spec_from_file_location(
        "qfx5241_psuutil", str(PLUGIN_DIR / "psuutil.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPsuUtil:
    def setup_method(self):
        self.mod = _load_psuutil_module()
        self.psu = self.mod.PsuUtil()

    def test_get_num_psus_returns_2(self):
        assert self.psu.get_num_psus() == 2

    def test_psu_dir_path(self):
        expected = "/sys/devices/pci0000:00/0000:00:1c.0/0000:0f:00.0/psu-tmc.15"
        assert self.mod.PsuUtil.PSU_DIR == expected

    def test_get_attr_value_returns_err_when_file_missing(self):
        with patch("os.path.isfile", return_value=False):
            assert self.psu.get_attr_value("/nonexistent") == "ERR"

    def test_get_attr_value_reads_file(self):
        with patch("os.path.isfile", return_value=True):
            with patch("builtins.open", mock_open(read_data="1\n")):
                assert self.psu.get_attr_value("/some/path") == 1

    def test_get_psu_status_true(self):
        with patch.object(self.psu, "get_attr_value", return_value=1):
            assert self.psu.get_psu_status(1) is True

    def test_get_psu_status_false(self):
        with patch.object(self.psu, "get_attr_value", return_value=0):
            assert self.psu.get_psu_status(1) is False

    def test_get_psu_presence_true(self):
        with patch.object(self.psu, "get_attr_value", return_value=1):
            assert self.psu.get_psu_presence(2) is True

    def test_get_psu_presence_false(self):
        with patch.object(self.psu, "get_attr_value", return_value=0):
            assert self.psu.get_psu_presence(2) is False
