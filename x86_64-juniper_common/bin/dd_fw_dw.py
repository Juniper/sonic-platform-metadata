
#!/usr/bin/python3
"""
Utility to download firmware for QSFP-DD modules
"""
from subprocess import check_output, DEVNULL, CalledProcessError
import argparse
import re
import os
import sys
from itertools import repeat
from time import sleep
import logging
import datetime
from struct import pack, unpack
import string
import copy
from collections import OrderedDict
import multiprocessing
from enum import Enum

import sonic_platform.platform

ADDR_CDB_STATUS = 37
ADDR_PASSWORD = 122
ADDR_PAGE_SEL = 127

# Initializing loggers. One for displaying info messages and
# the other for printing error messages. I wanted to have
# different formatters for these error and info messages.
#display.setLevel(logging.INFO)
display = logging.getLogger('display-logs')
display.setLevel(logging.INFO)
dump = logging.getLogger('error-logs')
dump.setLevel(logging.ERROR)

info = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s: %(message)s')
info.setFormatter(formatter)

#Create a logs directory if not present
if not os.path.exists('/var/tmp/qsfp_dd_fwupdate'):
    os.makedirs('/var/tmp/qsfp_dd_fwupdate')

if os.path.isfile('/var/tmp/qsfp_dd_fwupdate/fw_upgrade.log'):
    os.remove('/var/tmp/qsfp_dd_fwupdate/fw_upgrade.log')

if os.path.isfile('/var/tmp/qsfp_dd_fwupdate/fw_upgrade_err.log'):
    os.remove('/var/tmp/qsfp_dd_fwupdate/fw_upgrade_err.log')

info_file = logging.FileHandler('/var/tmp/qsfp_dd_fwupdate/fw_upgrade.log')
info_file.setFormatter(formatter)

error = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s: %(process)d  %(message)s')
error.setFormatter(formatter)

error_file = logging.FileHandler('/var/tmp/qsfp_dd_fwupdate/fw_upgrade_err.log')
error_file.setFormatter(formatter)

display.addHandler(info)
display.addHandler(info_file)

dump.addHandler(error)
dump.addHandler(error_file)

def display_header(message):
    """
    Helper function to print a formatted Header
    @args
        message (str): Header content
    @return
        None
    """
    display.info("")
    display.info(message)
    display.info("="*50)

    return message

def display_kv(key, value):
    """
    Helper function to print a key values pair of the form
        Image A        : 2.1
        Image B        : 2.2
    @args
        key (str): Field description
        value (any type) : Field value
    """
    display.info("{:>4}{:<40}:  {}".format(' ', key, value))
    return ("{:<4}{:<4}: {}".format(' ',key, value))

def get_sonic_port_index(name):
    """
    Translate a SONiC interface name to its platform transceiver index.

    The 1.6T platform exposes each 8-lane port as Ethernet<N>, where N is
    the first lane index. The Junos-style alias maps directly to the same
    physical port number.
    @args
        name (str): SONiC interface name (Ethernet<N>) or alias (et-f/p/n)
    @return
        int: platform transceiver index
    """
    match = re.fullmatch(r'Ethernet(\d+)', name)
    if match:
        first_lane = int(match.group(1))
        if first_lane % 8:
            raise ValueError("{} is not an 8-lane 1.6T port".format(name))
        return first_lane // 8

    match = re.fullmatch(r'et-\d+/\d+/(\d+)', name)
    if match:
        return int(match.group(1))

    raise ValueError("unsupported interface '{}'; use Ethernet<N> or et-f/p/n".format(name))

class Status(Enum):

    SUCCESS = 0
    #CDB Failure
    CDB_BLOCK_BUSY_AFTER_TIMEOUT = 1
    CDB_BLOCK_FAILURE = 2
    CDB_ADV_BLOCK_READ_FAILURE = 3
    CDB_COMMAND_EXE_FAILURE = 4
    CDB_STATUS_CHECK_FAILURE = 5
    CDB_LPL_WRITE_FAILURE = 6
    CDB_COMMAND_WRITE_FAILURE = 7
    CDB_STATUS_UNSUCCESSFUL = 8
    CDB_RESPONSE_READ_FAILURE = 9

    I2C_WRITE_FAILURE = 10
    SET_PAGE_FAILURE = 11
    MODULE_STATE_READ_FAILURE = 12
    CMIS_VERSION_READ_FAILURE = 13
    LP_STATE_READ_FAILURE = 14
    WRITE_TO_BLOCK_FAILURE = 15

    XCVR_PERIODIC_STOP_FAILURE = 16
    XCVR_PERIODIC_START_FAILURE = 17
    SET_MODULE_TO_HPM_FAILURE = 18
    PUT_MODULE_TO_HPM_FAILURE = 19

    AUTO_PAGING_UNSUPPORTED = 20
    MIN_TWI_BYTES_ERROR = 21
    FW_DOWNLOAD_UNSUPPORTED = 22
    OPTIC_SOFT_INSERT_REMOVE_FAILURE = 23
    OPTICS_GET_I2C_ADDRESS_FAILURE = 24
    OPTICS_GET_NUMBER_OF_CHANNELS = 25
    OPTICS_SERDES_RETRY_ENABLE_DISABLE_FAILURE = 26


