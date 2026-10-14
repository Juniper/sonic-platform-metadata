#!/bin/bash

#UPDTOOL="/usr/local/sbin/fpga_upd2"
UPDTOOL="/usr/bin/fpga_upd2"
TMP_FILE="/tmp/flash_write_data.bin"    # binary file for option -wf

# Global
CMD=
CMD_ERASE=
BAR=
OFFSET=

# Project related
#FPGA_ID="10ee7021"
ADDR_PCA9548_01="0x77"
ADDR_PCA9548_02="0x76"
CHAL_PCA9548_01="0x1"
CHAL_PCA9548_02="0x4"
ADDR_FPGA="0x60"

# Check FPGA version supported or not
SUPPORT_MAJOR="0x5B"    # R0B, 0x0B for golden, 0x5B for primary, LSB
SUPPORT_MINOR="0x13"

# Register
REG_MAJOR_VER="0x01"
REG_MINOR_VER="0x02"
REG_SPI_CONTROL="0x6F"
REG_CTRL_BIT="0x0"      # bit[0], default 0 to WANPLL, set 1 to flash

# Used for UPDTOOL
GOLDEN_START=0x0        # Golden start address
PRIMARY_START=0x400000  # Primary start address
PRI_IMAGE_LEN=0x37FBFF  # Count in 0x0 ~ 0x500000, actually image size is around 0x40C540
ALL_IMAGE_LEN=0x77FC3B  # Double the PRI_IMAGE_LEN

FLASH_SIZE=0x1000000
PAGE_SIZE=0x100         # 256 bytes

function usage()
{
    program=$(basename $0)
    echo "Usage: $program [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -rid                  : Read ID (0x9F)"
    echo "  -upd <file>           : Update flash with FPGA code, primary only"
    echo "  -rf <offset> <length> : Read flash with specified length"
    echo "  -ef <offset> <length> : Erase flash with specified length"
    echo "  -wf <offset> <data>   : Write flash in one byte hex data"
    echo ""
    echo "Examples:"
    echo "  $program -rid"
    echo "  $program -upd /tmp/fpga_code.rpd"
    echo "  $program -rf $PRIMARY_START 0x1"
    echo "  $program -ef $PRIMARY_START 0x100"
    echo "  $program -wf $PRIMARY_START 0x12"
}

function get_per_byte_write_offset_and_data()
{
    local offset=$1
    local data=$2

    # Re-format to hex
    int_offset=`printf "%d" $offset`
    hex_offset=0x`printf "%x" $int_offset`

    # Get LSB of offset to calculate
    lsb=$((hex_offset & 0xff))
    int_lsb=`printf "%d" $lsb`
    write_offset=$((int_offset - int_lsb))      # int
    OFFSET="0x`printf "%x" $write_offset`"      # hex, OFFSET is global

    # Re-format to hex and get data number part only
    #data=${data:2:2}
    data=`printf "%d" $data`                    # string to int
    data=`printf "%x" $data`                    # int to hex

    # Since page program, we need to get reset data to write back
    pcie_enable
    switch_spi_to_flash
    readback=`$UPDTOOL -R -A $OFFSET -L $PAGE_SIZE $BAR | tail -n +12`
    switch_spi_to_default
    pcie_disable

    # Output to a binary file and write later
    seq_data=`echo "$readback" | sed  's/.\{10\}//' | sed 's/.\{20\}$//' | sed 's/  / /g' | sed ':a;N;s/\n/ /g;ta'`
    i=0
    hex_output=""
    for rd in $seq_data; do
        if [ $i -eq $int_lsb ]; then
            data_per_byte=$data
        else
            data_per_byte=$rd
        fi

        if [ "$hex_output" == "" ]; then
            hex_output="\x$data_per_byte"
        else
            hex_output="$hex_output\x$data_per_byte"
        fi

        i=$((i+1))
    done
    rm -f $TMP_FILE
    printf "$hex_output" | dd of=$TMP_FILE bs=1
}

