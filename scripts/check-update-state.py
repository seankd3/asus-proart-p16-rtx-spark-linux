#!/usr/bin/env python3
"""Read-only check of installed P16 policy after an Omarchy update."""
from pathlib import Path
import subprocess

from p16_release import PACKAGE, RELEASE

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'config/p16-acpi': '/etc/initcpio/install/p16-acpi',
    'config/zz-p16-hardware-fixes.conf': '/etc/mkinitcpio.conf.d/zz-p16-hardware-fixes.conf',
    'config/zz-p16-keyboard.conf': '/etc/mkinitcpio.conf.d/zz-p16-keyboard.conf',
    'config/zz-p16-input.conf': '/etc/mkinitcpio.conf.d/zz-p16-input.conf',
    'config/nvidia.conf': '/etc/modprobe.d/nvidia.conf',
    'config/omarchy-n1x-nvidiafb.conf': '/etc/modprobe.d/omarchy-n1x-nvidiafb.conf',
    'config/00-omarchy-n1x-usb4.conf': '/etc/limine-entry-tool.d/00-omarchy-n1x-usb4.conf',
    'config/00-omarchy-n1x-gpu.conf': '/etc/limine-entry-tool.d/00-omarchy-n1x-gpu.conf',
    'config/00-omarchy-n1x-sleep.conf': '/etc/limine-entry-tool.d/00-omarchy-n1x-sleep.conf',
    'scripts/p16-charge-limit': '/usr/local/libexec/p16-charge-limit',
    'scripts/p16-temperatures': '/usr/local/bin/p16-temperatures',
    'config/p16-asus-controls.service': '/etc/systemd/system/p16-asus-controls.service',
    'config/p16-charge-limit.service': '/etc/systemd/system/p16-charge-limit.service',
    'config/20-p16-asus.conf': '/etc/systemd/system/power-profiles-daemon.service.d/20-p16-asus.conf',
    'config/50-p16-critical.conf': '/etc/UPower/UPower.conf.d/50-p16-critical.conf',
}


def main():
    issues = []
    for source, destination in FILES.items():
        if not Path(destination).is_file() or (ROOT / source).read_bytes() != Path(destination).read_bytes():
            issues.append(f'{destination} differs from P16 source')
    critical = subprocess.run(['busctl', '--system', 'call', 'org.freedesktop.UPower',
                               '/org/freedesktop/UPower', 'org.freedesktop.UPower',
                               'GetCriticalAction'], text=True, capture_output=True)
    if critical.stdout.strip() != 's "PowerOff"':
        issues.append('running UPower critical action is not PowerOff')
    keyboard = Path('/etc/limine-entry-tool.d/zz-p16-keyboard.conf')
    if not keyboard.is_file() or 'acpi_osi=' not in keyboard.read_text():
        issues.append('P16 keyboard ACPI option missing')
    for name in (PACKAGE, PACKAGE + '-headers'):
        if subprocess.run(['pacman', '-Q', name], capture_output=True).returncode:
            issues.append(f'{name} package missing')
    nvidia = subprocess.run(['pacman', '-Q', 'nvidia-utils'], text=True, capture_output=True)
    module = subprocess.run(['modinfo', '-k', RELEASE,
                             '-F', 'version', 'nvidia'], text=True, capture_output=True)
    if nvidia.returncode or module.returncode or nvidia.stdout.split()[1].rsplit('-', 1)[0] != module.stdout.strip():
        issues.append('NVIDIA module for the P16 kernel does not match installed userspace')
    if subprocess.run(['modinfo', '-k', RELEASE, 'spbm'],
                      capture_output=True).returncode:
        issues.append('spark_hwmon/spbm module for the P16 kernel is missing')
    if not Path('/etc/pacman.d/hooks/zz-p16-boot-menu.hook').is_file():
        issues.append('P16 boot-menu update hook missing')
    if not Path('/etc/pacman.d/hooks/00-p16-guard-preflight.hook').is_file():
        issues.append('P16 pacman preflight hook missing')
    if not Path('/home/johann/.config/omarchy/hooks/pre-refresh-pacman.d/omarchy-p16-pre-refresh-pacman').is_file():
        issues.append('P16 Omarchy pre-refresh hook missing')
    guard = Path('/etc/pacman.d/hooks/90-mkinitcpio-install.hook')
    if not guard.is_file() or guard.read_bytes() != (ROOT / 'config/90-mkinitcpio-install.hook').read_bytes():
        issues.append('managed-kernel mkinitcpio guard missing or changed')
    if 'NoUpgrade = etc/pacman.d/hooks/90-mkinitcpio-install.hook' not in Path('/etc/pacman.conf').read_text():
        issues.append('pacman NoUpgrade for mkinitcpio guard missing')
    for name in ('p16-platform-policy', 'p16-speaker-firmware', 'p16-mayflower'):
        if subprocess.run(['pacman', '-Qq', name], capture_output=True).returncode:
            issues.append(f'{name} package missing')
    peripherals = subprocess.run(['python3', str(ROOT / 'scripts/peripherals.py'), 'check-installed'],
                                 text=True, capture_output=True)
    if peripherals.returncode:
        issues.append('touchpad/speaker firmware verification failed: ' + peripherals.stderr.strip())
    if issues:
        print('P16 update check: ' + '; '.join(issues))
        return 1
    print('P16 update check: installed policy, firmware and kernel packages match; boot and hardware use require separate checks')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
