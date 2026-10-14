"""Unit tests for QFX5241 sfputil.py plugin (GPIO-based SFP management, 32 QSFP ports)."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import patch, mock_open, MagicMock, call
from io import BytesIO

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "plugins"


# ---------------------------------------------------------------------------
# Stubs for sonic dependencies
# ---------------------------------------------------------------------------

def _stub_dependencies():
    """Stub out all sonic_* imports required by sfputil.py."""

    # sonic_sfp.sfputilbase
    fake_sfp_pkg = types.ModuleType("sonic_sfp")
    fake_sfp_base = types.ModuleType("sonic_sfp.sfputilbase")

    class FakeSfpUtilBase:
        IDENTITY_EEPROM_ADDR = 0x50
        DOM_EEPROM_ADDR = 0x51

        def __init__(self):
            pass

        def _get_port_eeprom_path(self, port, addr):
            return f"/sys/bus/i2c/devices/{port}-00{addr:02x}/eeprom"

        def _sfp_eeprom_present(self, path, offset):
            return True

        def _read_eeprom_specific_bytes(self, sysfs, offset, width):
            return bytearray(width)

    fake_sfp_base.SfpUtilBase = FakeSfpUtilBase
    fake_sfp_pkg.sfputilbase = fake_sfp_base
    sys.modules["sonic_sfp"] = fake_sfp_pkg
    sys.modules["sonic_sfp.sfputilbase"] = fake_sfp_base

    # sonic_platform_base.sonic_sfp.sff8472
    fake_spb = sys.modules.get("sonic_platform_base") or types.ModuleType("sonic_platform_base")
    fake_spb_sfp = types.ModuleType("sonic_platform_base.sonic_sfp")
    fake_sff8472 = types.ModuleType("sonic_platform_base.sonic_sfp.sff8472")
    fake_sff8436 = types.ModuleType("sonic_platform_base.sonic_sfp.sff8436")
    fake_inf8628 = types.ModuleType("sonic_platform_base.sonic_sfp.inf8628")

    class FakeDom:
        def __init__(self, *a, **kw):
            pass

        def parse_temperature(self, raw, offset):
            return {"data": {"Temperature": {"value": "25.5"}}}

        def parse_voltage(self, raw, offset):
            return {"data": {"Vcc": {"value": "3.3"}}}

        def parse_channel_monitor_params(self, raw, offset):
            return {"data": {
                "RX1Power": {"value": "-1.0"}, "RX2Power": {"value": "-1.1"},
                "RX3Power": {"value": "-1.2"}, "RX4Power": {"value": "-1.3"},
                "TX1Bias": {"value": "6.0"}, "TX2Bias": {"value": "6.1"},
                "TX3Bias": {"value": "6.2"}, "TX4Bias": {"value": "6.3"},
                "TXPower": {"value": "1.0"}, "TXBias": {"value": "6.0"},
                "RXPower": {"value": "-1.0"},
            }}

        def parse_channel_monitor_params_with_tx_power(self, raw, offset):
            return {"data": {
                "RX1Power": {"value": "-1.0"}, "RX2Power": {"value": "-1.1"},
                "RX3Power": {"value": "-1.2"}, "RX4Power": {"value": "-1.3"},
                "TX1Bias": {"value": "6.0"}, "TX2Bias": {"value": "6.1"},
                "TX3Bias": {"value": "6.2"}, "TX4Bias": {"value": "6.3"},
                "TX1Power": {"value": "1.0"}, "TX2Power": {"value": "1.1"},
                "TX3Power": {"value": "1.2"}, "TX4Power": {"value": "1.3"},
            }}

        def parse_sfp_dom_rev(self, raw, offset):
            return {"data": {"dom_rev": {"value": "SFF-8436 Rev 1.0"}}}

        def parse_module_threshold_values(self, raw, offset):
            return {"data": {
                "TempHighAlarm": {"value": "70"}, "TempHighWarning": {"value": "65"},
                "TempLowAlarm": {"value": "0"}, "TempLowWarning": {"value": "5"},
                "VccHighAlarm": {"value": "3.6"}, "VccHighWarning": {"value": "3.5"},
                "VccLowAlarm": {"value": "3.0"}, "VccLowWarning": {"value": "3.1"},
            }}

        def parse_channel_threshold_values(self, raw, offset):
            return {"data": {
                "RxPowerHighAlarm": {"value": "5"}, "RxPowerHighWarning": {"value": "4"},
                "RxPowerLowAlarm": {"value": "-20"}, "RxPowerLowWarning": {"value": "-18"},
                "TxBiasHighAlarm": {"value": "10"}, "TxBiasHighWarning": {"value": "9"},
                "TxBiasLowAlarm": {"value": "1"}, "TxBiasLowWarning": {"value": "2"},
            }}

        def parse_alarm_warning_threshold(self, raw, offset):
            return {"data": {
                "TempHighAlarm": {"value": "70"}, "TempLowAlarm": {"value": "0"},
                "TempHighWarning": {"value": "65"}, "TempLowWarning": {"value": "5"},
                "VoltageHighAlarm": {"value": "3.6"}, "VoltageLowAlarm": {"value": "3.0"},
                "VoltageHighWarning": {"value": "3.5"}, "VoltageLowWarning": {"value": "3.1"},
                "BiasHighAlarm": {"value": "10"}, "BiasLowAlarm": {"value": "1"},
                "BiasHighWarning": {"value": "9"}, "BiasLowWarning": {"value": "2"},
                "TXPowerHighAlarm": {"value": "5"}, "TXPowerLowAlarm": {"value": "-5"},
                "TXPowerHighWarning": {"value": "4"}, "TXPowerLowWarning": {"value": "-4"},
                "RXPowerHighAlarm": {"value": "5"}, "RXPowerLowAlarm": {"value": "-20"},
                "RXPowerHighWarning": {"value": "4"}, "RXPowerLowWarning": {"value": "-18"},
            }}

    class FakeInterfaceId:
        def __init__(self, *a, **kw):
            pass

        def parse_sfp_type(self, raw, offset):
            return {"data": {"type": {"value": "QSFP28"}, "type_abbrv_name": {"value": "QSFP28"}}}

        def parse_sfp_info_bulk(self, raw, offset):
            return {"data": {
                "type": {"value": "QSFP28"}, "type_abbrv_name": {"value": "QSFP28"},
                "Connector": {"value": "LC"}, "EncodingCodes": {"value": "64B/66B"},
                "Extended Identifier": {"value": "Power Class 1"},
                "RateIdentifier": {"value": "N/A"},
                "Specification compliance": {"value": {
                    "10/40G Ethernet Compliance Code": {"value": "40GBASE-LR4"},
                }},
                "Nominal Bit Rate(100Mbs)": {"value": "103"},
                "NominalSignallingRate(UnitsOf100Mbd)": {"value": "103"},
                "Length(km)": {"value": "10"},
                "LengthSMFkm-UnitsOfKm": {"value": "10"},
            }}

        def parse_vendor_name(self, raw, offset):
            return {"data": {"Vendor Name": {"value": "FINISAR"}}}

        def parse_vendor_pn(self, raw, offset):
            return {"data": {"Vendor PN": {"value": "FTLC9551REPM-J1"}}}

        def parse_vendor_rev(self, raw, offset):
            return {"data": {"Vendor Rev": {"value": "AB"}}}

        def parse_vendor_sn(self, raw, offset):
            return {"data": {"Vendor SN": {"value": "XYZ123"}}}

        def parse_vendor_oui(self, raw, offset):
            return {"data": {"Vendor OUI": {"value": "00-90-65"}}}

        def parse_vendor_date(self, raw, offset):
            return {"data": {"VendorDataCode(YYYY-MM-DD Lot)": {"value": "2020-01-15"}}}

        def parse_dom_capability(self, raw, offset):
            return {"data": {"Tx_power_support": {"value": "off"}}}

    fake_sff8472.sff8472Dom = FakeDom
    fake_sff8472.sff8472InterfaceId = FakeInterfaceId
    fake_sff8436.sff8436Dom = FakeDom
    fake_sff8436.sff8436InterfaceId = FakeInterfaceId
    fake_inf8628.inf8628InterfaceId = FakeInterfaceId

    fake_spb.sonic_sfp = fake_spb_sfp
    fake_spb_sfp.sff8472 = fake_sff8472
    fake_spb_sfp.sff8436 = fake_sff8436
    fake_spb_sfp.inf8628 = fake_inf8628

    sys.modules["sonic_platform_base"] = fake_spb
    sys.modules["sonic_platform_base.sonic_sfp"] = fake_spb_sfp
    sys.modules["sonic_platform_base.sonic_sfp.sff8472"] = fake_sff8472
    sys.modules["sonic_platform_base.sonic_sfp.sff8436"] = fake_sff8436
    sys.modules["sonic_platform_base.sonic_sfp.inf8628"] = fake_inf8628

    # sonic_py_common.logger
    fake_pycommon = sys.modules.get("sonic_py_common") or types.ModuleType("sonic_py_common")
    fake_logger = types.ModuleType("sonic_py_common.logger")

    class FakeLogger:
        def __init__(self, *a, **kw):
            pass

        def log_debug(self, *a):
            pass

        def log_info(self, *a):
            pass

        def log_error(self, *a):
            pass

    fake_logger.Logger = FakeLogger
    fake_pycommon.logger = fake_logger
    sys.modules["sonic_py_common"] = fake_pycommon
    sys.modules["sonic_py_common.logger"] = fake_logger


def _load_sfputil_module():
    _stub_dependencies()
    # Patch gpio_sfp_base_init and os.path.isfile/os.remove during module load
    with patch("glob.glob", return_value=["/sys/bus/platform/drivers/gpioslave-tmc/gpioslave-tmc.21/gpio/gpio400"]):
        with patch("builtins.open", mock_open(read_data="400\n")):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.isfile", return_value=False):
                    spec = importlib.util.spec_from_file_location(
                        "qfx5241_sfputil", str(PLUGIN_DIR / "sfputil.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sfpmod():
    return _load_sfputil_module()


@pytest.fixture
def sfp(sfpmod):
    """Create SfpUtil instance with mocked init."""
    with patch.object(sfpmod, "gpio_sfp_base_init"):
        with patch("os.path.isfile", return_value=False):
            obj = sfpmod.SfpUtil()
    obj.osfp_ports = []
    return obj


# ===========================================================================
# Module-level GPIO helper tests
# ===========================================================================

class TestGpioHelpers:
    def test_gpio_create_file_exists(self, sfpmod):
        with patch("os.path.exists", return_value=True):
            assert sfpmod.gpio_create_file(100) is True

    def test_gpio_create_file_creates(self, sfpmod):
        m = mock_open()
        with patch("os.path.exists", return_value=False):
            with patch("builtins.open", m):
                assert sfpmod.gpio_create_file(100) is True
        m().write.assert_called_with("100")

    def test_gpio_create_file_ioerror(self, sfpmod):
        with patch("os.path.exists", return_value=False):
            with patch("builtins.open", side_effect=IOError("fail")):
                assert sfpmod.gpio_create_file(100) is False

    def test_gpio_sfp_read(self, sfpmod):
        m = mock_open(read_data="1\n")
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_read(100) == 1

    def test_gpio_sfp_read_ioerror(self, sfpmod):
        with patch("builtins.open", side_effect=IOError("fail")):
            assert sfpmod.gpio_sfp_read(100) == 0

    def test_gpio_sfp_write_success(self, sfpmod):
        m = mock_open()
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_write(100, 1) is True

    def test_gpio_sfp_write_ioerror(self, sfpmod):
        with patch("builtins.open", side_effect=IOError("fail")):
            assert sfpmod.gpio_sfp_write(100, 1) is False

    def test_gpio_sfp_presence_get_port_present(self, sfpmod):
        sfpmod.gpio_sfp_presence[0] = 416
        m = mock_open(read_data="1\n")
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_presence_get(0) == 1

    def test_gpio_sfp_presence_get_port_missing(self, sfpmod):
        sfpmod.gpio_sfp_presence.clear()
        assert sfpmod.gpio_sfp_presence_get(99) == 0

    def test_gpio_sfp_lpmode_get_port_present(self, sfpmod):
        sfpmod.gpio_sfp_lpmode[0] = 448
        m = mock_open(read_data="0\n")
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_lpmode_get(0) == 0

    def test_gpio_sfp_lpmode_get_port_missing(self, sfpmod):
        sfpmod.gpio_sfp_lpmode.clear()
        assert sfpmod.gpio_sfp_lpmode_get(99) == 0

    def test_gpio_sfp_lpmode_set_success(self, sfpmod):
        sfpmod.gpio_sfp_lpmode[0] = 448
        m = mock_open()
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_lpmode_set(0, 1) is True

    def test_gpio_sfp_lpmode_set_port_missing(self, sfpmod):
        sfpmod.gpio_sfp_lpmode.clear()
        assert sfpmod.gpio_sfp_lpmode_set(99, 1) is False

    def test_gpio_sfp_reset_set_success(self, sfpmod):
        sfpmod.gpio_sfp_reset[0] = 400
        m = mock_open()
        with patch("builtins.open", m):
            assert sfpmod.gpio_sfp_reset_set(0, 0) is True

    def test_gpio_sfp_reset_set_port_missing(self, sfpmod):
        sfpmod.gpio_sfp_reset.clear()
        assert sfpmod.gpio_sfp_reset_set(99, 0) is False

    def test_gpio_sfp_port_init(self, sfpmod):
        sfpmod.gpio_sfp_presence.clear()
        sfpmod.gpio_sfp_reset.clear()
        sfpmod.gpio_sfp_lpmode.clear()
        with patch.object(sfpmod, "gpio_create_file", return_value=True):
            sfpmod.gpio_sfp_port_init(400, 5)
        assert 5 in sfpmod.gpio_sfp_presence
        assert 5 in sfpmod.gpio_sfp_reset
        assert 5 in sfpmod.gpio_sfp_lpmode

    def test_gpio_sfp_slave_init_no_glob(self, sfpmod):
        sfpmod.gpio_sfp_presence.clear()
        with patch("glob.glob", return_value=[]):
            sfpmod.gpio_sfp_slave_init("/fake/path", 0, 3)
        assert len(sfpmod.gpio_sfp_presence) == 0

    def test_gpio_sfp_slave_init_ioerror(self, sfpmod):
        with patch("glob.glob", return_value=["/fake/gpio400"]):
            with patch("builtins.open", side_effect=IOError("fail")):
                sfpmod.gpio_sfp_slave_init("/fake/path", 0, 3)

    def test_gpio_sfp_base_init(self, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_slave_init") as mock_init:
            sfpmod.gpio_sfp_base_init()
        assert mock_init.call_count == 2


# ===========================================================================
# SfpUtil class tests
# ===========================================================================

class TestSfpUtilProperties:
    def test_port_start(self, sfp):
        assert sfp.port_start == 0

    def test_port_end(self, sfp):
        assert sfp.port_end == 31

    def test_qsfp_port_start(self, sfp):
        assert sfp.qsfp_port_start == 0

    def test_qsfp_port_end(self, sfp):
        assert sfp.qsfp_port_end == 31

    def test_qsfp_ports(self, sfp):
        ports = sfp.qsfp_ports
        assert 0 in ports
        assert 31 in ports
        assert 32 in ports  # range(0, 33)

    def test_port_to_eeprom_mapping(self, sfp):
        mapping = sfp.port_to_eeprom_mapping
        assert 0 in mapping
        assert 31 in mapping
        assert "/i2c-14/" in mapping[0]
        assert "/i2c-45/" in mapping[31]

    def test_port_to_i2cbus_mapping(self, sfp, sfpmod):
        assert sfpmod.SfpUtil.port_to_i2cbus_mapping[0] == 14
        assert sfpmod.SfpUtil.port_to_i2cbus_mapping[31] == 45


class TestSfpUtilInit:
    def test_init_removes_sfppresence_file(self, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_base_init"):
            with patch("os.path.isfile", return_value=True):
                with patch("os.remove") as mock_rm:
                    sfpmod.SfpUtil()
        mock_rm.assert_called_once_with('/var/run/sfppresence')


class TestBitOperations:
    def test_clear_bit(self, sfp):
        assert sfp.clear_bit(0xFF, 0) == 0xFE
        assert sfp.clear_bit(0xFF, 7) == 0x7F

    def test_check_bit(self, sfp):
        assert sfp.check_bit(0x01, 0) == 1
        assert sfp.check_bit(0x01, 1) == 0

    def test_set_bit(self, sfp):
        assert sfp.set_bit(0x00, 0) == 0x01
        assert sfp.set_bit(0x00, 7) == 0x80


class TestOpticsDetection:
    def test_is_100g_optics_known(self, sfp):
        assert sfp.is_100g_optics("AFBR-89CDDZ-JU1") is True

    def test_is_100g_optics_unknown(self, sfp):
        assert sfp.is_100g_optics("UNKNOWN-PN") is False

    def test_is_40g_optics_known(self, sfp):
        assert sfp.is_40g_optics("FTL4C1QE1C-J1") is True

    def test_is_40g_optics_unknown(self, sfp):
        assert sfp.is_40g_optics("UNKNOWN") is False

    def test_is_optics_100g(self, sfp):
        assert sfp.is_optics("AFBR-89CDDZ-JU1") is True

    def test_is_optics_40g(self, sfp):
        assert sfp.is_optics("FTL4C1QE1C-J1") is True

    def test_is_optics_not(self, sfp):
        assert sfp.is_optics("DAC-CABLE") is False

    def test_is_highpower_optics(self, sfp):
        assert sfp.is_highpower_optics(0x03) != 0
        assert sfp.is_highpower_optics(0x00) == 0

    def test_is_cdr_present(self, sfp):
        assert sfp.is_cdr_present(0x0C) != 0
        assert sfp.is_cdr_present(0x00) == 0


class TestPresenceAndLpmode:
    def test_get_presence_true(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=1):
            assert sfp.get_presence(0) is True

    def test_get_presence_false(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
            assert sfp.get_presence(0) is False

    def test_get_low_power_mode_true(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_lpmode_get", return_value=0):
            assert sfp.get_low_power_mode(0) is True

    def test_get_low_power_mode_false(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_lpmode_get", return_value=1):
            assert sfp.get_low_power_mode(0) is False

    def test_set_low_power_mode_enable(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_lpmode_set", return_value=True) as mock_set:
            assert sfp.set_low_power_mode(0, True) is True
        mock_set.assert_called_with(0, 0)

    def test_set_low_power_mode_disable(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_lpmode_set", return_value=True) as mock_set:
            assert sfp.set_low_power_mode(0, False) is True
        mock_set.assert_called_with(0, 1)

    def test_reset(self, sfp, sfpmod):
        with patch.object(sfpmod, "gpio_sfp_reset_set", return_value=True) as mock_set:
            assert sfp.reset(0) is True
        mock_set.assert_called_with(0, 0)


class TestFileIO:
    def test_write_to_file_success(self, sfp):
        m = mock_open()
        with patch("builtins.open", m):
            assert sfp.write_to_file("/tmp/test", ["a", "b"]) is True

    def test_write_to_file_ioerror(self, sfp):
        with patch("builtins.open", side_effect=IOError("fail")):
            assert sfp.write_to_file("/tmp/test", []) is False

    def test_read_from_file_success(self, sfp):
        m = mock_open(read_data="line1\nline2\n")
        with patch("builtins.open", m):
            result = sfp.read_from_file("/tmp/test")
        assert result == ["line1", "line2"]

    def test_read_from_file_ioerror(self, sfp):
        with patch("builtins.open", side_effect=IOError("fail")):
            assert sfp.read_from_file("/tmp/test") is False


class TestProcessTxCTLE:
    def test_process_txctle_100g_optics(self, sfp):
        eeprom = BytesIO(b'\x00' * 300)
        # process_TxCTLE uses chr() with create_string_buffer which fails in py3
        # This is a known source code issue - test that it attempts the operation
        with pytest.raises(TypeError):
            sfp.process_TxCTLE(eeprom, 0, "AFBR-89CDDZ-JU1")

    def test_process_txctle_non_100g_optics(self, sfp):
        eeprom = BytesIO(b'\x00' * 300)
        # Should be a no-op for non-100g optics
        sfp.process_TxCTLE(eeprom, 0, "DAC-CABLE")


class TestGetTransceiverInfoDict:
    def test_qsfp_port_returns_info(self, sfp):
        m = mock_open(read_data=b'\x00' * 256)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_info_dict(0)
        assert result is not None
        assert result["manufacturer"] == "FINISAR"
        assert result["model"] == "FTLC9551REPM-J1"
        assert result["serial"] == "XYZ123"
        assert result["type"] == "QSFP28"

    def test_qsfp_port_eeprom_not_present(self, sfp):
        with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
            result = sfp.get_transceiver_info_dict(0)
        assert result is None

    def test_qsfp_port_eeprom_ioerror(self, sfp):
        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", side_effect=IOError("fail")):
                result = sfp.get_transceiver_info_dict(0)
        assert result is None


class TestGetTransceiverDomInfoDict:
    def test_osfp_port_returns_na(self, sfp):
        sfp.osfp_ports = [99]
        result = sfp.get_transceiver_dom_info_dict(99)
        assert result["temperature"] == "N/A"
        assert result["tx1power"] == "N/A"

    def test_qsfp_port_returns_dom(self, sfp):
        m = mock_open(read_data=b'\x00' * 512)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_dom_info_dict(0)
        assert result is not None
        assert "temperature" in result
        assert "voltage" in result
        assert "rx1power" in result

    def test_qsfp_port_eeprom_not_present(self, sfp):
        with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
            result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_qsfp_port_eeprom_ioerror(self, sfp):
        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", side_effect=IOError("fail")):
                result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None


class TestGetTransceiverDomThresholdInfoDict:
    def test_qsfp_port_returns_thresholds(self, sfp):
        m = mock_open(read_data=b'\x00' * 512)
        with patch("builtins.open", m):
            with patch("io.open", m):
                result = sfp.get_transceiver_dom_threshold_info_dict(0)
        assert result is not None
        assert "temphighalarm" in result

    def test_qsfp_port_eeprom_not_present(self, sfp):
        with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
            result = sfp.get_transceiver_dom_threshold_info_dict(0)
        assert result is None

    def test_sfp_port_eeprom_not_present(self, sfp):
        # Force port out of qsfp_ports range
        sfp.PORTS_IN_BLOCK = 0  # makes qsfp_ports = [0]
        with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
            result = sfp.get_transceiver_dom_threshold_info_dict(5)
        assert result is None

    def test_sfp_port_returns_thresholds(self, sfp):
        sfp.PORTS_IN_BLOCK = 0
        m = mock_open(read_data=b'\x00' * 512)
        with patch("io.open", m):
            with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                # Source references undefined SFP_MODULE_THRESHOLD_OFFSET/WIDTH
                with pytest.raises(NameError):
                    sfp.get_transceiver_dom_threshold_info_dict(5)


class TestSfpDetect:
    def test_sfp_detect_no_presence_file(self, sfp, sfpmod):
        with patch("os.path.isfile", return_value=False):
            with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                with patch.object(sfp, "write_to_file", return_value=True):
                    result = sfp.sfp_detect()
        assert result[0] is True

    def test_sfp_detect_write_fails(self, sfp, sfpmod):
        with patch("os.path.isfile", return_value=False):
            with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                with patch.object(sfp, "write_to_file", return_value=False):
                    result = sfp.sfp_detect()
                    assert result[0] is False

    def test_sfp_detect_with_existing_presence_file(self, sfp, sfpmod):
        prev_values = ["0"] * 32
        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "write_to_file", return_value=True):
                        result = sfp.sfp_detect()
        assert result[0] is True


class TestGetTransceiverChangeEvent:
    def test_get_transceiver_change_event(self, sfp):
        with patch.object(sfp, "sfp_detect", return_value=(True, {0: "1"})):
            with patch("time.sleep"):
                result = sfp.get_transceiver_change_event(timeout=100)
        assert result == (True, {0: "1"})


class TestPortCtleSettings:
    def test_port_ctle_settings_all_ports(self, sfp, sfpmod):
        assert len(sfpmod.SfpUtil.port_ctle_settings) == 32
        assert sfpmod.SfpUtil.port_ctle_settings[0] == 119
        assert sfpmod.SfpUtil.port_ctle_settings[15] == 51


# ===========================================================================
# Additional coverage tests for under-covered functions
# ===========================================================================

class TestGpioSfpSlaveInit:
    def test_slave_init_opens_base_file(self, sfpmod):
        with patch("glob.glob", return_value=["/sys/gpio/gpio400"]):
            with patch("builtins.open", mock_open(read_data="400\n")):
                with patch.object(sfpmod, "gpio_sfp_port_init") as mock_init:
                    sfpmod.gpio_sfp_slave_init("/fake/path", 0, 2)
        assert mock_init.call_count == 3

    def test_slave_init_no_glob_match(self, sfpmod):
        with patch("glob.glob", return_value=[]):
            with patch.object(sfpmod, "gpio_sfp_port_init") as mock_init:
                sfpmod.gpio_sfp_slave_init("/fake/path", 0, 2)
        mock_init.assert_not_called()

    def test_slave_init_ioerror(self, sfpmod):
        with patch("glob.glob", return_value=["/sys/gpio/gpio400"]):
            with patch("builtins.open", side_effect=IOError("fail")):
                # Should not raise
                sfpmod.gpio_sfp_slave_init("/fake/path", 0, 2)


class TestProcessTxCTLEDeep:
    def test_process_txctle_100g_hits_chr_typeerror(self, sfp):
        """Source uses chr() with create_string_buffer which fails in py3."""
        eeprom_mock = MagicMock()
        with pytest.raises(TypeError):
            sfp.process_TxCTLE(eeprom_mock, 0, "FTLC9551REPM-J1")

    def test_process_txctle_40g_does_nothing(self, sfp):
        """40g optics should not trigger the CTLE write."""
        eeprom_mock = MagicMock()
        sfp.process_TxCTLE(eeprom_mock, 0, "some-40g-optic")
        eeprom_mock.seek.assert_not_called()

    def test_process_txctle_non_optic(self, sfp):
        """Non-optic part number goes to else:pass."""
        eeprom_mock = MagicMock()
        sfp.process_TxCTLE(eeprom_mock, 0, "UNKNOWN-PN-12345")
        eeprom_mock.seek.assert_not_called()


class TestSfpDetectWithChange:
    def test_sfp_detect_port_change_optics_detected(self, sfp, sfpmod):
        """Cover the path where previous != current and optics is found."""
        prev_values = ["0"] * 32
        prev_values[0] = "1"  # Port 0 was previously 1

        eeprom_mock = MagicMock()

        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                        with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                            with patch.object(sfp, "is_optics", return_value=True):
                                with patch.object(sfp, "process_TxCTLE"):
                                    with patch.object(sfp, "write_to_file", return_value=True):
                                        result = sfp.sfp_detect()
        assert result[0] is True

    def test_sfp_detect_port_change_not_optics(self, sfp, sfpmod):
        """Cover the path where change is detected but not optics."""
        prev_values = ["0"] * 32
        prev_values[0] = "1"  # Port 0 changed

        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                        with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                            with patch.object(sfp, "is_optics", return_value=False):
                                with patch.object(sfp, "write_to_file", return_value=True):
                                    result = sfp.sfp_detect()
        assert result[0] is True

    def test_sfp_detect_port_change_eeprom_not_present(self, sfp, sfpmod):
        """Cover path where eeprom not present during change detection."""
        prev_values = ["0"] * 32
        prev_values[0] = "1"

        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
                        result = sfp.sfp_detect()
        assert result is None

    def test_sfp_detect_port_change_eeprom_ioerror(self, sfp, sfpmod):
        """Cover IOError opening eeprom during change detection."""
        prev_values = ["0"] * 32
        prev_values[0] = "1"

        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                        with patch("builtins.open", side_effect=IOError("fail")):
                            result = sfp.sfp_detect()
        assert result is None

    def test_sfp_detect_port_change_read_eeprom_returns_none(self, sfp, sfpmod):
        """Cover path where _read_eeprom_specific_bytes returns None."""
        prev_values = ["0"] * 32
        prev_values[0] = "1"

        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=prev_values):
                with patch.object(sfpmod, "gpio_sfp_presence_get", return_value=0):
                    with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                        with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                            with patch.object(sfp, "_read_eeprom_specific_bytes", return_value=None):
                                result = sfp.sfp_detect()
        assert result is None

    def test_sfp_detect_read_from_file_returns_false(self, sfp, sfpmod):
        """Cover path where presence file exists but read fails."""
        with patch("os.path.isfile", return_value=True):
            with patch.object(sfp, "read_from_file", return_value=False):
                result = sfp.sfp_detect()
        assert result == (False, {})


class TestGetTransceiverInfoDictOSFP:
    def test_osfp_port_hits_nameerror(self, sfp):
        """Source bug: inf8628InterfaceId is never imported, so OSFP path raises NameError."""
        sfp.osfp_ports = [0]
        with pytest.raises(NameError, match="inf8628InterfaceId"):
            sfp.get_transceiver_info_dict(0)

    def test_osfp_port_with_injected_inf8628(self, sfp, sfpmod):
        """Cover the OSFP branch by injecting inf8628InterfaceId into module globals."""
        from unittest.mock import MagicMock
        fake_inf = MagicMock()
        fake_inf.return_value = MagicMock(
            parse_sfp_type=lambda r, o: {"data": {"type": {"value": "OSFP"}, "type_abbrv_name": {"value": "OSFP"}}},
            parse_vendor_name=lambda r, o: {"data": {"Vendor Name": {"value": "VENDOR"}}},
            parse_vendor_pn=lambda r, o: {"data": {"Vendor PN": {"value": "PN123"}}},
            parse_vendor_rev=lambda r, o: {"data": {"Vendor Rev": {"value": "01"}}},
            parse_vendor_sn=lambda r, o: {"data": {"Vendor SN": {"value": "SN456"}}},
        )
        sfp.osfp_ports = [0]
        sfpmod.__dict__["inf8628InterfaceId"] = fake_inf
        try:
            m = mock_open(read_data=b'\x00' * 256)
            with patch("builtins.open", m):
                result = sfp.get_transceiver_info_dict(0)
            assert result is not None
            assert result["type"] == "OSFP"
            assert result["vendor_oui"] == "N/A"
        finally:
            del sfpmod.__dict__["inf8628InterfaceId"]

    def test_osfp_port_eeprom_not_present(self, sfp, sfpmod):
        """Cover eeprom not present with injected inf8628."""
        fake_inf = MagicMock()
        sfp.osfp_ports = [0]
        sfpmod.__dict__["inf8628InterfaceId"] = fake_inf
        try:
            with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
                result = sfp.get_transceiver_info_dict(0)
            assert result is None
        finally:
            del sfpmod.__dict__["inf8628InterfaceId"]

    def test_osfp_port_eeprom_ioerror(self, sfp, sfpmod):
        fake_inf = MagicMock()
        sfp.osfp_ports = [0]
        sfpmod.__dict__["inf8628InterfaceId"] = fake_inf
        try:
            with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                with patch("builtins.open", side_effect=IOError("fail")):
                    result = sfp.get_transceiver_info_dict(0)
            assert result is None
        finally:
            del sfpmod.__dict__["inf8628InterfaceId"]

    def test_osfp_read_eeprom_returns_none(self, sfp, sfpmod):
        fake_inf = MagicMock()
        fake_inf.return_value = MagicMock(
            parse_sfp_type=lambda r, o: {"data": {"type": {"value": "OSFP"}, "type_abbrv_name": {"value": "OSFP"}}},
        )
        sfp.osfp_ports = [0]
        sfpmod.__dict__["inf8628InterfaceId"] = fake_inf
        try:
            with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                    # Second read returns None (vendor_name)
                    call_count = [0]
                    def fake_read(sysfs, offset, width):
                        call_count[0] += 1
                        if call_count[0] == 2:
                            return None
                        return bytearray(width)
                    with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                        result = sfp.get_transceiver_info_dict(0)
            assert result is None
        finally:
            del sfpmod.__dict__["inf8628InterfaceId"]


class TestGetTransceiverInfoDictQSFPDeep:
    def test_qsfp_read_eeprom_returns_none_for_bulk(self, sfp):
        """Cover None return from _read_eeprom_specific_bytes for bulk data."""
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # First call (bulk) returns None
            return bytearray(width)

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_info_dict(0)
        assert result is None

    def test_qsfp_read_eeprom_returns_none_for_vendor_name(self, sfp):
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 2:
                return None
            return bytearray(width)

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_info_dict(0)
        assert result is None

    def test_qsfp_close_ioerror(self, sfp):
        """Cover IOError on close."""
        mock_file = MagicMock()
        mock_file.close.side_effect = IOError("close fail")

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", return_value=mock_file):
                result = sfp.get_transceiver_info_dict(0)
        assert result is None

    def test_qsfp_cable_length_found(self, sfp):
        """Verify cable_type and cable_length are populated."""
        m = mock_open(read_data=b'\x00' * 256)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_info_dict(0)
        # From mock, Length(km) is in the bulk data
        assert result["cable_type"] is not None
        assert result["cable_length"] is not None


class TestGetTransceiverDomInfoDictDeep:
    def test_qsfp_dom_read_returns_none_temperature(self, sfp):
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return bytearray(width)

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_qsfp_dom_close_ioerror(self, sfp):
        mock_file = MagicMock()
        mock_file.close.side_effect = IOError("close fail")

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", return_value=mock_file):
                result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_qsfp_dom_all_fields_populated(self, sfp):
        m = mock_open(read_data=b'\x00' * 512)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_dom_info_dict(0)
        assert result["temperature"] == "25.5"
        assert result["voltage"] == "3.3"
        assert result["rx1power"] == "-1.0"
        assert result["tx1bias"] == "6.0"
        # tx1power is N/A because Tx_power_support mock returns "off"
        assert result["tx1power"] == "N/A"

    def test_qsfp_dom_with_tx_power_support(self, sfp, sfpmod):
        """Cover the tx_power branch when Tx_power_support is on and dom_rev is SFF-8636."""
        m = mock_open(read_data=b'\x00' * 512)
        # Need both dom_rev=SFF-8636 and Tx_power_support=on
        sfpmod.sff8436InterfaceId.parse_dom_capability = lambda self, r, o: {
            "data": {"Tx_power_support": {"value": "on"}}}
        sfpmod.sff8436Dom.parse_sfp_dom_rev = lambda self, r, o: {
            "data": {"dom_rev": {"value": "SFF-8636 Rev 2.0"}}}
        try:
            with patch("builtins.open", m):
                result = sfp.get_transceiver_dom_info_dict(0)
            assert result["tx1power"] == "1.0"
        finally:
            sfpmod.sff8436InterfaceId.parse_dom_capability = lambda self, r, o: {
                "data": {"Tx_power_support": {"value": "off"}}}
            sfpmod.sff8436Dom.parse_sfp_dom_rev = lambda self, r, o: {
                "data": {"dom_rev": {"value": "SFF-8436 Rev 1.0"}}}


class TestGetTransceiverDomThresholdDeep:
    def test_qsfp_threshold_read_returns_none(self, sfp):
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return bytearray(width)

        m = mock_open(read_data=b'\x00' * 256)
        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", m):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_dom_threshold_info_dict(0)
        assert result is None

    def test_qsfp_threshold_close_ioerror(self, sfp):
        mock_file = MagicMock()
        mock_file.close.side_effect = IOError("close fail")

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", return_value=mock_file):
                with patch("io.open", return_value=mock_file):
                    result = sfp.get_transceiver_dom_threshold_info_dict(0)
        assert result is None

    def test_qsfp_threshold_all_fields(self, sfp):
        m = mock_open(read_data=b'\x00' * 512)
        with patch("builtins.open", m):
            with patch("io.open", m):
                result = sfp.get_transceiver_dom_threshold_info_dict(0)
        assert result["temphighalarm"] == "70"
        assert result["templowalarm"] == "0"
        assert result["vcchighalarm"] == "3.6"
        assert result["rxpowerhighalarm"] == "5"
        assert result["txbiashighalarm"] == "10"


class TestSFPPortPaths:
    """Test the SFP (non-QSFP) code paths by making port not in qsfp_ports."""

    def test_sfp_port_transceiver_info(self, sfp):
        """Cover the SFP branch in get_transceiver_info_dict."""
        sfp.PORTS_IN_BLOCK = -1  # Make port 0 NOT a QSFP port
        sfp.osfp_ports = []
        m = mock_open(read_data=b'\x00' * 256)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_info_dict(0)
        assert result is not None
        assert result["type"] == "QSFP28"
        assert result["vendor_oui"] == "00-90-65"
        assert result["vendor_date"] == "2020-01-15"

    def test_sfp_port_transceiver_info_none_reads(self, sfp):
        """Cover None returns in SFP branch."""
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 3:  # vendor_pn
                return None
            return bytearray(width)

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_info_dict(0)
        assert result is None

    def test_sfp_port_dom_info(self, sfp):
        """Cover the SFP branch in get_transceiver_dom_info_dict."""
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        m = mock_open(read_data=b'\x00' * 512)
        with patch("builtins.open", m):
            result = sfp.get_transceiver_dom_info_dict(0)
        assert result is not None
        assert result["temperature"] == "25.5"
        assert result["rx1power"] == "-1.0"
        assert result["rx2power"] == "N/A"
        assert result["tx1bias"] == "6.0"
        assert result["tx2bias"] == "N/A"

    def test_sfp_port_dom_info_eeprom_not_present(self, sfp):
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        with patch.object(sfp, "_sfp_eeprom_present", return_value=False):
            result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_sfp_port_dom_info_ioerror(self, sfp):
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", side_effect=IOError("fail")):
                result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_sfp_port_dom_info_read_none(self, sfp):
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        call_count = [0]

        def fake_read(sysfs, offset, width):
            call_count[0] += 1
            if call_count[0] == 2:
                return None
            return bytearray(width)

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", mock_open(read_data=b'\x00' * 256)):
                with patch.object(sfp, "_read_eeprom_specific_bytes", side_effect=fake_read):
                    result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_sfp_port_dom_close_ioerror(self, sfp):
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        mock_file = MagicMock()
        mock_file.close.side_effect = IOError("close fail")

        with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
            with patch("builtins.open", return_value=mock_file):
                result = sfp.get_transceiver_dom_info_dict(0)
        assert result is None

    def test_sfp_port_threshold_info(self, sfp, sfpmod):
        """Cover SFP threshold path - references SFP_MODULE_THRESHOLD_OFFSET."""
        sfp.PORTS_IN_BLOCK = -1
        sfp.osfp_ports = []
        # Inject the missing constant to cover the path
        sfpmod.__dict__["SFP_MODULE_THRESHOLD_OFFSET"] = 0
        sfpmod.__dict__["SFP_MODULE_THRESHOLD_WIDTH"] = 40
        m = mock_open(read_data=b'\x00' * 512)
        try:
            with patch.object(sfp, "_sfp_eeprom_present", return_value=True):
                with patch("io.open", m):
                    result = sfp.get_transceiver_dom_threshold_info_dict(0)
            assert result is not None
            assert result["temphighalarm"] == "70"
        finally:
            del sfpmod.__dict__["SFP_MODULE_THRESHOLD_OFFSET"]
            del sfpmod.__dict__["SFP_MODULE_THRESHOLD_WIDTH"]
