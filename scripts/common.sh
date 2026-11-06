#!/usr/bin/env bash
set -euo pipefail
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PKGVER=$(sed -n 's/^pkgver=//p' "$REPO/kernel/PKGBUILD")
PKGREL=$(sed -n 's/^pkgrel=//p' "$REPO/kernel/PKGBUILD")
PACKAGE=linux-p16-asus-$PKGREL
RELEASE=$PKGVER-$PKGREL-omarchy-p16-asus
check_model() {
  [[ $(uname -m) == aarch64 ]] || { echo 'AArch64 required.' >&2; exit 1; }
  [[ $(cat /sys/class/dmi/id/sys_vendor) == ASUS &&
     $(cat /sys/class/dmi/id/product_name) == 'ProArt P16 H7607BA' ]] || {
    echo 'This repository requires the ASUS ProArt P16 H7607BA.' >&2; exit 1;
  }
}
check_machine() {
  check_model
  [[ $(cat /sys/class/dmi/id/bios_version) == H7607BA.300 ]] || {
    echo 'Installation requires BIOS H7607BA.300.' >&2; exit 1;
  }
}
require_root() { [[ $EUID == 0 ]] || { echo "Mit sudo ausführen: $0" >&2; exit 1; }; }

# The dump runs as root, but all subsequent compilation runs as the owner.
prepare_build_dir() {
  local build_uid=${SUDO_UID:-$(stat -c %u "$REPO")}
  local build_gid=${SUDO_GID:-$(stat -c %g "$REPO")}
  [[ $build_uid != 0 ]] || { echo 'Repository eines normalen Benutzers verwenden.' >&2; return 1; }
  install -d -m755 -o "$build_uid" -g "$build_gid" "$REPO/build"
}
