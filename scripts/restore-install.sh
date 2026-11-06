#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_root
check_model
[[ $# == 1 ]] || { echo "Aufruf: sudo $0 /root/p16-install.<Kennung>" >&2; exit 1; }
mountpoint -q /boot
[[ $(findmnt -n -o FSTYPE /boot) == vfat ]]
exec 200>/run/lock/boot-partition.lock
flock -w 30 200
python3 "$REPO/scripts/install_state.py" restore "$1"
reload_restored_config
sync
printf 'EFI-Images, Bootmenü und Systemdateien wiederhergestellt. Kernelpakete bleiben installiert.\n'