class Xcvr:
    """
    Object to perform operations on optics. Qsfp-DD firmware download
    involves many steps. This class helps in defining modular functions
    and an elegant way to exchange information between them
    """
    # pylint: disable=too-many-instance-attributes
    # need all of the arguments
    def __init__(self, port_n, verbose, optic_map):
        self.status = Status.SUCCESS
        self.port = port_n
        self.verbose = verbose
        self.i2c_dev, i2c_status = self.__get_i2c_address()
        self.header_bytes = 0
        self.bsize = 8
        self.start_max_ms = 40
        self.abort_max_ms = 40
        self.write_max_ms = 40
        self.complete_max_ms = 40
        self.copy_max_ms = 40
        self.fw_file = None
        self.file_size = 0
        self.max_twi_bytes = 128
        self.auto_paging = True
        self.password = list(unpack('BBBB', pack('>i', optic_map["password"])))
        self.only_lpl_supported = False
        self.intf_map = optic_map
        self.num_chans, chan_status = self.__get_num_chans()
        self.hitless_upgrade = True
        if (i2c_status) !=  Status.SUCCESS:
            self.status = i2c_status
        elif (chan_status) !=  Status.SUCCESS:
            self.status = chan_status

    def __get_i2c_address(self):
        """
        Resolve the transceiver's I2C bus through SONiC Platform API.
        Different sonic_platform implementations expose the bus number
        differently (inventory-based Sfp has _xcvr.getI2cBus(), PDDF-based
        Sfp only exposes get_eeprom_path()/_mux_bus), so try each in turn.
        @args
            None
        @return
            i2c_dev (int): /dev/i2c-%d handle for the platform port
        """
        try:
            chassis = sonic_platform.platform.Platform().get_chassis()
            sfp = chassis.get_sfp(self.port)

            if hasattr(sfp, '_xcvr'):
                return sfp._xcvr.getI2cBus(), Status.SUCCESS

            if hasattr(sfp, '_mux_bus') and sfp._mux_bus is not None:
                return sfp._mux_bus, Status.SUCCESS

            eeprom_path = sfp.get_eeprom_path()
            match = re.search(r'i2c-(\d+)', eeprom_path)
            if match:
                return int(match.group(1)), Status.SUCCESS

            dump.error("Could not determine I2C bus for port %s", self.port)
            return 0, Status.OPTICS_GET_I2C_ADDRESS_FAILURE
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            dump.error("Failed to resolve I2C bus for port %s: %s", self.port, error)
            return 0, Status.OPTICS_GET_I2C_ADDRESS_FAILURE

    def __get_num_chans(self):
        """
        SONiC does not expose the Junos channel retry controls. This value is
        retained only for compatibility with the shared firmware workflow.
        @args
            None
        @return
        """
        return 0, Status.SUCCESS

    def __run(self, cmd):
        """
        Wrapper for check_output. Handles errors and prints debug info
        @args
            cmd (list): command name and arguments as list, as expected
                        by subporcess.check_output()
        @return
            res (bool): True for successful cmd execution. False otherwise
            output (str): output from command execution
        """
        status = Status.SUCCESS
        output = ''
        try:
            if self.verbose:
                output = check_output(cmd).decode('utf-8').strip()
            else:
                output = check_output(cmd, stderr=DEVNULL).decode('utf-8').strip()
        except (CalledProcessError, OSError):
            if self.verbose:
                status = Status.CDB_COMMAND_EXE_FAILURE
                dump.error("Failed to execute command %s", ' '.join(cmd))
            return False, output, status
        if self.verbose:
            dump.info("\n\tcmd:    %s\n\toutput: %s", ' '.join(cmd), output)
        return True, output, status


    def __i2c_read(self, addr, length):
        """
        Read bytes from EEPROM through an i2ctransfer transaction.
        @args
            addr (int): EEPROM register starting address
            length (int): Number of bytes to read from starting address
        @return
            res (bool): True if I2C read is successful, False otherwise
            output (list): Bytes read back from EEPROM as list
        """
        status = Status.SUCCESS
        cmd = ['i2ctransfer', '-f', '-y', str(self.i2c_dev), 'w1@0x50',
               '0x{:02x}'.format(addr), 'r{}@0x50'.format(length)]
        res, out, status = self.__run(cmd)
        if not res:
            return res, out

        out_int_list = [int(n, 0) for n in out.split()]
        return res, out_int_list


    def __i2c_write(self, addr, values):
        """
        Write bytes from EEPROM through an i2ctransfer transaction.
        @args
            addr (int): EEPROM register starting address
            values (list): bytes to write to device from starting address
        @return
            res (bool): True if I2C read is successful, False otherwise
            output (list): output from i2c_set if any
        """
        cmd = ['i2ctransfer', '-f', '-y', str(self.i2c_dev),
               'w{}@0x50'.format(len(values) + 1), '0x{:02x}'.format(addr)]
        cmd.extend('0x{:02x}'.format(value) for value in values)
        res, out, status = self.__run(cmd)

        return res, status
    def __get_cmis_rev(self):
        res, out = self.__i2c_read(1, 1)
        return res, out[0]

    def is_password_supported(self):
        res, cmis_version = self.__get_cmis_rev()
        if not res:
            return False
        """ CMIS 5.0 or plus does not implement password support for cdb cmd 0x41 and byte 136"""
        if cmis_version == 0x40 or cmis_version == 0x41:
            return True
        return False

    def __page_sel(self, page):
        """
        Write to EEPROM byte 127 to select High mem page
        @args
            page (int): High mem page, for ex. 0x10, 0x9f etc
        @return
            res (bool): True if page sel is successful, False otherwise
        """
        return self.__i2c_write(ADDR_PAGE_SEL, [page])

    def retry_cdb(self):
        res, out = self.__i2c_read(ADDR_CDB_STATUS, 1)
        if not res:
            return res, 0

        cdb_st = out[0]
        return res, cdb_st

    def __check_cdb_status(self, timeout):
        """
        Read byte 37 to check CDB status. If CDB status indicates module is busy,
        keeps checking for status once every 10ms, until time diff exceeds given
        timeout
        @args
            timeout (int): Max ms to wait if module CDB status is STS_BUSY
        @return
            res (bool): True if CDB status is successful, False otherwise
        """
        res = False
        status = Status.SUCCESS
        """
        latch_try_max = 1000
        latch_try = 0

        while latch_try < latch_try_max:
            res, out = self.__i2c_read(8, 1)
            if not res:
                display.warning("    I2C read NACK. Waiting..")
                sleep(0.01)
                continue

            cdb_latch = out[0] & 0x40

            if cdb_latch == 0x40:
                break

            dump.warning("CDB block wait for latch ({})".format(cdb_latch))
            sleep(0.01)
            latch_try = latch_try + 1
        """

        start = datetime.datetime.now()
        while True:
            res, cdb_st = self.retry_cdb()
            if not res:
                display.warning("    I2C read NACK. Waiting..")
                sleep(0.01)
                continue

            if (cdb_st == 0xff):
                """
                Retrying status register read
                """
                sleep(2)
                res, cdb_st = self.retry_cdb()

                if not res:
                    display.warning("    I2C read NACK. Waiting..")
                    sleep(0.01)
                    continue

            if (cdb_st & 0xc0) == 0:
                res = True
                break

            if cdb_st & 0x40:
                fail_reason = {0x0: "Reserved",
                               0x1: "CMD code unknown",
                               0x2: "Parameter range error",
                               0x3: "Previous CMD not aborted",
                               0x4: "CMD timeout",
                               0x5: "Check code error",
                               0x6: "Password error"}

                err_num = cdb_st & 0x3f
                err_str = fail_reason.get(err_num, "unknown")
                status = Status.CDB_BLOCK_FAILURE
                dump.error("CDB block failed (err=%d, %s)", err_num, err_str)
                res = False
                break

            if cdb_st & 0x80:
                dump.warning("CDB block busy")
                now = datetime.datetime.now()
                delta_ms = ((now - start).total_seconds()) * 1000
                if delta_ms > timeout:
                    status = Status.CDB_BLOCK_BUSY_AFTER_TIMEOUT
                    dump.error("CDB block busy even after timeout %dms", delta_ms)
                    res = False
                    break
                sleep(0.01)

        return res, status


    def __execute_cdb_cmd(self, cmd, lpl, timeout, epl_m=0, epl_l=0, readback=True):
        """
        Executes a CDB command, waits for CDB status ok returns module's
        response to command. QSFP-DD modules respond to CDB commands by
        filling up bytes 136 - 255 of page 0x9F of EEPROM. This function
        returns those 120 bytes as a list
        @args
            cmd (int): CDB command. Refer Table 9-1 to Table 9-15 in CMIS 4.0
            lpl (list): Arguments to CDB cmd. To be filled up in bytes 136-255
                        of page 0x9F by host.
            timeout (int): Max time in ms a module can take to respond to CDB
                           command
        @return
            res (bool): True if CDB command execution is successful, False
                        otherwise
            reply (list): Module response to CDB command
        """
        # pylint: disable=too-many-arguments
        status = Status.SUCCESS
        reply = []

        for i in range(3):
        #Step 1. Set page to 0x9f
            res = self.__page_sel(0x9f)
            if not res:
                status = Status.SET_PAGE_FAILURE
                dump.error("Failed to set page")
                return res, reply, status

            res, out = self.__i2c_read(ADDR_PAGE_SEL, 1)
            if (out == [159]):
                break
            else:
                sleep(0.5)

        #Step 2. Calculate checksum for bytes 128 - (135 + len(lpl))
        #        bytes 133, 134, 135 needs to be 0 for checksum
        #        refer Table 8-106 in CMIS 4.0
        lpl_info = [epl_m, epl_l, len(lpl), 0, 0, 0] + lpl
        chk_sum = sum(cmd + lpl_info) % 256
        lpl_info[3] = ~chk_sum + (2**8)

        #Step 3. Fill up LPL bytes
        pad_size = 126 - len(lpl_info)
        pad = list(repeat(0, pad_size))
        lpl_info = lpl_info + pad
        res, status = self.__i2c_write(130, lpl_info)

        if not res:
            status = Status.CDB_LPL_WRITE_FAILURE
            dump.error("Failed to write CDB LPL")
            return res, reply, status

        #Step 4. Write CDB command
        res, status  = self.__i2c_write(128, cmd)
        if not res:
            status = Status.CDB_COMMAND_WRITE_FAILURE
            dump.error("Failed to write CDB command")
            return res, reply, status

        #Step 5. Wait until module finishes processing request
        res , status = self.__check_cdb_status(timeout)
        if not res:
            status = Status.CDB_STATUS_UNSUCCESSFUL
            dump.error("CDB status unsuccessful")
            return res, reply, status

        if not readback:
            return res, reply

        #Step 6. Read 120 bytes response from module
        res, reply = self.__i2c_read(136, 120)
        if not res:
            status = Status.CDB_RESPONSE_READ_FAILURE
            dump.error("Failed to read CDB response")
        return res, reply, status

    def stop_periodic(self):
        """
        stop xcvr periodic. Need to do this before starting FW upgrade
        Failure to stop periodic will lead to termination of script
        @args
            None
        @return
            None
        """

        display.info("SONiC xcvrd polling is not controlled by this utility")
        return Status.SUCCESS


    def start_periodic(self):
        """
        start xcvr periodic. Need to do this after completing FW upgrade
        Failure to start periodic will lead to termination of script
        @args
            None
        @return
            None
        """
        return Status.SUCCESS

    def write_password(self):
        """
        Some module require password to unlock firmware download
        password is written bytes 122-125 of low mem page.
        Failure to write password will lead to termination of script
        @args
            password (list): 4 byte password as a list of bytes
        @return
            None
        """
        status = Status.SUCCESS
        res, status = self.__i2c_write(ADDR_PASSWORD, self.password)
        if not res:
            status = Status.I2C_WRITE_FAILURE
            dump.error("I2C write failed for password entry")
            return status
        return status

    @staticmethod
    def __display_cdb_impl(cdb_ad):
        """
        Decode byte 163:7-6 of Page 0x1.
        Refer Table 8-35 of CMIS 4.0
        """
        cdb_impl_map = {0x00: "CDB not implemented",
                        0x40: "Only one instance of CDB",
                        0x80: "Two instance of CDB",
                        0xC0: "Reserved"}
        display_kv("CDB Implemented", cdb_impl_map.get(cdb_ad))

    @staticmethod
    def __display_cdb_bg_operation(cdb_oper):
        """
        Decode byte 163:5 of Page 0x1.
        Refer Table 8-35 of CMIS 4.0
        """
        cdb_oper_map = {0x00: "not implemented",
                        0x20: "implemented"}
        display_kv("Background CDB operation", cdb_oper_map.get(cdb_oper))

    @staticmethod
    def __display_cdb_auto_paging(cdb_ap):
        """
        Decode byte 163:4 of Page 0x1.
        Refer Table 8-35 of CMIS 4.0
        """
        cdb_ap_map = {0x00: "not implemented",
                      0x10: "implemented"}
        display_kv("CDB Auto Paging", cdb_ap_map.get(cdb_ap))

    @staticmethod
    def __display_cdb_epl_pages(cdb_epl):
        """
        Decode byte 163:3-0 of Page 0x1.
        Refer Table 8-35 of CMIS 4.0
        """
        cdb_epl_map = {0x00: "A0-AFh not implemented",
                       0x01: "A0h implemented",
                       0x02: "A0-A1h implemented",
                       0x03: "A0-A2h implemented",
                       0x04: "A0-A3h implemented",
                       0x05: "A0-A7h implemented",
                       0x06: "A0-ABh implemented",
                       0x07: "A0-AFh implemented"}
        display_kv("EPL Pages", cdb_epl_map.get(cdb_epl))

    def get_cdb_supported_info(self):
        """
        Decodes CDB supported features for a module. This info
        is present in bytes 163-166 of High mem page 0x1
        Refer Table 8-35 of CMIS 4.0
        Failures to get information will lead to termination
        of the script
        """
        status = Status.SUCCESS
        res = self.__page_sel(0x1)
        if not res:
            status = Status.SET_PAGE_FAILURE
            dump.error("Failed to set page")
            return status

        res, out = self.__i2c_read(163, 4)
        if not res:
            status = Status.CDB_ADV_BLOCK_READ_FAILURE
            dump.error("Failed to read CDB adv byte")
            return status

        display_header("CDB features")
        Xcvr.__display_cdb_impl(out[0] & 0xc0)
        Xcvr.__display_cdb_bg_operation(out[0] & 0x20)
        self.auto_paging = out[0] & 0x10
        Xcvr.__display_cdb_auto_paging(self.auto_paging)
        Xcvr.__display_cdb_epl_pages(out[0] & 0xf)
        self.max_twi_bytes = (out[1] + 1) * 8
        display_kv("Max TWI Bytes per write", self.max_twi_bytes)
        self.max_twi_bytes =  128 # override. Kernel limitation on evo
        if out[2] & 0x80:
            display_kv("CDB command processing", "On STOP bit")
        else:
            display_kv("CDB command processing", "On write to byte 129")
        tnack_time = (out[2] & 0xf) * 160
        display_kv("CDB tNACK time (ms)", tnack_time)
        tnack_ind = (out[3] & 0x80) >> 7
        display_kv("tNACK time indicator", tnack_ind)
        tnack_max_time = 80 - (out[3] & 0x7f)
        display_kv("Max tNACK time (ms)", tnack_max_time)
        return status

    @staticmethod
    def __display_feature_password(password_type):
        """
        Decodes byte 136 in Module Return LPL of CDB cmd 0x41h
        Refer Table 9-10 in CMIS 4.0

        Rev:1 CMIS 5.0 has marked 136 as reserved. Hence below logic is
        not true for CMIS 5.0 and later
        """
        fw_pass_map = {0x0 : "Unlocked by vendor password",
                       0x1 : "Unlocked by vendor password sequence",
                       0x80 : "Unlocked by standard MSA password"}
        display_kv("FW download password type", fw_pass_map.get(password_type,
                                                            "Unknown"))

    @staticmethod
    def __display_feature_copy_abort(copy_abort):
        """
        Decodes byte 137 in Module Return LPL of CDB cmd 0x41h
        Refer Table 9-10 in CMIS 4.0
        """
        copy_abort_map = {0x1: "supported",
                          0x2: "supported",
                          0x4: "supported",
                          0x80: "supported"}
        display_kv("Abort 102h", copy_abort_map.get(copy_abort & 0x1, "not supported"))
        display_kv("Copy 108h", copy_abort_map.get(copy_abort & 0x2, "not supported"))
        display_kv("Skip erased", copy_abort_map.get(copy_abort & 0x4, "not supported"))
        display_kv("Image readback", copy_abort_map.get(copy_abort & 0x80, "not supported"))

    @staticmethod
    def __module_state_str(mstate_info):
        """
        Decode byte 3:3-1 of Lower Memory page.
        Refer Table 8-7 of CMIS 5.2
        """
        mstate_str_map = {0x0 : "Reserved0",
                          0x1 : "ModuleLowPwr",
                          0x2 : "ModulePwrUp",
                          0x3 : "ModuleReady",
                          0x4 : "ModulePwrDn",
                          0x5 : "Fault",
                          0x6 : "Reserved6",
                          0x7 : "Reserved7"}
        mstate_str = mstate_str_map[(mstate_info & 0xe) >> 1]
        return mstate_str

    def get_download_features(self):
        """
        Displays FW download supported features and also stores some info
        for future use. This function decodes Module returned LPL to CDB
        command 0x41. Refer Table 9-10 in CMIS 4.0.
        Failure to execute CDB cmd 0x41 results in termination of script
        @args
            None
        @return
            None
        """
        status = Status.SUCCESS
        cmd = [0x00, 0x41]
        lpl = []
        res, out, status = self.__execute_cdb_cmd(cmd, lpl, self.write_max_ms)

        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute CDB command")
            return status

        display_header("Firmware download supported features")
        if (self.is_password_supported()) :
            Xcvr.__display_feature_password(out[0])
        Xcvr.__display_feature_copy_abort(out[1])
        """
        0b = max duration multiplier M is 1
        1b = max duration multiplier M is 10
        This bit encodes a multiplier value M which governs the interpretation of values
        found in the U16 array of advertised max durations in Bytes 144-153 of CDB message 0x41:
        These advertised values are multiplied by M.
        """
        multiplier = 1
        if out[1] and 0x08:
            multiplier = 10
        self.header_bytes = out[2]
        display_kv("Start command payload size", self.header_bytes)
        display_kv("Erased byte", out[3])

        self.bsize = (out[4] + 1) * 8
        display_kv("Max FW download block size", self.bsize)

        lpl_epl_map = {0x0: "None supported",
                       0x1: "LPL supported",
                       0x2: "EPL supported",
                       0x3: "Both supported"}
        self.only_lpl_supported = out[5] == 0x1
        display_kv("Write LPL/EPL", lpl_epl_map.get(out[5], "Unknown"))
        display_kv("Only LPL supported", self.only_lpl_supported)
        display_kv("Readback LPL/EPL", lpl_epl_map.get(out[6], "Unknown"))
        if out[7] == 1:
            self.hitless_upgrade = True
            display_kv("Hitless mode", "supported")
        else:
            self.hitless_upgrade = False
            display_kv("Hitless mode", "not supported")

        self.start_max_ms = out[8] << 8 | out[9]
        self.start_max_ms = self.start_max_ms * multiplier
        self.abort_max_ms = out[10] << 8 | out[11]
        self.abort_max_ms = self.abort_max_ms * multiplier
        self.write_max_ms = out[12] << 8 | out[13]
        self.write_max_ms = self.write_max_ms * multiplier
        self.complete_max_ms = out[14] << 8 | out[15]
        self.complete_max_ms = self.complete_max_ms * multiplier
        self.copy_max_ms = out[16] << 8 | out[17]
        self.copy_max_ms = self.copy_max_ms * multiplier
        display_kv("CDB start max time (ms)", self.start_max_ms)
        display_kv("CDB abort max time (ms)", self.abort_max_ms)
        display_kv("CDB write max time (ms)", self.write_max_ms)
        display_kv("CDB complete max time (ms)", self.complete_max_ms)
        display_kv("CDB copy max time (ms)", self.copy_max_ms)
        return status

    @staticmethod
    def __display_fw_status(raw):
        """
        Decodes byte 136 of in Module returned LPL for CDB cmd 0x100
        Refer Table 9-16 in CMIS 4.0
        """
        fw_status_list = []
        if raw == 0:
            display_kv("Factory Boot Image", "running")
            return

        img_status_map = {0x1 : "running",
                          0x2 : "committed",
                          0x3 : "running and committed",
                          0x4 : "erased / empty",
                          0x8 : "reserved"}

        imga_status = raw & 0xf
        fw_status_list.append(display_kv("Image A", img_status_map.get(imga_status, "unknown")))

        imgb_status = (raw & 0xf0) >> 4
        fw_status_list.append(display_kv("Image B", img_status_map.get(imgb_status, "unknown")))
        return fw_status_list

    @staticmethod
    def __decode_str(byte_set):
        """
        Decodes a byte list to a printable version
        Some modules represent version in ascii format while some
        use integer format. For example if version is 2.1 some store
        32, 31 in eeprom while some vendors directly store 2.1 in
        eeprom. This function tests if version is ascii and decodes
        accordingly
        """
        decoded = ''
        pset = set(string.printable) - set(string.whitespace)
        for b in byte_set:
            ascii_rep = chr(b)
            hex_rep = format(b, 'x')
            if set(ascii_rep).issubset(pset):
                decoded = decoded + ascii_rep
            else:
                decoded = decoded + hex_rep
        return decoded

    @staticmethod
    def __display_fw_info(prefix, raw_bytes):
        """
        Decodes bytes 138-255 of in Module returned LPL for CDB cmd 0x100
        Refer Table 9-16 in CMIS 4.0
        """
        fw_info_ver_list = []
        ver  = Xcvr.__decode_str([raw_bytes[0], 46, raw_bytes[1]])
        fw_info_ver_list.append(display_kv("{} version".format(prefix), ver))
        build = Xcvr.__decode_str(raw_bytes[2:4])
        fw_info_ver_list.append(display_kv("{} build".format(prefix), build))
        extra = Xcvr.__decode_str(raw_bytes[4:])
        fw_info_ver_list.append(display_kv("{} extra".format(prefix), extra))
        return fw_info_ver_list

    def get_firmware_info(self):
        """
        Gets information about current firmware by executing CDB cmd 0x100
        Refer Table 9-16 in CMIS 4.0
        Failure to execute CDB cmd 0x100 results in termination of script
        @args
            None
        @return
            None
        """
        fw_info_list = []
        fw_ver_list = []
        fw_img_a =[]
        fw_img_b = []
        fw_factory =[]
        status = Status.SUCCESS
        cmd = [0x01, 0x00]
        lpl = []
        res, out, status = self.__execute_cdb_cmd(cmd, lpl, self.write_max_ms)
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute CDB command")
            return fw_info_list, status

        fw_status = out[0]
        fw_info_list.append(display_header("Firmware status"))
        fw_status_list = Xcvr.__display_fw_status(fw_status)
        if fw_status_list:
            fw_info_list.extend(fw_status_list)

        #img_info = out[1]
        #img_info = 0x7 # eoptolink bug
        '''
          Due to coverity issue caused by the above eoptolink bug,
          removing the earlier check
          img_info & 0x1 for Image A version.
          img_info & 0x2 for Image B version.
          img_info & 0x4 for Factory Image version.
        '''
        fw_info_list.append(display_header("Firmware info"))
        fw_img_a = (Xcvr.__display_fw_info("Image A", out[2:38]))
        fw_img_b = (Xcvr.__display_fw_info("Image B", out[38:74]))
        fw_factory = (Xcvr.__display_fw_info("Factory Image", out[74:110]))

        fw_info_list.extend(fw_img_a)
        fw_info_list.extend(fw_img_b)
        fw_info_list.extend(fw_factory)

        res, mstate = self.__i2c_read(3, 1)
        if not res:
            status = Status.MODULE_STATE_READ_FAILURE
            dump.error("Failed to read module state")
            return fw_info_list, status

        mstate_str = Xcvr.__module_state_str(mstate[0])
        fw_info_list.append(display_kv("Module state", mstate_str))

        return fw_info_list, status

    def verify_cmis_revison(self):
        """
        Verify if CMIS version is 4.0+. FW download in not
        supported for CMIS 3.0. CMIS compliance information
        can be found in byte 1 ox low mem page.
        Failure to read CMIS info from EEPROM or for CMIS ver
        is less than 4.0 script will be terminated
        """
        status = Status.SUCCESS
        res, out = self.__i2c_read(1, 1)
        if not res:
            status = Status.CMIS_VERSION_READ_FAILURE
            dump.error("Failed to read CMIS version")
            return status

        if out[0] < 0x40:
            status = Status.FW_DOWNLOAD_UNSUPPORTED
            dump.error("FW download not supported")
            return status
        return status

    def start_fw_download(self, name):
        """
        Start FW download by executing CDB cmd 0x101
        Refer Table 9-17 in CMIS 4.0.
        Failure to start firmware download will lead to
        script termination
        @args
            name (str): Name of the FW binary fine
        @return
            None
        """
        status = Status.SUCCESS
        display_header("Initiating FW download. File {}".format(name))
        self.fw_file = open(name, 'rb') #read as binary file
        self.file_size = self.fw_file.seek(0, 2) #go to end of file
        self.fw_file.seek(0) #go back to start of file

        # break down file size to 4 bytes
        # for example, if file_size = 80792 bytes
        # size_bytes = file_size.to_bytes(4, 'big') = b'\x00\x01;\x98'
        # struct.unpack('BBBB', size_bytes) = (0, 1, 59, 152)
        # finally list method converts the output to python list
        # size_unpacked = [0, 1, 59, 152]
        size_bytes = self.file_size.to_bytes(4, 'big')
        size_unpacked = list(unpack('BBBB', size_bytes))

        # First few bytes in the firmware file are header bytes
        # Number of bytes that needs to be treated as header
        # is derived from response to CDB command 0x41
        header = self.fw_file.read(self.header_bytes)
        header_unpacked = list(unpack('B'*self.header_bytes, header))

        self.file_size = self.file_size - self.header_bytes
        lpl = size_unpacked + [0, 0, 0, 0] + header_unpacked
        cmd = [0x1, 0x1]
        res = self.__execute_cdb_cmd(cmd, lpl, self.start_max_ms)[0]
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute CDB command")
            return status
        return status

    def start_no_autopaging_fw_download(self, file_name1):
        """
        Start FW download for no autopaging supported modules"
        """
        status = Status.SUCCESS
        display_header("Initiating FW download. File1 {}".format(file_name1))
        fw_file1 = open(file_name1, 'rb') #read as binary file
        file_size1 = fw_file1.seek(0, 2) #go to end of file
        fw_file1.seek(0) #go to end of file
        size_bytes1 = file_size1.to_bytes(4, 'big')
        size_unpacked1 = list(unpack('BBBB', size_bytes1))
        binFileBuff = fw_file1.read();

        epl_block_size = self.header_bytes + 8;
        cdb_buff  = [1, 1, 0, 0, epl_block_size, 0, 0, 0]
        size_bytes1_int = int.from_bytes(size_bytes1, byteorder="big")
        offset_8 = (size_bytes1_int >> 24) & 0xFF;
        offset_9 = (size_bytes1_int >> 16) & 0xFF;
        offset_10 = (size_bytes1_int >> 8) & 0xFF;
        offset_11 = (size_bytes1_int >> 0) & 0xFF;
        cdb_buff.insert(8, offset_8);
        cdb_buff.insert(9, offset_9);
        cdb_buff.insert(10, offset_10);
        cdb_buff.insert(11, offset_11);
        cdb_buff.insert(12, 0);
        cdb_buff.insert(13, 0);
        cdb_buff.insert(14, 0);
        cdb_buff.insert(15, 0);
        j = 16;
        i = 0;
        while i < self.header_bytes:
            cdb_buff.insert(j, binFileBuff[i]);
            j += 1
            i += 1
        #Calculating the code
        check_code = 0
        i = 0
        while i < (epl_block_size+8):
            check_code = check_code + cdb_buff[i]
            i += 1
        check_code = ~check_code#or check_code = 0xFF - check_code,revert bits,for example 0x55->0xAA,0x33->0xCC
        cdb_buff[0x05] = check_code
        res = self.__page_sel(0x9f)
        if not res:
            status = Status.SET_PAGE_FAILURE
            dump.error("Failed to set page")
            return status

        i = 2
        while i < (epl_block_size+8):
            addr = 0x80 + i
            self.__i2c_write(addr, [cdb_buff[i]])
            i += 1
        cmd = [0x1, 0x1]
        res, status = self.__i2c_write(0x80, cmd)
        if not res:
            status = Status.CDB_COMMAND_WRITE_FAILURE
            dump.error("Failed to write CDB START command");
            return status
        #here need add detect device 0x50,if do cmd 0101 successfully,it will take no more than 1second to erase flash rom
        #I just give simple delay here,you can optimize into while(!detectDeive) check later
        sleep(10)
        retry = 0
        while 1:
            res, val = self.__i2c_read(0x25, 1)
            if (val[0] == 1) :
                print("CDB START COMMAND is successful")
                break;
            retry += 1
            sleep(10)
            if retry == 10 :
                print("CDB Start Firmware Download command failed")
                status = Status.CDB_COMMAND_WRITE_FAILURE
                dump.error("Failed to write CDB START command");
                return status

        #WriteEPL:Do Cmd 0104h without AutoPaging(2048bytes each time)
        x = 0
        while x < ((file_size1 - self.header_bytes)/2048) :
            y = 0
            while y < 16:
                z = 0
                buf = []
                while z < 128:
                    index = self.header_bytes + x * 2048 + y * 128 + z
                    if binFileBuff.__len__() > index :
                        buf.insert(z, binFileBuff[index])
                    else:
                        break
                    z += 1
                if z == 0: #if data is end, skip to next step, do not write next page data.
                    break
                self.__i2c_write(0x7F, [0xA0+y])
                res, val = self.__i2c_read(0x7F, 1) #read back the page value
                self.__i2c_write(0x80, buf)
                self.__i2c_write(0x7F, [0xA0+y])
                res, val = self.__i2c_read(0x7F, 1) #read back the page value
                res, val = self.__i2c_read(0x80, 128) #read back the 128 bytes, please confirm whether support readbuff or not
                #check the data transfer right or not
                i = 0
                while i < len(buf):
                    if (val[i] != buf[i]):
                        print("WRITE EPL Data is Error")
                        exit(1)
                    i += 1
                buf.clear()
                y += 1
            #After write 2048bytes(16 pages),then do cmd 0104
            buf.clear()
            buf = [1,4,8,0,4,0,0,0]
            buf[2] = (int((y*128)/256) & 0xFF)
            buf[3] = (((y*128)%256) & 0xFF)
            buf.insert(8, (((x * 2048) >> 24) & 0xFF))
            buf.insert(9, (((x * 2048) >> 16) & 0xFF))
            buf.insert(10, (((x * 2048) >> 8) & 0xFF))
            buf.insert(11, 0)
            check_code = 0
            i = 0
            while i < 0xc:
                check_code = check_code + buf[i]
                i += 1
            check_code = ~check_code#or check_code = 0xFF - check_code,revert bits,for example 0x55->0xAA,0x33->0xCC
            buf[5] = check_code
            res = self.__page_sel(0x9f)
            if not res:
                status = Status.SET_PAGE_FAILURE
                dump.error("Failed to set page")
                return status
            i = 0
            while i < 10:
                self.__i2c_write(0x82+i, [buf[2+i]])
                i += 1
            cmd = [0x1,0x4]
            res, status = self.__i2c_write(0x80, cmd)
            if not res:
                status = Status.CDB_COMMAND_WRITE_FAILURE
                dump.error("Failed to write EPL command");
                return status
            sleep(0.2)
            retry = 0
            while 1:
                res, val = self.__i2c_read(0x25, 1)
                if (val[0] == 1) :
                    print("WRITE EPL COMMAND is successful")
                    break;
                retry += 1
                sleep(10)
                if retry == 10 :
                    print("Write Firmware Block EPL command failed")
                    status = Status.CDB_COMMAND_WRITE_FAILURE
                    return status
            x += 1
        #Firmware download complete
        cdbBuff = [1,7, 0, 0, 0, 0xF7, 0, 0]
        res = self.__page_sel(0x9f)
        if not res:
            status = Status.SET_PAGE_FAILURE
            dump.error("Failed to set page")
            return status

        i = 0
        while i < 6:
            self.__i2c_write(0x82+i, [cdbBuff[2+i]])
            i += 1
        cmd = [1, 7]
        res, status = self.__i2c_write(0x80, cmd)
        if not res:
            status = Status.CDB_COMMAND_WRITE_FAILURE
            dump.error("Failed to write FW Downlod Complete Command")
        sleep(0.16)
        retry = 0
        while 1:
            res, val = self.__i2c_read(0x25, 1)
            if (val[0] == 1) :
                print("CDB Firmware download complete command is successful")
                break;
            retry += 1
            sleep(10)
            if retry == 10 :
                status = Status.CDB_COMMAND_WRITE_FAILURE
                print("Firmware download complete command failed")
                return status
        #Run FW image
        cdbBuf = [1, 9, 0, 0, 4, 0xF1, 0, 0, 0, 0, 0, 0]
        res = self.__page_sel(0x9f)
        if not res:
            status = Status.SET_PAGE_FAILURE
            dump.error("Failed to set page")
            return status

        i = 0
        while i < 10:
            self.__i2c_write(0x82+i, [cdbBuf[2+i]])
            i += 1
        cmd = [0x1, 0x9]
        res, status = self.__i2c_write(0x80, cmd)
        if not res:
            dump.error("Failed to write FW run command")
            status = Status.CDB_COMMAND_WRITE_FAILURE
            return status
        #need waste about no mor than 3second to copy image
        sleep(4)
        retry = 0
        while 1:
            res, val = self.__i2c_read(0x25, 1)
            if (val[0] == 1) :
                print("CDB Run FW image command is successful")
                break;
            retry += 1
            sleep(10)
            if retry == 10 :
                dump.error("Failed to write FW run command")
                status = Status.CDB_COMMAND_WRITE_FAILURE
                return status
        #Commit FW image
        cdbBuf = [0x01, 0x0A, 0, 0, 0, 0xF4, 0, 0]
        res = self.__page_sel(0x9f)
        if not res:
            status = Status.SET_PAGE_FAILURE
            dump.error("Failed to set page")
            return status

        i = 0
        while i < 6:
            self.__i2c_write(0x82+i, [cdbBuf[2+i]])
            i += 1
        cmd = [0x1, 0xA]
        res, status = self.__i2c_write(0x80, cmd)
        if not res:
            dump.error("Failed to write FW Commit command")
            status = Status.CDB_COMMAND_WRITE_FAILURE
            return status

        sleep(0.16)
        retry = 0
        while 1:
            res, val = self.__i2c_read(0x25, 1)
            if (val[0] == 1) :
                print("CDB Commit FW image command is successful")
                break;
            retry += 1
            sleep(10)
            if retry == 10 :
                dump.error("Failed to write Commit FW image command")
                status = Status.CDB_COMMAND_WRITE_FAILURE
                return status



    def flash_new_fw_lpl(self):
        """
        Some modules support only LPL and not EPL.
        Read max block size, write to LPL payload
        run CDB command 0x103
        """
        status = Status.SUCCESS
        total_bytes = 0
        eof = False
        display_header("Starting FW download - LPL")
        display_kv("Max block size", self.bsize)
        while total_bytes < self.file_size:
            block = self.fw_file.read(self.bsize)
            if not block:
                eof = True
                break
            block_unpack = list(unpack('B'*len(block), block))
            lpl_len = list(unpack('BBBB', total_bytes.to_bytes(4, 'big')))
            lpl = lpl_len + block_unpack
            cmd = [0x1, 0x3]
            res  = self.__execute_cdb_cmd(cmd, lpl, self.write_max_ms)
            if not res:
                status = Status.CDB_COMMAND_EXE_FAILURE
                dump.error("Failed to execute CDB command 0x104")
                return status
            total_bytes = total_bytes + self.bsize
            display_kv("Finished writing bytes", total_bytes)

        #finished writing all bytes. Now execute CDB cmd 0x107
        res = self.__execute_cdb_cmd([0x1, 0x7], [], self.complete_max_ms)[0]
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute CDB command 0x107")
            return status

        display_kv("FW download completed. Bytes written", total_bytes)
        return status

    def flash_new_fw_epl(self):
        """
        Read bytes from FW file and flash it onto Module, reading
        MAX TWI bytes at a time. Makes an assumption that
        max_twi_bytes is at least 128 bytes and auto-paging is
        implemented
        """
        status = Status.SUCCESS
        total_bytes = 0
        eof = False
        display_header("Starting FW download - EPL")
        display_kv("Total file size", self.file_size)
        display_kv("Max block size", self.bsize)
        while total_bytes < self.file_size:
            block_bytes = 0
            res = self.__page_sel(0xa0)
            if not res:
                status = Status.SET_PAGE_FAILURE
                dump.error("Failed to set page. total_bytes=%d", total_bytes)
                return status

            while block_bytes < self.bsize:
                block = self.fw_file.read(self.max_twi_bytes)
                if not block:
                    eof = True
                    break

                block_unpack = list(unpack('B'*len(block), block))
                res, status = self.__i2c_write(128, block_unpack)
                if not res:
                    status = Status.WRITE_TO_BLOCK_FAILURE
                    dump.error("Failed to write block. bytes=%d blocks=%d",
                               total_bytes, block_bytes)
                    return status
                block_bytes = block_bytes + len(block)

            #Block size written to EPL, check CDB status now
            res, status = self.__check_cdb_status(self.write_max_ms)
            if not res:
                status = Status.CDB_STATUS_CHECK_FAILURE
                dump.error("CDB status check failed")
                return status

            cmd = [0x1, 0x4]
            epl_len = list(unpack('BB', block_bytes.to_bytes(2, 'big')))
            lpl = list(unpack('BBBB', total_bytes.to_bytes(4, 'big')))
            res = self.__execute_cdb_cmd(cmd, lpl, self.write_max_ms, epl_len[0], epl_len[1])[0]
            if not res:
                status = Status.CDB_COMMAND_EXE_FAILURE
                dump.error("Failed to execute CDB command 0x104")
                return status

            total_bytes = total_bytes + block_bytes
            display_kv("Finished writing bytes", total_bytes)
            if eof:
                break

        #finished writing all bytes. Now execute CDB cmd 0x107
        res = self.__execute_cdb_cmd([0x1, 0x7], [], self.complete_max_ms)[0]
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute CDB command 0x107")
            return status

        display_kv("FW download completed. Bytes written", total_bytes)
        return status

    def __module_high_power_mode(self):
        """
        Transitions module to high power mode
        """
        status = Status.SUCCESS
        res, mstate = self.__i2c_read(3, 1)
        if not res:
            status = Status.MODULE_STATE_READ_FAILURE
            dump.error("Failed to read module state")
            return res

        mstate_str = Xcvr.__module_state_str(mstate[0])
        display_kv("Module state", mstate_str)

        res, low = self.__i2c_read(26, 1)
        if not res:
            status = Status.LP_STATE_READ_FAILURE
            dump.error("Failed to read LP state")
            return res

        low[0] = low[0] & ~(1 << 6) # clear 6th bit

        res, status  = self.__i2c_write(26, low)
        if not res:
            status = Status.SET_MODULE_TO_HPM_FAILURE
            dump.error("Failed to set module to high power mode")
            return res

        display_kv("Sleep", "10s")
        sleep(10)

        res, mstate = self.__i2c_read(3, 1)
        if not res:
            status = Status.MODULE_STATE_READ_FAILURE
            dump.error("Failed to read module state")
            return res

        mstate_str = Xcvr.__module_state_str(mstate[0])
        display_kv("Module state", mstate_str)
        return True


    def run_and_commit_new_image(self):
        """
        Running and committing image. Run image command is needed to
        switch to newly downloaded firmware and commit image command
        is needed to ensure module boots up with new image after reset
        """
        status = Status.SUCCESS
        display_header("Running Image")

        for i in range(5):
            res, mstate = self.__i2c_read(3, 1)
            if not res:
                status = Status.MODULE_STATE_READ_FAILURE
                dump.error("Failed to read module state")
                return status
            mstate_str = Xcvr.__module_state_str(mstate[0])

            if (mstate_str ==  "ModuleReady"):
                break
            else:
                sleep(2)

        display_kv("Module state", mstate_str)

        cmd = [0x1, 0x9]
        lpl = [0x0, 0x1, 0x1, 0x4] #attempt hitless, wait 0x0014 ms = 20ms
        if not self.hitless_upgrade:
            lpl[1] = 0
        res = self.__execute_cdb_cmd(cmd, lpl, self.complete_max_ms, 0, 0, False)[0]
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute command 0x109")
            return status

        display_kv("Sleep", "{}s".format(self.write_max_ms/1000))
        sleep(self.write_max_ms/1000)

        res = self.__module_high_power_mode()
        if not res:
            status = Status.PUT_MODULE_TO_HPM_FAILURE
            dump.error("Failed to put module in high power mode")
            return status

        display_header("Commit running Image")
        if self.is_password_supported():
            """ CMIS 5.0 or plus does not implement password support for cdb cmd 0x41 and byte 136"""
            self.write_password()
        display_kv("Sleep", "2s")
        sleep(2)
        self.intf_map["fw_info"], status = self.get_firmware_info()
        cmd = [0x1, 0xa]
        lpl = []
        res = self.__execute_cdb_cmd(cmd, lpl, self.complete_max_ms)[0]
        if not res:
            status = Status.CDB_COMMAND_EXE_FAILURE
            dump.error("Failed to execute command 0x10a")
            return status
        return status

    def check_script_support(self, x_handle):
        """
        Script doesn't support firmware download for modules which do not
        support auto paging and do not support at least 128 bytes in single
        twi transaction
        """
        status = Status.SUCCESS
        if (x_handle.intf_map["skip_auto_paging_check"] == True):
            return status

        if not self.auto_paging:
            status = Status.AUTO_PAGING_UNSUPPORTED
            dump.error("Module doesn't support auto paging. Script can't handle")
            return status

        if self.max_twi_bytes < 128:
            status = Status.MIN_TWI_BYTES_ERROR
            dump.error("Max TWI bytes in single transaction is %d. Script can't handle",
                       self.max_twi_bytes)
            return status
        return status

    def run_soft_oir(self):
        """
        After the firmware downloaded on the module, performing a Soft OIR
        to reflect new FW version.
        """

        display.info("Firmware activation is performed by CMIS CDB commands")
        return Status.SUCCESS

    def disable_optics_serdes_retry(self):
        return Status.SUCCESS
    def enable_optics_serdes_retry(self):
        return Status.SUCCESS

