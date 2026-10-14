"""Unit tests for QFX5241 eeprom_data.py plugin (EEPROM TLV parsing)."""

import importlib.util
import pathlib
import sys
import types
import struct
from unittest.mock import patch, mock_open, MagicMock, call

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


# ---------------------------------------------------------------------------
# Build a valid TLV EEPROM binary image for testing
# ---------------------------------------------------------------------------

def _build_tlv_eeprom():
    """Build a minimal valid TLV EEPROM binary for testing."""
    # TLV header: "TlvInfo\0" + version(1) + total_length(2)
    header = b"TlvInfo\x00\x01"

    tlvs = b""

    # Product Name (0x21)
    name = b"QFX5241-48Y"
    tlvs += struct.pack("BB", 0x21, len(name)) + name

    # Part Number (0x22)
    pn = b"750-123456"
    tlvs += struct.pack("BB", 0x22, len(pn)) + pn

    # Serial Number (0x23)
    sn = b"AB1234567890"
    tlvs += struct.pack("BB", 0x23, len(sn)) + sn

    # MAC Base (0x24) - 6 bytes
    mac = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55])
    tlvs += struct.pack("BB", 0x24, 6) + mac

    # Manufacture Date (0x25)
    mdate = b"01/15/2020 00:00:00"
    tlvs += struct.pack("BB", 0x25, len(mdate)) + mdate

    # Device Version (0x26)
    dver = b"01"
    tlvs += struct.pack("BB", 0x26, len(dver)) + dver

    # Platform Name (0x28)
    plat = b"x86_64-juniper_qfx5241-r0"
    tlvs += struct.pack("BB", 0x28, len(plat)) + plat

    # MAC Size (0x2A) - 2 bytes
    tlvs += struct.pack("BB", 0x2A, 2) + struct.pack(">H", 256)

    # Vendor Name (0x2D)
    vname = b"Juniper"
    tlvs += struct.pack("BB", 0x2D, len(vname)) + vname

    # Manufacturer Name (0x2B)
    mname = b"Juniper Networks"
    tlvs += struct.pack("BB", 0x2B, len(mname)) + mname

    # Service Tag (0x2F)
    stag = b"STAG123"
    tlvs += struct.pack("BB", 0x2F, len(stag)) + stag

    # CRC-32 (0xFE) - placeholder 4 bytes
    import binascii
    crc_data = header + struct.pack(">H", len(tlvs) + 6) + tlvs + struct.pack("BB", 0xFE, 4)
    # Fix the total_length in header
    total_length = len(tlvs) + 6  # +6 for CRC TLV (2 byte header + 4 byte value)
    full = b"TlvInfo\x00\x01" + struct.pack(">H", total_length) + tlvs
    full += struct.pack("BB", 0xFE, 4)
    crc = binascii.crc32(full) & 0xFFFFFFFF
    full += struct.pack(">I", crc)

    return full


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _stub_dependencies():
    """Stub sonic_eeprom and sonic_py_common."""
    fake_pkg = types.ModuleType("sonic_eeprom")
    fake_sub = types.ModuleType("sonic_eeprom.eeprom_tlvinfo")

    class FakeTlvInfoDecoder:
        _TLV_INFO_HDR_LEN = 11
        _TLV_CODE_PRODUCT_NAME = 0x21
        _TLV_CODE_PART_NUMBER = 0x22
        _TLV_CODE_SERIAL_NUMBER = 0x23
        _TLV_CODE_MAC_BASE = 0x24
        _TLV_CODE_MANUF_DATE = 0x25
        _TLV_CODE_DEVICE_VERSION = 0x26
        _TLV_CODE_PLATFORM_NAME = 0x28
        _TLV_CODE_ONIE_VERSION = 0x29
        _TLV_CODE_MAC_SIZE = 0x2A
        _TLV_CODE_MANUF_NAME = 0x2B
        _TLV_CODE_VENDOR_NAME = 0x2D
        _TLV_CODE_VENDOR_EXT = 0xFD
        _TLV_CODE_CRC_32 = 0xFE
        _TLV_CODE_SERVICE_TAG = 0x2F

        def __init__(self, path, offset, fmt, readonly):
            self._path = path

        def read_eeprom(self):
            with open(self._path, "rb") as f:
                return bytearray(f.read())

        def is_valid_tlvinfo_header(self, eeprom):
            return eeprom[:7] == bytearray(b"TlvInfo")

        def is_valid_tlv(self, tlv):
            if len(tlv) < 2:
                return False
            return len(tlv) >= 2 + tlv[1]

        def get_tlv_field(self, eeprom, code):
            if eeprom == "N/A":
                return (False, None)
            idx = self._TLV_INFO_HDR_LEN
            total = (eeprom[9] << 8) | eeprom[10]
            end = self._TLV_INFO_HDR_LEN + total
            while idx + 2 < len(eeprom) and idx < end:
                if eeprom[idx] == code:
                    length = eeprom[idx + 1]
                    return (True, eeprom[idx:idx + 2 + length])
                idx += 2 + eeprom[idx + 1]
            return (False, None)

        def decoder(self, tlv_type, tlv):
            code = tlv[0]
            length = tlv[1]
            value = tlv[2:2 + length].decode('utf-8', errors='replace')
            return (f"TLV_0x{code:02X}", value)

    fake_sub.TlvInfoDecoder = FakeTlvInfoDecoder
    fake_pkg.eeprom_tlvinfo = fake_sub
    sys.modules["sonic_eeprom"] = fake_pkg
    sys.modules["sonic_eeprom.eeprom_tlvinfo"] = fake_sub

    # sonic_py_common.general
    fake_pycommon = sys.modules.get("sonic_py_common") or types.ModuleType("sonic_py_common")
    fake_general = types.ModuleType("sonic_py_common.general")
    fake_general.getstatusoutput_noshell = MagicMock(return_value=(0, "1"))
    fake_pycommon.general = fake_general
    sys.modules["sonic_py_common"] = fake_pycommon
    sys.modules["sonic_py_common.general"] = fake_general