function switch_spi_to_default()
{
    if [ -n "`which cpldutil`" ]; then
        # DIAG
        val=0x`cpldutil -md FPGA_SYS $REG_SPI_CONTROL | sed -n '2p' | awk -F ' ' '{print $2}'`
        val=$((val & (0 << REG_CTRL_BIT)))
        cpldutil -mw FPGA_SYS $REG_SPI_CONTROL $val
    else
        # ONIE
        val=`i2cget -y -f 0 $ADDR_FPGA $REG_SPI_CONTROL`
        val=$((val & (0 << REG_CTRL_BIT)))
        i2cset -y -f 0 $ADDR_FPGA $REG_SPI_CONTROL $val
        if [ -n $ADDR_PCA9548_02 ]; then
            i2cset -y -f 0 $ADDR_PCA9548_02 0x0 0x0
        fi
        i2cset -y -f 0 $ADDR_PCA9548_01 0x0 0x0
    fi
}

function switch_spi_to_flash()
{
    if [ -n "`which cpldutil`" ]; then
        # DIAG
        val=0x`cpldutil -md FPGA_SYS $REG_SPI_CONTROL | sed -n '2p' | awk -F ' ' '{print $2}'`
        val=$((val | (1 << REG_CTRL_BIT)))
        cpldutil -mw FPGA_SYS $REG_SPI_CONTROL $val
    else
        # ONIE
        i2cset -y -f 0 $ADDR_PCA9548_01 0x0 $CHAL_PCA9548_01
        if [ -n $ADDR_PCA9548_02 ]; then
            i2cset -y -f 0 $ADDR_PCA9548_02 0x0 $CHAL_PCA9548_02
        fi
        val=`i2cget -y -f 0 $ADDR_FPGA $REG_SPI_CONTROL`
        val=$((val | (1 << REG_CTRL_BIT)))
        i2cset -y -f 0 $ADDR_FPGA $REG_SPI_CONTROL $val
    fi
}

function pcie_disable()
{
    local bus="`cat /proc/bus/pci/devices | grep $FPGA_ID | awk -F ' ' '{print $1}'`"
    echo "0x4=0" > /sys/class/pci_bus/0000:${bus:0:2}/device/0000:${bus:0:2}:${bus:2:2}.0/config
}

function pcie_enable()
{
    local bus="`cat /proc/bus/pci/devices | grep $FPGA_ID | awk -F ' ' '{print $1}'`"
    en=`hexdump /sys/class/pci_bus/0000:${bus:0:2}/device/0000:${bus:0:2}:${bus:2:2}.0/config | grep 0000000 | awk -F ' ' '{print $4}'`
    if [ ${en:2:2} -ne 6 ]; then
        echo "0x4=6" > /sys/class/pci_bus/0000:${bus:0:2}/device/0000:${bus:0:2}:${bus:2:2}.0/config
    fi
}

