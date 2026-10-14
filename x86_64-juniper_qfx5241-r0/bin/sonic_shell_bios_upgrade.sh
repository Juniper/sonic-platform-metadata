#!/bin/bash
#       sonic_shell_bios_upgrade.sh - Secure Boot BIOS utility
#
# SYNOPSIS:
#       sonic_shell_bios_upgrade.sh
#
# DESCRIPTION:
#    This script is inteded for Checking the Secure Boot BIOS
#    and provide support for upgrade during install and upgrade on Brackla only.
# Arguments:
#   - boot : use this argument in the init script at the time of reboot.
#   - fwupgrade : use this argument when BIOS/firmware upgrade is requested.
#   - osupgrade : use this argument when OS/Junos/Evo image upgrade is requested.
#   - osinstall : use this argument when OS has to be installed. Used in the
#           bootup init script.
#
# AUTHOR: Praneeth Patil <praneethp@juniper.net>
#
# Copyright (c) 2016-2025, Juniper Networks, Inc.
# All rights reserved.
#


# defines for Utilities used
DMIDECODE=/usr/sbin/dmidecode
STRINGS=/usr/bin/strings
OSINDICATE=/usr/bin/OsIndicate.sh
EVO_EFI_LOCATION='/boot/EFI'
UPDATE_CAPSULE_LOCATION='/boot/EFI/UpdateCapsule/'
GOLDEN_EFI_LOCATION='/boot/EFI/Juniper/'
CAPSULE=""
LSTKNWNGD=""
LSTKNWNGD_CANDIDATE=""
CAPSULE_VER="00_00.00_00"
LSTKNWNGD_VER="00_00.00_00"
LSTKNWNGD_CANDIDATE_VER="00_00.00_00"

#error codes
EOK=0
EFAIL=1000

# bios version enums
BIOS_VERSION_NEW=1
BIOS_VERSION_EQL=2
BIOS_VERSION_OLD=3

# globals
FLASH_BIOS_VER="00_00.00_00"
BIOS_VARIANT_INDEX=0

ESP_MOUNT='/mnt/esp_mnt'
BOOT_PART='/boot/'
DISK=""
ESP_PART=""

################################## BIOS INSTALL/UPGRADE #######################
#
# BIOS utilities
#
bios_flash_get_version_info() {
        #current/flash BIOS version.
        flash_bios_version_str=$($DMIDECODE -s bios-version | grep -v "^#")
        
        # Get the number string after REH_SFB(/P)_<number_string>
        flash_bios_version=$(echo $flash_bios_version_str)
        # return version string
	flash_bios_version="$(cut -d'_' -f4 <<<"$flash_bios_version")"
        flash_bios_version=$(echo "$flash_bios_version" | cut -c 4-)
        echo $flash_bios_version
}

bios_set_upgrade_flag() {
    $OSINDICATE -s FILE_CAPSULE_DELIVERY
    if [ $? -eq 0 ]; then
        return "0";
    else
        echo "BIOS capsule delivry failed";
        return "1";
    fi
}

# Compare the version, first arg against the second arg
bios_version_compare() {
    ret=$BIOS_VERSION_OLD
    version_1=$1
    version_2=$2

    if [[ "$version_1" > "$version_2" ]]; then
        ret=$BIOS_VERSION_NEW;
    elif [[ "$version_1" = "$version_2" ]]; then
        ret=$BIOS_VERSION_EQL;
    fi

    echo $ret
}

# Removed dropbox.efi and last-known-good.efi from $EVO_EFI_LOCATION
efi_upgrade_cleanup() {
    echo "Cleaning up!!"
    rm -f $UPDATE_CAPSULE_LOCATION/$CAPSULE
    rm -f $GOLDEN_EFI_LOCATION/$LSTKNWNGD_CANDIDATE
}

#sonic-os or onie partition is not an ESP partition.
#Bios looks in the ESP boot partition for capsule file to upgrade to new version.
#So copying the capsule to the ESP partition instead of SONIC-OS
mount_efi_partiton() {
    BOOT_PART=$(lsblk | grep '/boot' | awk '{print $1}' | sed 's/[^a-z0-9]*//g')
    DISK=$(lsblk -no PKNAME /dev/$BOOT_PART)
    ESP_PART=$(lsblk -pno NAME,LABEL "/dev/$DISK" | grep -i EFI | awk '{print $1}' | sed 's/^[^/]*//')

    if [ ! -d $ESP_MOUNT ]; then
        mkdir -p $ESP_MOUNT
    fi

    mount -t vfat $ESP_PART $ESP_MOUNT
    if [ $? -eq 0 ]; then
        echo "ESP mount Succeeded"
    else
        echo "ESP Mount failed"
        exit 1
    fi

    UPDATE_CAPSULE_LOCATION=$ESP_MOUNT/EFI/UpdateCapsule/
}