def print_fw_info(x_handle):
    """
    Prints Information about the firmware
    """
    status = Status.SUCCESS
    status = x_handle.get_cdb_supported_info()
    if (status) !=  Status.SUCCESS:
        return status

    status = x_handle.get_download_features()
    if (status) !=  Status.SUCCESS:
        return status
    sleep(2)
    x_handle.intf_map["fw_info"], status  = x_handle.get_firmware_info()
    if (status) !=  Status.SUCCESS:
        return status
    return status

def firmware_download(x_handle):
    """
    Perform steps for FW download
    """
    status = Status.SUCCESS
    status = print_fw_info(x_handle)
    if (status) !=  Status.SUCCESS:
        return status

    status = x_handle.check_script_support(x_handle)
    if (status) !=  Status.SUCCESS:
        return status

    if (x_handle.intf_map["fw1_location"]):
        status = x_handle.start_fw_download(x_handle.intf_map["fw1_location"])
        if (status) !=  Status.SUCCESS:
            return status

        if x_handle.only_lpl_supported:
            status = x_handle.flash_new_fw_lpl()
            if (status) !=  Status.SUCCESS:
                return status
        else:
            status = x_handle.flash_new_fw_epl()
            if (status) !=  Status.SUCCESS:
                return status

    if (x_handle.intf_map["fw2_location"]):
        status = x_handle.start_fw_download(x_handle.intf_map["fw2_location"])
        if (status) !=  Status.SUCCESS:
            return status

        if x_handle.only_lpl_supported:
            status =  x_handle.flash_new_fw_lpl()
            if (status) !=  Status.SUCCESS:
                return status
        else:
            status = x_handle.flash_new_fw_epl()
            if (status) !=  Status.SUCCESS:
                return status

    status = x_handle.run_and_commit_new_image()
    if (status) !=  Status.SUCCESS:
        return status

    return status

