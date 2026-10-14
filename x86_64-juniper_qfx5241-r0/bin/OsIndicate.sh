#!/bin/bash
#
# NAME:
#	OsIndicate.sh - Set or Decode contents of OsIndications* EFI vars
#
# SYNOPSIS:
#
#	OsIndicate.sh -ds
#	OsIndicate.sh -dr
#	OsIndicate.sh -s string
#
#
# DESCRIPTION:
#	OsIndicate.sh allows you to decode contents of
#		- OsIndications
#		- OsIndicationsSupported
#		EFI variables.
#
#	Options:
#
#	-ds
#		Decode OsIndicationsSupported EFI variable
#
#	-dr
#		Decode OsIndications EFI variable
#
#	-s string
#		Set the bit correspinding to string in OsIndications EFI
#		variable. If the string is preceeded by a hypher (-), then the
#		bit is unset instead.
#			examples of supported strings:
#			- BOOT_TO_FW_UI
#			- TIMESTAMP_REVOCATION
#			- FILE_CAPSULE_DELIVERY
#			- FMP_CAPSULE
#			- CAPSULE_RESULT
#			- "" means clear all bits
#
# AUTHOR:
#	amesh@juniper.net
#
# Copyright (c) 2015, Juniper Networks, Inc.
#

# RCSid:
#	$Id: $

Error () {
	echo "Error: $@" >&2
	exit 1
}

# Where EFI varfs is mounted to
EFIVARFS=${EFIVARFS:-/sys/firmware/efi/efivars}

# OsIndicationsSupported variable
OIS=${OIS:-OsIndicationsSupported-8be4df61-93ca-11d2-aa0d-00e098032b8c}

# OsIndications variable
OI=${OI:-OsIndications-8be4df61-93ca-11d2-aa0d-00e098032b8c}

# bitset values, in decimal
BOOT_TO_FW_UI=1
TIMESTAMP_REVOCATION=2
FILE_CAPSULE_DELIVERY=4
FMP_CAPSULE=8
CAPSULE_RESULT=16

find_or_mount_efivarfs() {
	# check whether efivarfs is supported
	grep -w -q efivarfs /proc/filesystems || Error "efivarfs is not supported"

	# check if efivarfs is already mounted
	grep -qs 'efivarfs' /proc/mounts && return

	mount -l | awk '{print $1}' | grep -w -q efivarfs
	if [ $? -eq 0 ]; then
		# already mounted
		EFIVARFS=`mount -l | awk '$1 == "efivarfs" {print $3; exit}'`
		test -d "${EFIVARFS}" || Error "failed to find mounted efivarfs"
	else
		# mount efivarfs, if not mounted
		[ -d $EFIVARFS ] || mkdir -p $EFIVARFS || Error "$EFIVARFS directory does not exist"
		mount -t efivarfs efivarfs $EFIVARFS || Error "failed mount efivarfs on $EFIVARFS"
	fi
}

cleanup() {
	test -f "${varfile}" && rm -f "${varfile}"
}

do_help=:
do_dump_supported=:
do_dump_requested=:
do_set=:
do_set_arg=

help() {
    sed -n -e '/RCSid/,$d' -e '/^#!/d' -e '/^#\.[a-z]/d' -e '/^#/s,^# *,,p' $0
    exit ${1:-0}
}

# $1 - path to the variable file
dump() {
	local val tmp

	test -f "$1" || Error ${1} doesnt exist

	val=`dd if=${1} bs=1 skip=4 2>/dev/null | od -t dL | awk '{$1=""; print; exit}'`

	test -z "$val" && Error unable to read out ${1}

	case "${1}" in
	*Supported*) name=OsIndicationsSupported ;;
	*)           name=OsIndications ;;
	esac

	echo -n "${name}: "

	for x in BOOT_TO_FW_UI TIMESTAMP_REVOCATION FILE_CAPSULE_DELIVERY FMP_CAPSULE CAPSULE_RESULT; do
		tmp=$val
		let "tmp &= $x"
		if [ $tmp -gt 0 ]; then
			echo -n " $x"
		fi

	done

	echo "."
}

# $1 - string representation of bit to set
setvar() {
	local arg val_ val tmp positive

	case "$1" in
	-*) positive=-1; arg=${1#-*} ;;
	*) positive=1; arg="$1" ;;
	esac

	# if the OI var exists, use that; otherwise start with empty one
	if [ -f ${EFIVARFS}/${OI} ]; then
		dd if=${EFIVARFS}/${OI} of=${varfile} 2>/dev/null || Error \
			unable to read out OsIndicationsSupported
	else
		dd if=/dev/zero of=${varfile} bs=1 count=12 2>/dev/null || Error \
			unable to write to ${varfile}
	fi

	# empty arg means clear everything
	if [ -z "$arg" -o "$arg" = "clear" ]; then
		val=0
	else
		# convert string reprensetation to binary bit
		eval val_=\"\$$arg\"
		test -z "$val_" && Error $arg is invalid argument


		# read out current bit mask from OI
		val=`dd bs=1 skip=4 if=${varfile} 2>/dev/null | \
			od -t dL | awk '{$1=""; print; exit}' | sed 's, *,,g'`

		test -z "$val" && Error unable to read out ${EFIVARFS}/${OIS}

		case ${positive} in
		-1)
			# unset the requested bit
			let "val &= ~ $val_"
			;;
		1)
			# set the requested bit
			let "val |= $val_"
			;;
		esac
	fi


	# set attributes
	echo -ne "$(printf '\\x%x' 7)" | dd of=${varfile} bs=1 \
		seek=0 conv=notrunc 2>/dev/null || Error writing to ${varfile}

	# set new value
	echo -ne "$(printf '\\x%x' $val)" | dd of=${varfile} bs=1 \
		seek=4 conv=notrunc 2>/dev/null || Error writing to ${varfile}

	# On wrl9 and later versions, the immutable flag is set on EFI variables,
	# to make changes to the EFI variables first remove the immutable flag, make
	# changes to the variable and then set back the immutable flag
	[ -f ${EFIVARFS}/${OI} ] && chattr -i ${EFIVARFS}/${OI}

	# write it out
	cat ${varfile} > ${EFIVARFS}/${OI} || Error unable to write to ${EFIVARFS}/${OI}

	chattr +i ${EFIVARFS}/${OI}
}

varfile=$(mktemp /tmp/varfile.XXXXXX)
test -f ${varfile} || Error "cannot create temporary file"

trap "cleanup" 0 1 2 3 6

while [ $# -gt 0 ]
do
	case "$1" in
	-h) do_help=; shift;;
	-ds) do_dump_supported=; shift;;
	-dr) do_dump_requested=; shift;;
	-s) test $# -lt 2 && help -1;
		do_set=;
		do_set_arg="$2"; shift 2;;
	*=*) eval "$1"; shift;;
	*) help -1; break ;;
	esac
done

$do_help help
find_or_mount_efivarfs
$do_dump_supported dump ${EFIVARFS}/${OIS}
$do_dump_requested dump ${EFIVARFS}/${OI}
$do_set setvar $do_set_arg
