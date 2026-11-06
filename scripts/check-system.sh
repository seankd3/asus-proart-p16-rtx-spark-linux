#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
check_machine
release=$(python3 "$REPO/scripts/p16_release.py" --shell)
eval "$release"
python3 "$REPO/scripts/peripherals.py" check-installed
[[ $(uname -r) == "$RELEASE" ]]
printf '\nKernel\n'; uname -r
printf '\nASUS-Module\n'; lsmod | rg 'asus|wmi|spi_hid|hid_sensor'
printf '\nAkku\n'; cat /sys/class/power_supply/BAT0/{capacity,status,voltage_now,charge_control_end_threshold}
printf '\nProfile\n'; powerprofilesctl list
printf '\nASUS-Profil\n'; cat /sys/firmware/acpi/platform_profile
printf '\nDienste\n'; systemctl --no-pager status p16-asus-controls.service p16-charge-limit.service power-profiles-daemon.service
printf '\nNVIDIA\n'; nvidia-smi
printf '\nTemperaturen\n'; p16-temperatures