def no_autopaging_firmware_download(x_handle):
    """
    Perform steps for no autopaging FW download"
    """

    status = Status.SUCCESS
    status = print_fw_info(x_handle)
    if (status) !=  Status.SUCCESS:
        return status

    if (x_handle.intf_map["fw1_location"]):
        status = x_handle.start_no_autopaging_fw_download(x_handle.intf_map["fw1_location"])
        if (status) !=  Status.SUCCESS:
            return status

    return status

def fill_cfg_block(block, optic_map):
    """
    Get info from the block and fill the optic map.
    """
    optic_map["name"] = "None"
    optic_map["fw1_location"] = None
    optic_map["fw2_location"] = None
    optic_map["password"] = "None"
    optic_map["vendor_sn"] = "None"
    optic_map["vendor_pno"] = "None"
    optic_map["old_fw_ver"] = "None"
    optic_map["skip_auto_paging_check"] = False

    for line in block:
        if "et-" in line:
            optic_map["name"] = (line.replace(' ', '')).rstrip()
            fpc = line.split("-")[1]
            optic_map["fpc"] = fpc.split('/')[0]
        if "file1" in line:
            optic_map["fw1_location"] = line.split(":")[1].strip()
        if "file2" in line:
            optic_map["fw2_location"] = line.split(":")[1].strip()
        if "vendor_pass" in line:
            optic_map["password"] = int(line.split(":")[1].strip(), 16)
        if "vendor_serial_no" in line:
            optic_map["vendor_sn"] = line.split(":")[1].strip()
        if "vendor_part_no" in line:
            optic_map["vendor_pno"] = line.split(":")[1].strip()
        if "version" in line:
            optic_map["old_fw_ver"] = line.split(":")[1].strip()
        if "skip_auto_paging_check" in line:
            optic_map["skip_auto_paging_check"] = eval(line.split(":")[1].strip())

    return optic_map

