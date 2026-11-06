#!/usr/bin/env python3
"""Keep a validated P16 Limine default and timeout after menu rewrites."""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import tempfile

MENU = Path('/boot/limine.conf')
VALID_DEFAULTS = {
    'Omarchy/linux-p16-asus-23',
    'Omarchy/linux-p16-asus-24',
    'Omarchy/linux-p16-asus-25',
    'Omarchy/linux-p16-asus-26',
    'Omarchy/linux-p16-asus-23-npu2',
    'Omarchy/linux-rtx-spark-npu',
}


def main():
    if os.geteuid():
        raise SystemExit('Run as root')
    with Path('/run/lock/boot-partition.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = MENU.read_text()
        defaults = re.findall(r'^default_entry:\s*(.*?)\s*$', original, re.M)
        if len(defaults) != 1 or defaults[0] not in VALID_DEFAULTS:
            raise SystemExit(f'Unexpected Limine default: {defaults}')
        default_kernel = defaults[0].rsplit('/', 1)[-1]
        if not re.search(rf'^\s*//{re.escape(default_kernel)}\s*$', original, re.M):
            raise SystemExit(f'Default Limine entry is missing: {defaults[0]}')
        timeouts = re.findall(r'^timeout:\s*(.*?)\s*$', original, re.M)
        if len(timeouts) != 1:
            raise SystemExit(f'Expected one Limine timeout, found {timeouts}')
        paths = re.findall(r'boot\(\):(/EFI/Linux/[^#\s]+)#([a-fA-F0-9]{128})', original)
        if not paths:
            raise SystemExit('No hashed EFI images in Limine menu')
        for name, digest in paths:
            image = Path('/boot') / name.lstrip('/')
            if not image.is_file() or hashlib.blake2b(image.read_bytes()).hexdigest() != digest.lower():
                raise SystemExit(f'EFI image missing or checksum mismatch: {name}')
        if timeouts[0] == '10':
            print('P16 Limine default and timeout already correct; EFI hashes valid')
            return
        updated = re.sub(r'^timeout:.*$', 'timeout: 10', original, count=1, flags=re.M)
        descriptor, temp_name = tempfile.mkstemp(prefix='.p16-limine-', dir=MENU.parent)
        try:
            with os.fdopen(descriptor, 'w') as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp_name, MENU.stat().st_mode & 0o777)
            if MENU.read_text() != original:
                raise SystemExit('Limine menu changed during timeout update')
            os.replace(temp_name, MENU)
            os.sync()
        finally:
            Path(temp_name).unlink(missing_ok=True)
        print(
            f'P16 Limine default verified: {defaults[0]}; '
            f'timeout {timeouts[0]} -> 10; EFI hashes valid'
        )


if __name__ == '__main__':
    main()
