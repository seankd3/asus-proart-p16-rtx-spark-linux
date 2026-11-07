#!/usr/bin/env python3
"""Build pacman-owned snapshots of this machine's audited local P16 policy."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
OUT = Path.home() / '.cache/p16-local-packages'
SPEAKER = REPO / 'config/speaker-firmware.json'

PLATFORM = [
    '/etc/initcpio/install/p16-acpi',
    '/etc/initcpio/p16-acpi/SSDT.aml',
    '/etc/mkinitcpio.conf.d/zz-p16-hardware-fixes.conf',
    '/etc/mkinitcpio.conf.d/zz-p16-input.conf',
    '/etc/mkinitcpio.conf.d/zz-p16-keyboard.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-gpu.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-sleep.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-usb4.conf',
    '/etc/limine-entry-tool.d/zz-p16-keyboard.conf',
    '/etc/modprobe.d/nvidia.conf',
    '/etc/modprobe.d/omarchy-n1x-nvidiafb.conf',
    '/etc/systemd/system/p16-asus-controls.service',
    '/etc/systemd/system/p16-charge-limit.service',
    '/etc/systemd/system/power-profiles-daemon.service.d/20-p16-asus.conf',
    '/etc/UPower/UPower.conf.d/50-p16-critical.conf',
    '/etc/libinput/local-overrides.quirks',
    '/etc/pacman.d/hooks/00-p16-guard-preflight.hook',
    '/etc/pacman.d/hooks/zz-p16-boot-menu.hook',
    '/usr/local/sbin/p16-boot-menu-policy',
    '/usr/local/sbin/p16-guard-preflight',
    '/usr/share/p16-platform/90-mkinitcpio-install.hook',
    '/usr/local/libexec/p16-charge-limit',
    '/usr/local/bin/p16-temperatures',
]
SPEAKER_FILES = json.loads(SPEAKER.read_text())['files']
SPEAKERS = [f'/usr/lib/firmware/cirrus/{file["name"]}' for file in SPEAKER_FILES]
SPEAKERS += [f'/usr/lib/firmware/cirrus/{file["name"].replace("spkid0", "spkid1")}'
             for file in SPEAKER_FILES]
MAYFLOWER = [str(path) for path in sorted(Path('/usr/share/plymouth/themes/mayflower').iterdir())]
MAYFLOWER += [
    '/etc/mayflower-boot/logo.png',
    '/etc/pacman.d/hooks/80-mayflower-boot.hook',
    '/usr/local/sbin/mayflower-boot-refresh',
]
PACKAGES = {
    'p16-platform-policy': PLATFORM + [
        '/usr/local/sbin/p16-mkinitcpio-managed',
    ],
    'p16-speaker-firmware': SPEAKERS,
    'p16-mayflower': MAYFLOWER,
}
SOURCES = {
    '/etc/initcpio/install/p16-acpi': REPO / 'config/p16-acpi',
    '/usr/local/sbin/p16-mkinitcpio-managed': REPO / 'scripts/p16-mkinitcpio-managed',
    '/usr/local/sbin/p16-guard-preflight': REPO / 'scripts/p16-guard-preflight',
    '/usr/share/p16-platform/90-mkinitcpio-install.hook': REPO / 'config/90-mkinitcpio-install.hook',
    '/etc/pacman.d/hooks/00-p16-guard-preflight.hook': REPO / 'config/00-p16-guard-preflight.hook',
}


def snapshot(name, paths):
    directory = OUT / name
    directory.mkdir(parents=True, exist_ok=True)
    payload = directory / 'payload'
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir()
    for destination in paths:
        target = payload / destination.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        source = SOURCES.get(destination, Path(destination))
        if not source.exists() and not source.is_symlink():
            raise SystemExit(f'Missing source: {source}')
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
        if destination in ('/usr/local/sbin/p16-mkinitcpio-managed',
                           '/usr/local/sbin/p16-guard-preflight'):
            target.chmod(0o755)
    if name == 'p16-speaker-firmware':
        for record in SPEAKER_FILES:
            path = payload / 'usr/lib/firmware/cirrus' / record['name']
            data = path.read_bytes()
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise SystemExit(f'Speaker firmware differs from pinned vendor source: {record["name"]}')
    archive = directory / 'payload.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(payload, arcname='payload')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    backup = ' '.join(f"'{path.lstrip('/')}" + "'" for path in paths if path.startswith('/etc/'))
    (directory / 'PKGBUILD').write_text(f'''pkgname={name}
pkgver=1.0.0
pkgrel=1
pkgdesc="Local ASUS P16 update policy snapshot"
arch=('aarch64')
license=('custom')
source=('payload.tar.gz')
sha256sums=('{digest}')
backup=({backup})
package() {{
  cp -a "$srcdir/payload/." "$pkgdir/"
}}
''')
    subprocess.run(['makepkg', '--force', '--noconfirm'], cwd=directory, check=True)
    for package in sorted(directory.glob(f'{name}-1.0.0-1-aarch64.pkg.tar.*')):
        print(package)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    names = sys.argv[1:] or list(PACKAGES)
    for name in names:
        if name not in PACKAGES:
            raise SystemExit(f'Unknown package: {name}')
        snapshot(name, PACKAGES[name])


if __name__ == '__main__':
    main()