def get_config_from_file(config_file, optics_list):
    """
    Get details from the config file and create the optics list
    """

    with open(config_file) as fp:
        lines = fp.readlines()
        for line in lines:
            if "optics" and "{" in line:
                line_num = lines.index(line)
                tmp_lines = lines[line_num+1:]
                num_lines = 0
                for i in tmp_lines:
                    num_lines = num_lines + 1
                    if "}" in i:
                        end_str = copy.copy(i)
                        end = lines.index(i)
                        block = lines[line_num:line_num+num_lines]
                        if ("optics" in line) :
                            optic_map = OrderedDict()
                            optic_map = fill_cfg_block(block, optic_map)
                            optics_list.append(optic_map)
                        lines = lines[end+1:]
                        break
            else:
                continue

class FwInit:
    def __init__(self):
        self.optics_list =[]
        self.optic_map = OrderedDict()
        self.optic_fwinfo_map = {}
        self.report_strings_list = []

def get_fw_status_info(fw_status_info, fw_details, status):
    """
    Get the Fw Info, Status and store in fw_status_info
    """

    fw_status_info['info'] = fw_details
    fw_status_info['status'] = status
    return fw_status_info


def fw_upgrade_process(optic_map, optic_fwinfo_map):

    display_header("Parent process id: {} " .format(os.getppid()))
    display_header("Process id: {} interface: {} ".format(os.getpid(), optic_map["name"]))


    """
    Perform steps for FW upgrade Process"
    """

    status = Status.SUCCESS
    fw_status_info = {}
    fw_details = []
    fw_details.append('Not Applicable')

    try:
        port = get_sonic_port_index(optic_map["name"])
    except ValueError as error:
        dump.error("%s", error)
        sys.exit(1)
    xcvr = Xcvr(port, args.verbose, optic_map)

    status = xcvr.status
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    status = xcvr.verify_cmis_revison()
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    status = xcvr.stop_periodic()
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    sleep(1)
    status = xcvr.disable_optics_serdes_retry()
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)
    if xcvr.is_password_supported():
        """ CMIS 5.0 or plus does not implement password support for cdb cmd 0x41 and byte 136"""
        status = xcvr.write_password()
        if ((status)) !=  Status.SUCCESS:
            optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
            sys.exit(1)

    status = args.func(xcvr)
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    status = xcvr.run_soft_oir()
    if (status) !=  Status.SUCCESS:
        return status

    sleep(1)
    status = xcvr.start_periodic()
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    sleep(1)
    status = xcvr.enable_optics_serdes_retry()
    if ((status)) !=  Status.SUCCESS:
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, fw_details, status)
        sys.exit(1)

    if (xcvr.intf_map["fw_info"]):
        optic_fwinfo_map[optic_map["name"]] = get_fw_status_info(fw_status_info, xcvr.intf_map["fw_info"], status)


