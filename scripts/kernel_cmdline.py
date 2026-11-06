#!/usr/bin/env python3
"""Build the validated P16 command line while preserving root/LUKS arguments."""
from pathlib import Path
import shlex
import sys

PLATFORM_OPTIONS = [
    'console=tty0', 'acpi=nospcr', 'acpi_osi=!Windows 2022',
    'pci=hpbussize=0x80,hpmmiosize=32M,hpmmioprefsize=32G',
]


def build(text):
    args = shlex.split(text)
    # A drop-in that appends a quoted argument (zz-p16-keyboard.conf: ' acpi_osi=...') leaves its leading space in the token.
    # Option names are matched after that space; every token that stays keeps its exact value.
    names = [a.lstrip() for a in args]
    if not any(n.startswith(('root=', 'rd.luks.name=', 'cryptdevice=')) for n in names):
        raise ValueError('Run from the installed system, not the live ISO.')
    blocked = ('BOOT_IMAGE=', 'initrd=', 'acpi_osi=', 'console=', 'acpi=',
               'systemd.unit=', 'rd.driver.blacklist=', 'module_blacklist=',
               'modprobe.blacklist=', 'power_wrap_drv.usb4_release=', 'pci=',
               'mem_sleep_default=', 'initcall_blacklist=')
    initcalls = ['simpledrm_platform_driver_init']
    for name in names:
        if name.startswith('initcall_blacklist='):
            initcalls.extend(x for x in name.split('=', 1)[1].split(',') if x)
    args = [a for a, n in zip(args, names) if not n.startswith(blocked)]
    args += PLATFORM_OPTIONS + ['initcall_blacklist=' + ','.join(dict.fromkeys(initcalls))]
    for arg in args:
        if any(c in arg for c in ('"', '\n', '\r', '\x00')):
            raise ValueError('Invalid character in kernel command line.')
    return ' '.join('acpi_osi="!Windows 2022"' if a == 'acpi_osi=!Windows 2022' else
                    '"' + a + '"' if any(c.isspace() for c in a) else a for a in args) + '\n'


if __name__ == '__main__':
    Path(sys.argv[2]).write_text(build(Path(sys.argv[1]).read_text()))
