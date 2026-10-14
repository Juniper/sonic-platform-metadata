#
# Name: led_control.py version: 1.0
#
# Description: Platform-specific LED control functionality for Juniper QFX5200
#
# Copyright (c) 2020, Juniper Networks, Inc.
# All rights reserved.
#
# Notice and Disclaimer: This code is licensed to you under the GNU General
# Public License as published by the Free Software Foundation, version 3 or
# any later version. This code is not an official Juniper product. You can
# obtain a copy of the License at <https://www.gnu.org/licenses/>
#
# OSS License:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Third-Party Code: This code may depend on other components under separate
# copyright notice and license terms.  Your use of the source code for those
# components is subject to the terms and conditions of the respective license
# as noted in the Third-Party source code file.

try:
    from sonic_led.led_control_base import LedControlBase
    import os
    import syslog
    import glob
    from socket import *
    from select import *
except ImportError as e:
    raise ImportError(str(e) + " - required module not found")


def DBG_PRINT(str):
    syslog.openlog("ledi_control")
    syslog.syslog(syslog.LOG_INFO, str)
    syslog.closelog()


class LedControl(LedControlBase):
    """Platform specific LED control class"""
    LED_SYSFS_PATH = "/sys/class/leds/fpc0-port{0}/brightness"

    LED_COLOR_OFF = 0
    LED_COLOR_RED = 1
    LED_COLOR_GREEN = 2
    LED_COLOR_AMBER = 3
    LED_COLOR_SFP_GREEN = 1

    def __init__(self):
        # TODO:
        # 1. Handle partial interface link up for a port
        # 2. Red for any faults in the port (speed unsupported, fec, traffic, etc)
        # 3. Amber for warnings etc
        OSFP_PORTS = 64
        SFP_PORTS = 2
        OSFP_INTERFACES = OSFP_PORTS * 8
        SFP_INTERFACES = SFP_PORTS * 1

        self.portMapping = {}

        for lane in range(OSFP_INTERFACES):
            self.portMapping[f"Ethernet{lane}"] = lane //8

        for lane in range(OSFP_INTERFACES, OSFP_INTERFACES + SFP_INTERFACES):
            self.portMapping[f"Ethernet{lane}"] = OSFP_PORTS + (lane %8)

    def _changeLedColor(self, port, color):
        path = self.LED_SYSFS_PATH.format(self.portMapping[port])

        with open(path, 'w') as f:
            f.write(str(color))

    def port_link_state_change(self, port, state):
        """
        Called when port link state changes. Update port link state LED here.

        :param port: A string, SONiC port name (e.g., "Ethernet0")
        :param state: A string, the port link state (either "up" or "down")
        """
        if port in self.portMapping:
            if state == "up":
                if port in ["Ethernet512" ,"Ethernet513"]:
                    self._changeLedColor(port, self.LED_COLOR_SFP_GREEN)
                else:
                    self._changeLedColor(port, self.LED_COLOR_GREEN)
            elif state == "down":
                self._changeLedColor(port, self.LED_COLOR_OFF)
        else:
            return