#Checks if required files exist, if found the *.efi files will be copied to
# upgrade locations.
efi_upgrade_init() {
    #CAPSULE=CICL_U_BUG0_v33.01.efi
    SRC_LOCATION=$1
    CAPSULE=$2
    #FIXME - fix the CAPSULE_VER extract methis
    CAPSULE_VER=$(echo "$CAPSULE" | grep -oP 'v\K[0-9]+(\.[0-9]+)*')

    if [ ! -d $GOLDEN_EFI_LOCATION ]; then
        mkdir -p $GOLDEN_EFI_LOCATION
    fi

    mount_efi_partiton

    if [ ! -d $UPDATE_CAPSULE_LOCATION ]; then
        mkdir -p $UPDATE_CAPSULE_LOCATION
    fi

    # Copy capsule (dropbox.efi) to UpdateCapsule
    # (/boot/EFI/UpdateCapsule)
    echo "CMD: cp -f $SRC_LOCATION/$CAPSULE $UPDATE_CAPSULE_LOCATION$CAPSULE"
    cp -f $SRC_LOCATION/$CAPSULE $UPDATE_CAPSULE_LOCATION$CAPSULE

    echo "Capsule is copied to ESP Partition: $ESP_PART/EFI/UpdateCapsule/$CAPSULE"
    umount $ESP_MOUNT
}

#
# BIOS Install/Upgrade functions
#
#############################################################
#############################################################
###   OS reboot: This is called when BIOs upgrade is done ###
###   Step 1: If partition exists                         ###
###        (a) Check for EFI/Juniper folder               ###
###        (b) Check if last-known-good.efi exists        ###
###        (c) If not, treat it as an error               ###
###   Step 2: Compare bios version against flash          ###
###   Step 3: If verion is different than flash           ###
###        (a) Check if efi.candidate file exits          ###
###        (b) if yes, replace .efi with efi.candidate    ###
###        (c) If no candidate file, treat it as an error ###
###   NOTE: It assumes that capsule and candidate are of  ###
###         same version and is confirmed before placing  ###
###         them in UpdateCapsule.                        ###
#############################################################
#############################################################
bios_boot_check() {

    bios_efi_get_name_version_info
    ret=$?
    if [ "$ret" -ne "$EOK" ]; then
        echo "ERROR: Failed to fetch EFI File details!"
        exit 1
    fi

    if [ ! -d $GOLDEN_EFI_LOCATION ]; then
        mkdir -p $GOLDEN_EFI_LOCATION
    fi

    if [ ! -d $UPDATE_CAPSULE_LOCATION ]; then
        mkdir -p $UPDATE_CAPSULE_LOCATION
    fi

    # We should always have last known good bios file present at
    # /boot/EFI/Juniper location. Hence copy it from /usr/share/firmware
    # location to /boot/EFI/Juniper location if it is already not copied.
    # As of now, last known good file is getting copied only when bios gets
    # upgraded. If the existing bios gets corrupted before bios upgrade, then
    # without last known good file it cannot be recovered by itself.
    if [ ! -f $GOLDEN_EFI_LOCATION$LSTKNWNGD ]; then
        if [ ! -f $SRC_LOCATION$LSTKNWNGD ]; then
            echo "No Last Known Good BIOS file $SRC_LOCATION$LSTKNWNGD present. BIOS cannot recover by itself."
        else
            cp -f $SRC_LOCATION$LSTKNWNGD $GOLDEN_EFI_LOCATION
        fi
    fi

    # Candidate File doesnt exist. This should never be the case.
    if [ ! -f $GOLDEN_EFI_LOCATION/$LSTKNWNGD_CANDIDATE ]; then
        echo "No Last Known Good BIOS file"
        return 1
    fi 

    # Candidate File Exists. Compare Version with Flash Version.

    result=$(bios_version_compare $LSTKNWNGD_CANDIDATE_VER $FLASH_BIOS_VER)
    if [ "$result" -eq "$BIOS_VERSION_EQL" ]; then
        # Flash and new EFI is in Sync 
        echo "Flash and EFI versions are same. Converting Candidate to LastKnownGood EFI"
        cp -f $GOLDEN_EFI_LOCATION/$LSTKNWNGD_CANDIDATE \
                $GOLDEN_EFI_LOCATION/$LSTKNWNGD

    elif [ "$result" -eq "$BIOS_VERSION_OLD" ]; then
        # Candidate File Version is Older than Flash Version!
        echo "ERROR: Candidate File Version is Older than Flash Version!"
    else # Candidate File Version is Newer than Flash Version!
        echo "ERROR: Candidate File Version is Newer than Flash Version!"
    fi

    efi_upgrade_cleanup

}

