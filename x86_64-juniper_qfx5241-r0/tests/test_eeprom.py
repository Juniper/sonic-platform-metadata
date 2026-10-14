"""Unit tests for QFX5241 eeprom.py plugin."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import patch

import pytest


PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


def _stub_sonic_eeprom():
    fake_pkg = types.ModuleType("sonic_eeprom")
    fake_sub = types.ModuleType("sonic_eeprom.eeprom_tlvinfo")

    class FakeTlvInfoDecoder:
        _TLV_INFO_MAX_LEN = 256

        def __init__(self, *a, **kw):
            pass

    fake_sub.TlvInfoDecoder = FakeTlvInfoDecoder
    fake_pkg.eeprom_tlvinfo = fake_sub
    sys.modules["sonic_eeprom"] = fake_pkg
    sys.modules["sonic_eeprom.eeprom_tlvinfo"] = fake_sub


def _load_eeprom_module():
    _stub_sonic_eeprom()
    spec = importlib.util.spec_from_file_location(
        "qfx5241_eeprom", str(PLUGIN_DIR / "eeprom.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eeprom_path_constant():
    mod = _load_eeprom_module()
    assert mod.EEPROM_PATH == "/sys/bus/i2c/devices/0-0051/eeprom"


def test_board_raises_when_eeprom_missing():
    mod = _load_eeprom_module()
    with patch("os.path.exists", return_value=False):
        with pytest.raises(RuntimeError, match="No syseeprom found"):
            mod.board("board", "/tmp", "/tmp", True)


def test_board_init_sets_eeprom_path():
    mod = _load_eeprom_module()
    with patch("os.path.exists", return_value=True):
        b = mod.board("board", "/tmp", "/tmp", True)
        assert b.eeprom_path == mod.EEPROM_PATH


def test_board_tlv_info_max_len():
    mod = _load_eeprom_module()
    assert mod.board._TLV_INFO_MAX_LEN == 256