def _load_eeprom_data_module():
    _stub_dependencies()
    spec = importlib.util.spec_from_file_location(
        "qfx5241_eeprom_data", str(PLUGIN_DIR / "eeprom_data.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests for fantype_detect
# ---------------------------------------------------------------------------

class TestFantypeDetect:
    def test_returns_afo(self):
        mod = _load_eeprom_data_module()
        sys.modules["sonic_py_common.general"].getstatusoutput_noshell = MagicMock(return_value=(0, "1"))
        with patch("os.listdir", return_value=["fan1_type"]):
            with patch("os.path.join", return_value="/fake/fan1_type"):
                result = mod.fantype_detect()
        assert result == "1"

    def test_returns_afi(self):
        mod = _load_eeprom_data_module()
        with patch.object(mod, "getstatusoutput_noshell", return_value=(0, "0")):
            with patch("os.listdir", return_value=["fan1_type"]):
                with patch("os.path.join", return_value="/fake/fan1_type"):
                    result = mod.fantype_detect()
        assert result == "0"

    def test_returns_default_when_no_type_files(self):
        mod = _load_eeprom_data_module()
        with patch("os.listdir", return_value=["fan1_speed"]):
            result = mod.fantype_detect()
        assert result == "0"

    def test_returns_default_when_invalid_value(self):
        mod = _load_eeprom_data_module()
        with patch.object(mod, "getstatusoutput_noshell", return_value=(0, "unknown")):
            with patch("os.listdir", return_value=["fan1_type"]):
                with patch("os.path.join", return_value="/fake/fan1_type"):
                    result = mod.fantype_detect()
        assert result == "0"


# ---------------------------------------------------------------------------
# Tests for Eeprom class
# ---------------------------------------------------------------------------

class TestEeprom:
    @pytest.fixture
    def eeprom_binary(self, tmp_path):
        data = _build_tlv_eeprom()
        eeprom_file = tmp_path / "eeprom_24c02.bin"
        eeprom_file.write_bytes(data)
        return str(eeprom_file)

    @pytest.fixture
    def eeprom_obj(self, eeprom_binary):
        mod = _load_eeprom_data_module()
        with patch.object(mod.Eeprom, "__init__", lambda self: None):
            obj = mod.Eeprom.__new__(mod.Eeprom)
        # Manually init with our test eeprom
        obj._Eeprom__eeprom_path = eeprom_binary
        obj._TLV_INFO_HDR_LEN = 11
        obj._TLV_CODE_PRODUCT_NAME = 0x21
        obj._TLV_CODE_PART_NUMBER = 0x22
        obj._TLV_CODE_SERIAL_NUMBER = 0x23
        obj._TLV_CODE_MAC_BASE = 0x24
        obj._TLV_CODE_MANUF_DATE = 0x25
        obj._TLV_CODE_DEVICE_VERSION = 0x26
        obj._TLV_CODE_PLATFORM_NAME = 0x28
        obj._TLV_CODE_ONIE_VERSION = 0x29
        obj._TLV_CODE_MAC_SIZE = 0x2A
        obj._TLV_CODE_MANUF_NAME = 0x2B
        obj._TLV_CODE_VENDOR_NAME = 0x2D
        obj._TLV_CODE_VENDOR_EXT = 0xFD
        obj._TLV_CODE_CRC_32 = 0xFE
        obj._TLV_CODE_SERVICE_TAG = 0x2F

        with open(eeprom_binary, "rb") as f:
            obj._Eeprom__eeprom_data = bytearray(f.read())

        # Build tlv dict
        obj._Eeprom__eeprom_tlv_dict = {}
        eeprom = obj._Eeprom__eeprom_data
        total_length = (eeprom[9] << 8) | eeprom[10]
        tlv_index = 11
        tlv_end = 11 + total_length
        while (tlv_index + 2) < len(eeprom) and tlv_index < tlv_end:
            code = "0x%02X" % eeprom[tlv_index]
            length = eeprom[tlv_index + 1]
            if tlv_index + 2 + length > len(eeprom):
                break
            value = eeprom[tlv_index + 2:tlv_index + 2 + length].decode('utf-8', errors='replace')
            obj._Eeprom__eeprom_tlv_dict[code] = value
            if eeprom[tlv_index] == 0xFE:
                break
            tlv_index += 2 + length

        # Mock get_tlv_field to return (True, [code, length, value_bytes])
        def _get_tlv_field(data, code):
            idx = 11
            total = (data[9] << 8) | data[10]
            end = 11 + total
            while idx + 2 < len(data) and idx < end:
                if data[idx] == code:
                    length = data[idx + 1]
                    # Return as list: [code, length, value_as_bytes]
                    value = bytes(data[idx + 2:idx + 2 + length])
                    return (True, [data[idx], length, value])
                idx += 2 + data[idx + 1]
            return (False, None)

        obj.get_tlv_field = _get_tlv_field
        return obj

    def test_serial_number_str(self, eeprom_obj):
        result = eeprom_obj.serial_number_str()
        assert result == "AB1234567890"

    def test_base_mac_address(self, eeprom_obj):
        result = eeprom_obj.base_mac_address()
        assert result == "00:11:22:33:44:55"

    def test_modelstr(self, eeprom_obj):
        result = eeprom_obj.modelstr()
        assert result == "QFX5241-48Y"

    def test_part_number_str(self, eeprom_obj):
        result = eeprom_obj.part_number_str()
        assert result == "750-123456"

    def test_serial_tag_str(self, eeprom_obj):
        result = eeprom_obj.serial_tag_str()
        assert result == "STAG123"

    def test_revision_str(self, eeprom_obj):
        result = eeprom_obj.revision_str()
        assert result == "01"

    def test_manuDate_str(self, eeprom_obj):
        result = eeprom_obj.manuDate_str()
        assert result == "01/15/2020 00:00:00"

    def test_platform_str(self, eeprom_obj):
        result = eeprom_obj.platform_str()
        assert result == "x86_64-juniper_qfx5241-r0"

    def test_MACsize_str(self, eeprom_obj):
        result = eeprom_obj.MACsize_str()
        assert result == "256"

    def test_vendor_name_str(self, eeprom_obj):
        result = eeprom_obj.vendor_name_str()
        assert result == "Juniper"

    def test_manufacture_name_str(self, eeprom_obj):
        result = eeprom_obj.manufacture_name_str()
        assert result == "Juniper Networks"

    def test_system_eeprom_info(self, eeprom_obj):
        result = eeprom_obj.system_eeprom_info()
        assert isinstance(result, dict)
        assert "0x21" in result

    # Test N/A fallback paths
    def test_serial_number_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.serial_number_str() == "N/A"

    def test_modelstr_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.modelstr() == "QFX5241"

    def test_part_number_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.part_number_str() == "N/A"

    def test_serial_tag_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.serial_tag_str() == "N/A"

    def test_revision_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.revision_str() == "N/A"

    def test_manuDate_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.manuDate_str() == "N/A"

    def test_platform_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.platform_str() == "N/A"

    def test_MACsize_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.MACsize_str() == "N/A"

    def test_vendor_name_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.vendor_name_str() == "N/A"

    def test_manufacture_name_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.manufacture_name_str() == "N/A"

    def test_onie_version_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        assert eeprom_obj.onie_version_str() == "N/A"

    def test_vendor_ext_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        result = eeprom_obj.vendor_ext_str()
        assert result == ("N/A", "N/A")

    def test_crc_str_invalid(self, eeprom_obj):
        eeprom_obj.get_tlv_field = lambda data, code: (False, None)
        result = eeprom_obj.crc_str()
        assert result == "N/A"


class TestEepromInit:
    def test_eeprom_init_success(self, tmp_path):
        """Test Eeprom.__init__ with valid EEPROM data."""
        mod = _load_eeprom_data_module()
        data = _build_tlv_eeprom()
        eeprom_file = tmp_path / "eeprom_24c02.bin"
        eeprom_file.write_bytes(data)

        with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "__init__",
                          lambda self, path, off, fmt, ro: setattr(self, '_path', path)):
            with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "read_eeprom",
                              return_value=bytearray(data)):
                with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "is_valid_tlvinfo_header",
                                  return_value=True):
                    with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "is_valid_tlv",
                                      return_value=True):
                        with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "decoder",
                                          return_value=("name", "value")):
                            obj = mod.Eeprom()
        assert isinstance(obj.system_eeprom_info(), dict)

    def test_eeprom_init_missing_file(self):
        """Test Eeprom.__init__ raises when eeprom file missing."""
        mod = _load_eeprom_data_module()
        with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "__init__",
                          lambda self, path, off, fmt, ro: None):
            with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "read_eeprom",
                              side_effect=Exception("No eeprom")):
                with pytest.raises(RuntimeError, match="Eeprom is not Programmed"):
                    mod.Eeprom()

    def test_eeprom_init_invalid_header(self):
        """Test Eeprom.__init__ with invalid TLV header."""
        mod = _load_eeprom_data_module()
        bad_data = bytearray(b"\x00" * 256)
        with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "__init__",
                          lambda self, path, off, fmt, ro: None):
            with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "read_eeprom",
                              return_value=bad_data):
                with patch.object(mod.eeprom_tlvinfo.TlvInfoDecoder, "is_valid_tlvinfo_header",
                                  return_value=False):
                    obj = mod.Eeprom()
        # Should complete without error, dict remains empty
        assert obj.system_eeprom_info() == {}


