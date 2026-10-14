#!/usr/bin/env python
#
# Description: To handle platform specific dynamic configuration constraints.
#
# Copyright (c) 2025, Juniper Networks, Inc.
# All rights reserved.
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
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
from swsscommon.swsscommon import ConfigDBConnector
import sonic_platform.platform

## HELPER FUNCTIONS

def get_fp_port(interface):
    match = re.match(r'Ethernet(\d+)', interface)
    return match.group(1)

def get_port_pair_OD(interface):
    fp_port = int(get_fp_port(interface)) / 8
    pair_port = 0

    match (fp_port % 4):
        case 0:
            pair_port = fp_port + 3
        case 1:
            pair_port = fp_port + 1
        case 2:
            pair_port = fp_port - 1
        case 3:
            pair_port = fp_port - 3

    pair_port = int(pair_port*8)

    return f'Ethernet{pair_port}'

def get_port_pair_QD(interface):
    fp_port = int(get_fp_port(interface)) / 8
    pair_port = 0

    match (fp_port % 4):
        case 0:
            pair_port = fp_port + 1
        case 1:
            pair_port = fp_port - 1
        case 2:
            pair_port = fp_port + 1
        case 3:
            pair_port = fp_port - 1

    pair_port = int(pair_port*8)

    return f'Ethernet{pair_port}'

def breakout(interface_name, target_brkout, breakout_file_input):
    ## Find the port pair and it's breakout config
    #  0 <--> 3, 1 <--> 2 for Garnet OD
    #  0 <--> 1, 2 <--> 3 for Garnet QD
    chassis = sonic_platform.platform.Platform().get_chassis()
    hwsku = chassis.get_hwsku()
    if hwsku == "Juniper-QFX5241-64-QD":
        pair_port = get_port_pair_QD(interface_name)
    else:
        pair_port = get_port_pair_OD(interface_name)

    config_db = ConfigDBConnector()
    config_db.connect()

    cur_brkout_dict = config_db.get_table('BREAKOUT_CFG')
    if len(cur_brkout_dict) == 0:
        return False

    cur_pport_brkout = cur_brkout_dict[pair_port]["brkout_mode"]

    if int(target_brkout.split('x')[0]) + int(cur_pport_brkout.split('x')[0]) > 10:
        return False

    return True
