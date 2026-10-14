"""Guard the QFX5241 PDDF JSON against the PSU EEPROM address collision.

i2c-0 (CPU SMBus) is the parent of the PCA9548 muxes. i2c_i801 registers an SPD
client there whose address depends on the DIMM slots, so a 24c02 PSU EEPROM at
0x50/0x51 on a mux channel can be refused with -EBUSY (-16). The PSU EEPROMs are
therefore not instantiated through PDDF; PMBus supplies the PSU data.
"""

import json
import pathlib

PDDF_JSON = pathlib.Path(__file__).resolve().parents[1] / "pddf" / "pddf-device.json"


def _load():
    return json.loads(PDDF_JSON.read_text())


def test_psu_eeprom_clients_are_not_declared():
    data = _load()
    assert not [k for k in data if k.startswith("PSU") and k.endswith("_EEPROM")]
    assert "psu_eeprom" not in data["PLATFORM"]["pddf_dev_types"]["PSU"]


def test_psu_interfaces_are_pmbus_only():
    data = _load()
    for psu in ("PSU1", "PSU2"):
        assert data[psu]["i2c"]["interface"] == [
            {"itf": "pmbus", "dev": psu + "_PMBUS"}]


def test_every_interface_and_channel_reference_resolves():
    data = _load()
    dangling = []
    for key, dev in data.items():
        if not isinstance(dev, dict):
            continue
        i2c = dev.get("i2c", {})
        for itf in i2c.get("interface", []):
            if itf["dev"] not in data:
                dangling.append((key, itf["dev"]))
        for chan in i2c.get("channel", []):
            if chan["dev"] not in data:
                dangling.append((key, chan["dev"]))
    assert dangling == []
