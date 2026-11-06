"""The P16 kernel release, read from the recipe makepkg builds: pkgver and pkgrel in build/kernel/PKGBUILD.

scripts/build-kernel.sh assembles that recipe. Shell scripts read the release with --shell.
"""
from pathlib import Path
import re
import shlex
import sys

RECIPE = Path(__file__).resolve().parents[1] / 'build/kernel'
_PKGBUILD = (RECIPE / 'PKGBUILD').read_text()
PKGVER, PKGREL = (re.search(rf'^{name}=(\S+)$', _PKGBUILD, re.M)[1] for name in ('pkgver', 'pkgrel'))
PACKAGE = 'linux-p16-asus-' + PKGREL
# The kernel names version x.y as x.y.0.
RELEASE = '.'.join((PKGVER.split('.') + ['0'])[:3]) + f'-{PKGREL}-omarchy-p16-asus'
IMAGE = f'/EFI/Linux/omarchy_{PACKAGE}.efi'
# limine-entry-tool writes this kernel's menu command line from KERNEL_CMDLINE[PACKAGE], on install and on every rebuild.
CMDLINE_DROPIN = f'/etc/limine-entry-tool.d/zz-p16-cmdline-{PACKAGE}.conf'
PACKAGES = [RECIPE / f'{PACKAGE}{part}-{PKGVER}-{PKGREL}-aarch64.pkg.tar.zst' for part in ('', '-headers')]

if __name__ == '__main__':
    if sys.argv[1:] != ['--shell']:
        sys.exit('Usage: p16_release.py --shell')
    for name in ('PACKAGE', 'RELEASE', 'IMAGE', 'CMDLINE_DROPIN'):
        print(f'{name}={shlex.quote(globals()[name])}')
    print('PACKAGES=(' + ' '.join(shlex.quote(str(p)) for p in PACKAGES) + ')')