class TestMain:
    def test_main_function(self):
        """Test the main() function runs without error."""
        mod = _load_eeprom_data_module()
        mock_eeprom = MagicMock()
        mock_eeprom.modelstr.return_value = "QFX5241"
        mock_eeprom.part_number_str.return_value = "750-123456"
        mock_eeprom.serial_number_str.return_value = "SN123"
        mock_eeprom.base_mac_address.return_value = "00:11:22:33:44:55"
        mock_eeprom.manuDate_str.return_value = "2020-01-01"
        mock_eeprom.platform_str.return_value = "x86_64-juniper_qfx5241-r0"
        mock_eeprom.MACsize_str.return_value = "256"
        mock_eeprom.vendor_name_str.return_value = "Juniper"
        mock_eeprom.manufacture_name_str.return_value = "Juniper Networks"
        mock_eeprom.system_eeprom_info.return_value = {"0x29": "1.0", "0xFE": "0xABCD"}
        mock_eeprom.vendor_ext_str.return_value = ("0x4A", b"4A756E6970657220")

        with patch.object(mod, "Eeprom", return_value=mock_eeprom):
            # mock open to return bytes for 'rb' mode reads
            m = MagicMock()
            file_handles = {}

            def fake_open(path, mode='r', *args, **kwargs):
                handle = MagicMock()
                if 'b' in mode and 'r' in mode:
                    # Return valid hex chars for binascii.hexlify/unhexlify
                    handle.read.return_value = b'\x41\x42' * 128
                    handle.__enter__ = lambda s: s
                    handle.__exit__ = MagicMock(return_value=False)
                    handle.seek = MagicMock()
                elif 'b' in mode and 'w' in mode:
                    handle.__enter__ = lambda s: s
                    handle.__exit__ = MagicMock(return_value=False)
                    handle.write = MagicMock()
                else:
                    handle.__enter__ = lambda s: s
                    handle.__exit__ = MagicMock(return_value=False)
                    handle.write = MagicMock()
                    handle.close = MagicMock()
                    handle.seek = MagicMock()
                    # For hex file reads, return valid hex strings
                    handle.read = MagicMock(return_value=b'4142434445464748')
                return handle

            with patch("builtins.open", side_effect=fake_open):
                with patch("subprocess.call"):
                    mod.main()
