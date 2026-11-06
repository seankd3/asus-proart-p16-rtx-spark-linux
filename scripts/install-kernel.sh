#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_root
check_machine
resume_backup=""
if [[ $# != 0 ]]; then
  [[ $# == 2 && $1 == --resume ]] || { echo "Aufruf: sudo $0 [--resume /root/p16-install.<Kennung>]" >&2; exit 1; }
  resume_backup=$2
fi
mountpoint -q /boot || { echo '/boot muss eingehängt sein.' >&2; exit 1; }
[[ $(findmnt -n -o FSTYPE /boot) == vfat ]] || { echo 'FAT-EFI-Partition unter /boot erforderlich.' >&2; exit 1; }
[[ -f /boot/limine.conf ]]
exec 200>/run/lock/boot-partition.lock
flock -w 30 200
if [[ -z $resume_backup ]]; then
  [[ ! -e /usr/lib/modules/$RELEASE && ! -e /boot/EFI/Linux/omarchy_$PACKAGE.efi ]] || {
    echo 'P16-Kernel ist bereits vorhanden. Für eine abgebrochene Installation --resume mit ihrer Sicherung verwenden.' >&2; exit 1;
  }
fi
packages=("$REPO/kernel/$PACKAGE-$PKGVER-$PKGREL-aarch64.pkg.tar.zst" "$REPO/kernel/$PACKAGE-headers-$PKGVER-$PKGREL-aarch64.pkg.tar.zst")
for pkg in "${packages[@]}"; do [[ -s $pkg ]] || { echo "Paket fehlt: $pkg" >&2; exit 1; }; done
python3 - "$REPO" <<'PY'
from pathlib import Path
import hashlib, sys
root = Path(sys.argv[1]) / 'build/acpi/kernel/firmware/acpi'
for name, pin in {'DSDT.aml':'80e0ad4a051704003d09029cb600a9814e1cd370ce774b9749dc09d2232b5aaf', 'SSDT.aml':'9b28ffc14c19f8b7ea2278de88ff9ec070f5f70401107d81fef067a916aa9695'}.items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest() != pin:
        raise ValueError('ACPI-Prüfsumme stimmt nicht: ' + name)
PY
# Validate the host command line before package installation or live writes.
python3 "$REPO/scripts/kernel_cmdline.py" /proc/cmdline "$REPO/build/p16.cmdline"
python3 "$REPO/scripts/peripherals.py" check-inputs

backup=""
hooks=""
finish() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n $hooks ]]; then rm -rf "$hooks"; fi
  if (( code != 0 )) && [[ -n $backup && -f $backup/state.json ]]; then
    if python3 "$REPO/scripts/install_state.py" fail "$backup"; then
      reload_restored_config || true
      sync || true
      echo 'Bootdateien und Konfiguration wurden auf den gesicherten Stand zurückgesetzt.' >&2
    else
      echo 'Automatische Wiederherstellung fehlgeschlagen; Sicherung und Fehlermeldung prüfen.' >&2
    fi
    printf 'Nach Beheben des Fehlers erneut ausführen:\n  sudo %q --resume %q\n' "$0" "$backup" >&2
  fi
  exit "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -n $resume_backup ]]; then
  # Require a root-owned checkpoint and byte-identical package inputs.
  python3 "$REPO/scripts/install_state.py" resume "$resume_backup" "${packages[@]}"
  backup=$resume_backup
else
  backup=$(mktemp -d /root/p16-install.XXXXXXXX)
  python3 "$REPO/scripts/install_state.py" begin "$backup" "${packages[@]}"
fi
printf 'Sicherung und Checkpoint: %s\n' "$backup"
python3 "$REPO/scripts/peripherals.py" apply --backup "$backup"
# Only this transaction masks package hooks; rebuild the single new UKI below.
hooks=$(mktemp -d /run/p16-hooks.XXXXXXXX)
for directory in /usr/share/libalpm/hooks /etc/pacman.d/hooks; do
  for hook in "$directory"/*.hook; do
    if [[ -e $hook ]]; then ln -sf /dev/null "$hooks/${hook##*/}"; fi
  done
done
pacman -U --noconfirm --hookdir "$hooks" "${packages[@]}"
dkms autoinstall -k "$RELEASE"
depmod -a "$RELEASE"
install -Dm644 "$REPO/config/p16-acpi" /etc/initcpio/install/p16-acpi
install -Dm644 "$REPO/config/zz-p16-hardware-fixes.conf" /etc/mkinitcpio.conf.d/zz-p16-hardware-fixes.conf
install -Dm644 "$REPO/config/zz-p16-keyboard.conf" /etc/mkinitcpio.conf.d/zz-p16-keyboard.conf
install -Dm644 "$REPO/config/limine-p16-keyboard.conf" /etc/limine-entry-tool.d/zz-p16-keyboard.conf
install -Dm644 "$REPO/config/zz-p16-input.conf" /etc/mkinitcpio.conf.d/zz-p16-input.conf
for conf in nvidia.conf omarchy-n1x-nvidiafb.conf; do
  install -Dm644 "$REPO/config/$conf" "/etc/modprobe.d/$conf"
done
for conf in 00-omarchy-n1x-usb4.conf 00-omarchy-n1x-gpu.conf 00-omarchy-n1x-sleep.conf; do
  install -Dm644 "$REPO/config/$conf" "/etc/limine-entry-tool.d/$conf"
done
for table in DSDT.aml SSDT.aml; do
  install -Dm644 "$REPO/build/acpi/kernel/firmware/acpi/$table" "/etc/initcpio/p16-acpi/$table"
done
install -Dm755 "$REPO/scripts/p16-charge-limit" /usr/local/libexec/p16-charge-limit
install -Dm755 "$REPO/scripts/p16-temperatures" /usr/local/bin/p16-temperatures
for unit in p16-asus-controls.service p16-charge-limit.service; do
  install -Dm644 "$REPO/config/$unit" "/etc/systemd/system/$unit"
done
install -Dm644 "$REPO/config/20-p16-asus.conf" /etc/systemd/system/power-profiles-daemon.service.d/20-p16-asus.conf
install -Dm644 "$REPO/config/50-p16-critical.conf" /etc/UPower/UPower.conf.d/50-p16-critical.conf
mkinitcpio --kernel "$RELEASE" --cmdline "$REPO/build/p16.cmdline" --uki "$REPO/build/$PACKAGE.efi"
python3 "$REPO/scripts/verify-boot.py" --image "$REPO/build/$PACKAGE.efi" --image-only
limine-entry-tool --no-hooks --no-mutex --add-uki "$PACKAGE" "$REPO/build/$PACKAGE.efi" --comment 'ASUS ProArt P16: ACPI, SPI HID, ARM64 WMI'
python3 - <<'PY'
from pathlib import Path
import re
p=Path('/boot/limine.conf');text=p.read_text()
for key,value in [('quiet','no'),('timeout','10')]:
    pattern=rf'^{key}:.*$'
    text=re.sub(pattern,f'{key}: {value}',text,flags=re.M) if re.search(pattern,text,re.M) else f'{key}: {value}\n'+text
p.write_text(text)
PY
python3 "$REPO/scripts/install_state.py" verify "$backup"
python3 "$REPO/scripts/verify-boot.py"
systemctl daemon-reload
systemctl enable p16-charge-limit.service
pacman -Qk "$PACKAGE" "$PACKAGE-headers"
# UPower reads its configuration only at start, and its unit has no reload.
systemctl restart upower.service
[[ $(busctl --system call org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower GetCriticalAction) == 's "PowerOff"' ]] || {
  echo 'UPower meldet nicht PowerOff als kritische Akkuaktion.' >&2; exit 1;
}
sync
python3 "$REPO/scripts/install_state.py" complete "$backup"
printf 'Installation abgeschlossen. Sicherung: %s\nBeim nächsten Start %s im Limine-Menü auswählen.\n' "$backup" "$PACKAGE"