def generate_report(report_strings_list, optics_list, optic_fwinfo_map, tot_time):
    error_flag = False

    report_strings_list.append("============================================="+
                               "============================================="+
                               "============================================="+
                               "========================\n")
    report_strings_list.append("============================================="+
                               "========================== FIRMWARE INFO REPORT "+
                               "============================================="+
                               "=====================\n")
    report_strings_list.append("============================================="+
                               "============================================="+
                               "============================================="+
                               "========================")
    report_strings_list.append("| %-20s|  %20s  |%20s  |%20s  |%40s  |%20s |"
                               % ("Interface Name","Vendor S/N","Vendor P/N",
                               "Old FW Version","Status", "Time (min)"))
    report_strings_list.append("---------------------------------------------"+
                               "---------------------------------------------"+
                               "---------------------------------------------"+
                               "------------------------")
    # Iterate through all the optics in the optics_list
    for i in range(len(optics_list)):
        fpc = (optics_list[i]['fpc'])
        # Get Status and FW info details of the optic
        if (optic_fwinfo_map[(optics_list[i]['name'])]):
            report_strings_list.append("| %-20s|  %20s  |%20s  |%20s  |%40s  |%20s |"
            % (optics_list[i]['name'], optics_list[i]['vendor_sn'],
               optics_list[i]['vendor_pno'], optics_list[i]['old_fw_ver'], optic_fwinfo_map[optics_list[i]['name']]['status'].name, tot_time))
            report_strings_list.append("---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "------------------------")
            report_strings_list.append("| %-20s| %s|" % ("FW Info", " "*134))
            report_strings_list.append("---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "------------------------")
            if (optic_fwinfo_map[optics_list[i]['name']]['status'] !=  Status.SUCCESS):
                error_flag = True
            # Iterate over the FW Info details of the optic
            for j in range(len(optic_fwinfo_map[optics_list[i]['name']]['info'])):
                report_strings_list.append(optic_fwinfo_map[optics_list[i]['name']]['info'][j])
            report_strings_list.append("---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "---------------------------------------------"+
                                       "------------------------")

    report = '/var/tmp/qsfp_dd_fwupdate/FPC{}-upgrade-report.txt'.format(fpc)
    with open(report, mode='wt', encoding='utf-8') as report_file:
        for lines in report_strings_list:
           report_file.write('%s\n' % lines)
    report_file.close()
    if error_flag: 
       open("/var/run/upgd_failure", 'a').close()

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description="QSFP-DD FW download")
    arg_parser.add_argument("--verbose", help="Print debug details", action="store_true")

    subparsers = arg_parser.add_subparsers(dest='command',help="Choose one of the sub commands for the script")
    info_parser = subparsers.add_parser('fw_info', help="Displays FW info of the module")
    info_parser.add_argument('ifd_name', help="IFD name. For example et-0/1/2")
    hex_2_int = lambda x: int(x, 16)
    info_parser.add_argument('-p', '--password', type=hex_2_int, default='0x1011',
                              help='Vendor password for CMIS upgrade. Default is CMIS password 0x1110')
    info_parser.set_defaults(func=print_fw_info)

    fw_dw_parser = subparsers.add_parser('fw_dw', help="Download FW from bin file")
    fw_dw_parser.add_argument('ifd_name', help="IFD name. For example et-0/1/2")
    fw_dw_parser.add_argument('-f', '--file', type=str, required=True, help="FW binary file")
    fw_dw_parser.add_argument('-p', '--password', type=hex_2_int, default='0x1011',
                             help='Vendor password for CMIS upgrade. Default is CMIS password 0x1110')
    fw_dw_parser.set_defaults(func=firmware_download)

    no_autopaging_fw_dw_parser = subparsers.add_parser('fw_dw_no_autopaging', help="Download no autopaging FW from bin file")
    no_autopaging_fw_dw_parser.add_argument('ifd_name', help="IFD name. For example et-0/1/2")
    no_autopaging_fw_dw_parser.add_argument('-f', '--file', type=str, required=True, help="FW binary file")
    no_autopaging_fw_dw_parser.add_argument('-p', '--password', type=hex_2_int, default='0x1011',
                              help='Vendor password for CMIS upgrade. Default is CMIS password 0x1110')
    no_autopaging_fw_dw_parser.set_defaults(func=no_autopaging_firmware_download)

    innolight_fw_dw_parser = subparsers.add_parser('innolight_800g_fw_dw', help="Download FW "
                                                    "from bin file for Innolight 800g optics - "
                                                    "2X400G-LR4-P, 2X400G-FR4-P, 2X400G-DR4,"
                                                    "2X400G-LR4-10, 8x100G-FR1")
    innolight_fw_dw_parser.add_argument('ifd_name', help="IFD name. For example et-0/1/2")
    innolight_fw_dw_parser.add_argument('-f', '--file', type=str, required=True, help="FW binary file")
    innolight_fw_dw_parser.add_argument('-p', '--password', type=hex_2_int, default='0x1011',
                             help='Vendor password for CMIS upgrade. Default is CMIS password 0x1110')
    innolight_fw_dw_parser.set_defaults(func=firmware_download)

    info_all_parser = subparsers.add_parser('fw_info_all', help="Displays FW info of all the modules in the config file")
    info_all_parser.add_argument('-cfg','--cfg_file', dest="cfg_data", action="store", metavar="FILE",
                              required=True, help="Config File")
    info_all_parser.set_defaults(func=print_fw_info)

    fw_dw_all_parser = subparsers.add_parser('fw_dw_all', help="Downloads FW for all the modules in the config file")
    fw_dw_all_parser.add_argument('-cfg','--cfg_file', dest="cfg_data", action="store", metavar="FILE",
                              required=True, help="Config File")
    fw_dw_all_parser.set_defaults(func=firmware_download)


    start_t = datetime.datetime.now()
    args = arg_parser.parse_args()
    fw_init = FwInit()
    #Default behavior: all other optics need to go through auto paging support check.
    fw_init.optic_map["skip_auto_paging_check"] = False
    if args.command == 'fw_dw':
       fw_init.optic_map["name"] = args.ifd_name
       fw_init.optic_map["fw1_location"] = args.file
       fw_init.optic_map["fw2_location"] = None
       fw_init.optic_map["password"] = args.password
       fw_init.optics_list.append(fw_init.optic_map)
    elif args.command == 'fw_info':
       fw_init.optic_map["name"] = args.ifd_name
       fw_init.optic_map["password"] = args.password
       fw_init.optics_list.append(fw_init.optic_map)
    elif args.command == 'fw_dw_no_autopaging':
       fw_init.optic_map["name"] = args.ifd_name
       fw_init.optic_map["fw1_location"] = args.file
       fw_init.optic_map["fw2_location"] = None
       fw_init.optic_map["password"] = args.password
       fw_init.optics_list.append(fw_init.optic_map)
    elif args.command == 'innolight_800g_fw_dw':
       fw_init.optic_map["name"] = args.ifd_name
       fw_init.optic_map["fw1_location"] = args.file
       fw_init.optic_map["fw2_location"] = None
       fw_init.optic_map["password"] = args.password
       fw_init.optic_map["skip_auto_paging_check"] = True
       fw_init.optics_list.append(fw_init.optic_map)

    elif (args.command == 'fw_info_all' or args.command == 'fw_dw_all'):
        if  os.path.isfile(args.cfg_data) == False:
            print(("\nERROR: Input config file \"%s\" not found.\n" % args.cfg_data))
            sys.exit(1)
        else:
            get_config_from_file(args.cfg_data, fw_init.optics_list)
    else:
       dump.error("Please select vaild command")
       sys.exit(1)

    manager = multiprocessing.Manager()
    fwinfo_map = manager.dict()
    process_list =[]

    for i in range(len(fw_init.optics_list)):
        p = multiprocessing.Process(target=fw_upgrade_process, args=(fw_init.optics_list[i],fwinfo_map))
        process_list.append(p)
        p.start()
    for process in process_list:
        process.join()
    fw_init.optic_fwinfo_map = fwinfo_map

    if (args.command == 'fw_info_all' or args.command == 'fw_dw_all'):
        tot_time = datetime.datetime.now()-start_t
        print("Generating Report:")
        display_header("Generating Report:")
        sleep(1)
        generate_report(fw_init.report_strings_list,fw_init.optics_list, fw_init.optic_fwinfo_map, tot_time)
        display_header("Done!")
        open("/var/run/upgd_complete", 'a').close()
