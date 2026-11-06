"""The P16 kernel release, read from its one source: pkgver and pkgrel in kernel/PKGBUILD."""
from pathlib import Path
import re

_PKGBUILD = (Path(__file__).resolve().parents[1] / 'kernel/PKGBUILD').read_text()
PKGVER, PKGREL = (re.search(rf'^{name}=(\S+)$', _PKGBUILD, re.M)[1] for name in ('pkgver', 'pkgrel'))
PACKAGE = 'linux-p16-asus-' + PKGREL
RELEASE = f'{PKGVER}-{PKGREL}-omarchy-p16-asus'
IMAGE = f'/EFI/Linux/omarchy_{PACKAGE}.efi'