#############################################################
#############################################################
###   BIOS Upgrade:                                       ###
###                                                       ###
###   NOTE: This should be called at the END of install   ###
###                                                       ###
###   Step 2: If partition exists                         ###
###        (a) Check for EFI/Juniper folder               ###
###        (b) Check if last-known-good.efi exists        ###
###   Step 3: Compare capsule version against flash       ###
###   Step 4: If versions are different,                  ###
###        (a) Check if last known is newer than flash    ###
###        (b) if not, ignore (downgrade not allowed)     ###
###        (c) if yes, check for dropbox capsule          ###
###        (d) if dropbox.efi is not present, error       ###
###        (e) if present,                                ###
###        (f) create EFI/UpdateCapsule and copy capsule  ###
###        (g) copy last known .efi as .efi.candidate     ###
###   Step 5: Set UEFI variable to indicate bios upgrade  ###
###   Step 6: Repeat Step 4 (e, f g) for other partitions ###
###                                                       ###
###   NOTE: A Reboot is required for the update to finish ###
###                                                       ###
###   Firmware Upgrade:                                   ###
###       Only the location of the dropbox.efi changes    ###
###                                                       ###
###   NOTE: A Reboot is required for the update to finish ###
###                                                       ###
#############################################################
#############################################################
bios_upgrade_check() {
 
    IMAGE_DIR=$1
    IMAGE=$2
    efi_upgrade_init $IMAGE_DIR $IMAGE
    ret=$?
    if [ "$ret" -ne "$EOK" ]; then
        echo "ERROR: Upgrade Init failed!"
        efi_upgrade_cleanup
        exit 1
    fi

    c_result=$(bios_version_compare $CAPSULE_VER $FLASH_BIOS_VER)

    if [ "$c_result" -eq "$BIOS_VERSION_EQL" ]; then
        echo "Error: upgrade capsule & flash have same version. Nothing to upgrade"
        efi_upgrade_cleanup
        exit 1;
    elif [ "$c_result" -eq "$BIOS_VERSION_OLD" ]; then
        echo "NOTICE: upgrade capsule is older than flash version"
        efi_upgrade_cleanup
        exit 1;
    fi

    echo "=============================================="
    echo "=        Upgrading BIOS to $capsule_bios_ver       ="
    echo "=============================================="

    bios_set_upgrade_flag

    return 0;
}

usage() {
    echo $"Usage: $0 {boot/osinstall/osupgrade/fwupgrade}"
    echo $"Usage: $0 fwupgrade <SRC_LOCATION> <UPD_CAPSULE_NAME>
    <UPD_CAPSULE_VER> <LKG_CAPSULE_NAME> <LKG_CAPSULE_VER>"
}
# if secure boot is not enabled,
# no secure bios upgrade/check are needed

# /usr/bin/sb-status.sh || exit 0

# Get the BIOS version in the flash
FLASH_BIOS_VER=$(bios_flash_get_version_info)
echo "Flash Version : $FLASH_BIOS_VER"

if [[ $1 == "fwupgrade" ]]; then
    if [ $# -gt 3 ] && [ $# -ne 6 ]; then
        echo "Error: Invalid set of arguments"
        usage
        exit 1
    elif [ $# -eq 6 ]; then
        SKIP_JFIRMWARE_DATA=1
        #update/lkg capsule name validation check
        if [[ $3 != *_U_* ]] || [[ $5 != *_L_* ]]; then
            echo "Error: Invalid UPD/LKG capsule files"
            usage
            exit 1
        fi
        regex='^[0-9]+([.][0-9]+)?$'
        #update/lkg capsule version validation check
        if ! [[ $4 =~ $regex ]] || ! [[ $6 =~ $regex ]]; then
            echo "Error: Invalid UPD/LKG capsule version"
            usage
            exit 1
        fi
        CAPSULE=$3
        CAPSULE_VER=$4
        LSTKNWNGD=$5
        LSTKNWNGD_CANDIDATE="${LSTKNWNGD}.candidate"
        LSTKNWNGD_VER=$6
        LSTKNWNGD_CANDIDATE_VER=$LSTKNWNGD_VER
    fi
fi

case "$1" in
osinstall)
        # Host OS Install
        echo "Host OS Install - BIOS Upgrade Check"
        ;;
osupgrade)
        # Host OS Upgrade
        echo "Host OS Upgrade - BIOS Upgrade Check"
        ;;
fwupgrade)
        # Host OS Firmware Upgrade
        echo "Host OS Firmware Upgrade - BIOS Upgrade Check"
        bios_upgrade_check $2 $3
        ;;
boot)
        echo "BIOS Boot time Check"
        bios_boot_check 
        exit 0
        ;;
*)
        usage
        exit 1
esac
## end of file ##