function check_parameter
{
    local file
    local offset
    local len
    local data

    if [ $# -lt 1 ]; then
        usage
        exit 1
    fi

    case $1 in
        -rid)
            CMD="$UPDTOOL -O $BAR"
            break
            ;;
        -upd)
            if [ -z $2 ]; then
                usage
                exit 1
            else
                file=$2
            fi

            if [ -n $3 ] && [ "$3" == "-all" ]; then
                start_addr=$GOLDEN_START
                write_len=$ALL_IMAGE_LEN
            else
                start_addr=$PRIMARY_START
                write_len=$PRI_IMAGE_LEN
            fi
            CMD_ERASE="$UPDTOOL -E -A $start_addr -L $write_len $BAR"

            #----- File is only included by PRIMARY address -----
            #start_addr=$GOLDEN_START 
            CMD="$UPDTOOL -W -F $file -A $start_addr -L $write_len $BAR"
            break
            ;;
        -rf)
            if [ $# -ne 3 ]; then
                usage
                exit 1
            else
                offset=$2
                len=$3
            fi
            # Check input
            if [ `printf "%d" $((offset + len))` -gt `printf "%d" $FLASH_SIZE` ]; then
                echo "Read over flash size, please check!"
                exit 1
            fi
            CMD="$UPDTOOL -R -A $offset -L $len $BAR"
            break
            ;;
        -ef)
            if [ $# -ne 3 ]; then
                usage
                exit 1
            else
                offset=$2
                len=$3
            fi
            # Check input
            if [ `printf "%d" $((offset + len))` -gt `printf "%d" $FLASH_SIZE` ]; then
                echo "Erase over flash size, please check!"
                exit 1
            fi
            CMD="$UPDTOOL -E -A $offset -L $len $BAR"
            break
            ;;
        -wf)
            # Support one byte writing so far
            if [ $# -ne 3 ]; then
                usage
                exit 1
            else
                offset=$2
                data=$3
            fi
            # Check input
            if [ `printf "%d" $offset` -gt `printf "%d" $FLASH_SIZE` ]; then
                echo "Offset over flash size, please check!"
                exit 1
            fi
            if [ `printf "%d" $data` -gt `printf "%d" 0xFF` ]; then
                echo "Data over one byte length, please check!"
                exit 1
            fi

            # 4-byte alignment
            get_per_byte_write_offset_and_data $offset $data

            # Erase first, and then write file back
            CMD_ERASE="$UPDTOOL -E -A $OFFSET -L $PAGE_SIZE $BAR"    # OFFSET is global
            CMD="$UPDTOOL -W -F $TMP_FILE -A $OFFSET -L $PAGE_SIZE $BAR"
            break
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

function get_bar()
{
    # BAR is global
    BAR="0x`cat /proc/bus/pci/devices | grep $FPGA_ID | awk -F ' ' '{print $4}'`"
}

function check_fpga_ver()
{
    if [ -n "`which cpldutil`" ]; then
        # DIAG
        major_ver=0x`cpldutil -md FPGA_SYS $REG_MAJOR_VER | sed -n '2p' | awk -F ' ' '{print $2}'`
        minor_ver=0x`cpldutil -md FPGA_SYS $REG_MINOR_VER | sed -n '2p' | awk -F ' ' '{print $2}'`
    else
        # ONIE
        i2cset -y -f 0 $ADDR_PCA9548_01 0x0 $CHAL_PCA9548_01
        if [ -n $ADDR_PCA9548_02 ]; then
            i2cset -y -f 0 $ADDR_PCA9548_02 0x0 $CHAL_PCA9548_02
        fi
        major_ver=`i2cget -y -f 0 $ADDR_FPGA $REG_MAJOR_VER`
        minor_ver=`i2cget -y -f 0 $ADDR_FPGA $REG_MINOR_VER`
        if [ -n $ADDR_PCA9548_02 ]; then
            i2cset -y -f 0 $ADDR_PCA9548_02 0x0 0x0
        fi
        i2cset -y -f 0 $ADDR_PCA9548_01 0x0 0x0
    fi

    major_ver=`printf "%d" $major_ver`
    major_ver_lsb=$((major_ver & 0x0f))     # Check for R0B
    minor_ver=`printf "%d" $minor_ver`
    support_major=`printf "%d" $SUPPORT_MAJOR`
    support_major_lsb=$((support_major & 0x0f))
    support_minor=`printf "%d" $SUPPORT_MINOR`

    # FPGA code with version 0x5b.0x13 starts to support SPI update
    if [ $major_ver_lsb -ne $support_major_lsb ] || [ $minor_ver -lt $support_minor ]; then
        echo "FPGA ver doesn't support SPI update yet, please update FPGA via cpldupd first!"
        exit 1
    fi
}

function main()
{
    # Check FPGA supported or not
    #check_fpga_ver

    # Setup SPI
    get_bar         # Need to get BAR before check_parameter
    check_parameter $@
    pcie_enable
    #switch_spi_to_flash

    # Run_cmd
    if [ -n "$CMD_ERASE" ]; then
        $CMD_ERASE
    fi
    $CMD

    # Reset
    #switch_spi_to_default
    #pcie_disable

    if [ -f $TMP_FILE ]; then
        rm -f $TMP_FILE
    fi

    exit 0
}

main $@
