#!/usr/bin/env python3
"""Build the validated P16 command line while preserving root/LUKS arguments."""
from pathlib import Path
import shlex
import sys

PLATFORM_OPTIONS = [
    'console=tty0', 'acpi=nospcr', 'acpi_osi=!Windows 2022',
    'power_wrap_drv.usb4_release=0',
    'pci=hpbussize=0x80,hpmmiosize=32M,hpmmioprefsize=32G',
    'mem_sleep_default=s2idle',
]


def build(text):
    # A drop-in that appends a quoted argument (zz-p16-keyboard.conf: ' acpi_osi=...') leaves its spaces in the token.
    args = [a.strip() for a in shlex.split(text) if a.strip()]
    if not any(a.startswith(('root=', 'rd.luks.name=', 'cryptdevice=')) for a in args):
        raise ValueError('Run from the installed system, not the live ISO.')
    blocked = ('BOOT_IMAGE=', 'initrd=', 'acpi_osi=', 'console=', 'acpi=',
               'systemd.unit=', 'rd.driver.blacklist=', 'module_blacklist=',
               'modprobe.blacklist=', 'power_wrap_drv.usb4_release=', 'pci=',
               'mem_sleep_default=', 'initcall_blacklist=')
    initcalls = ['simpledrm_platform_driver_init']
    for arg in args:
        if arg.startswith('initcall_blacklist='):
            initcalls.extend(x for x in arg.split('=', 1)[1].split(',') if x)
    args = [a for a in args if not a.startswith(blocked)]
    args += PLATFORM_OPTIONS + ['initcall_blacklist=' + ','.join(dict.fromkeys(initcalls))]
    for arg in args:
        if any(c in arg for c in ('"', '\n', '\r', '\x00')):
            raise ValueError('Invalid character in kernel command line.')
    return ' '.join('acpi_osi="!Windows 2022"' if a == 'acpi_osi=!Windows 2022' else
                    '"' + a + '"' if any(c.isspace() for c in a) else a for a in args) + '\n'


if __name__ == '__main__':
    Path(sys.argv[2]).write_text(build(Path(sys.argv[1]).read_text()))
