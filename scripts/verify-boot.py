#!/usr/bin/env python3
"""Read-only PE/UKI and Limine BLAKE2b verification."""
import argparse
import hashlib
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from p16_release import IMAGE, PACKAGE, RELEASE
PINS = {'SSDT.aml': '9b28ffc14c19f8b7ea2278de88ff9ec070f5f70401107d81fef067a916aa9695'}

def sections(path):
    data = Path(path).read_bytes()
    if not data[:2] == b'MZ':
        raise ValueError('Kein PE-Image')
    pe = struct.unpack_from('<I', data, 60)[0]
    if not data[pe:pe + 4] == b'PE\x00\x00':
        raise ValueError('Ungültiger PE-Header')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    if machine != 0xaa64:
        raise ValueError('Kein ARM64-Image')
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    result = {}
    for i in range(count):
        h = pe + 24 + opt + i * 40
        name = data[h:h + 8].rstrip(b'\x00').decode()
        length, offset = struct.unpack_from('<II', data, h + 16)
        if not offset + length <= len(data):
            raise ValueError('PE-Sektion liegt außerhalb der Datei')
        result[name] = data[offset:offset + length]
    return result

def verify_image(path):
    sec = sections(path)
    if not sec['.uname'].rstrip(b'\x00\n').decode() == RELEASE:
        raise ValueError('Falscher Kernelrelease im UKI')
    modules = Path('/usr/lib/modules') / RELEASE
    symbols = (modules / 'build/System.map').read_text()
    if not re.search(' T arch_alloc_acpi_tables$', symbols, re.M):
        raise ValueError('ARM64-ACPI-Allocator fehlt')
    config = (modules / 'build/.config').read_text()
    for option in ['CONFIG_ACPI_TABLE_UPGRADE=y', 'CONFIG_ACPI_WMI=m', 'CONFIG_ASUS_WMI=m', 'CONFIG_ASUS_NB_WMI=m', 'CONFIG_SPI_HID_ACPI=m']:
        if not option in config.splitlines():
            raise ValueError(option)
    image = (modules / 'vmlinuz').read_bytes()
    if not sec['.linux'][:len(image)] == image:
        raise ValueError('Falscher Kernel im UKI')
    cmd = sec['.cmdline'].rstrip(b'\x00').decode()
    if not 'acpi_osi="!Windows 2022"' in cmd:
        raise ValueError('ACPI-OSI-Option fehlt im UKI')
    if not ('console=tty0' in cmd and 'acpi=nospcr' in cmd):
        raise ValueError('N1X-Konsolenoptionen fehlen im UKI')
    if re.search('(?:^|\\s)acpi=(?:off|noirq)(?:\\s|$)', cmd):
        raise ValueError('Unzulässige ACPI-Deaktivierung im UKI')
    data = sec['.initrd']
    offset = 0
    files = {}
    while data[offset:offset + 6] in (b'070701', b'070702'):
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        size, namesize = (fields[6], fields[11])
        start = offset + 110
        name = data[start:start + namesize - 1].decode().removeprefix('./').rstrip('/')
        start = start + namesize + 3 & ~3
        if not start + size <= len(data):
            raise ValueError('Abgeschnittenes frühes CPIO-Archiv')
        files[name] = data[start:start + size]
        offset = start + size + 3 & ~3
        if name == 'TRAILER!!!':
            break
    expected = {'kernel/firmware/acpi/' + name for name in PINS}
    actual = {name for name in files if name.startswith('kernel/firmware/acpi/')}
    if actual != expected:
        raise ValueError('Only the ASUS SSDT override is allowed; use the native firmware DSDT')
    for name, pin in PINS.items():
        if not hashlib.sha256(files['kernel/firmware/acpi/' + name]).hexdigest() == pin:
            raise ValueError('Falsche ACPI-Tabelle im UKI: ' + name)
    with tempfile.NamedTemporaryFile() as tmp:
        tmp.write(data)
        tmp.flush()
        listing = subprocess.check_output(['lsinitcpio', '--list', tmp.name], text=True)
    for module in ['nvidia.ko', 'nvidia-drm.ko', 'hid-asus.ko', 'spi-hid-acpi.ko', 'i2c-mt65xx.ko', 'i2c-hid-acpi.ko']:
        if not '/' + module in listing:
            raise ValueError('Modul fehlt: ' + module)
    for name in ['vfat', 'autofs4', 'zram', 'nvidia', 'nvidia_drm', 'spi_hid_acpi', 'hid_asus', 'wmi', 'asus_wmi', 'asus_nb_wmi']:
        deps = subprocess.check_output(['modprobe', '-S', RELEASE, '--show-depends', name], text=True)
        for line in deps.splitlines():
            if line.startswith('insmod '):
                if not Path(line.split()[1]).is_file():
                    raise ValueError(line)
        if not subprocess.check_output(['modinfo', '-k', RELEASE, '-F', 'vermagic', name], text=True).split()[0] == RELEASE:
            raise ValueError('Falsche Modulversion: ' + name)
    if not subprocess.check_output(['pacman', '-Qoq', '/usr/lib/modules/' + RELEASE + '/vmlinuz'], text=True).strip() == PACKAGE:
        raise ValueError('Kernelimage gehört nicht zum P16-Paket')
    print('UKI, ACPI-Overrides und Modulabhängigkeiten gültig:', path)

def verify_menu(config_path=Path('/boot/limine.conf'), boot_root=Path('/boot')):
    config = config_path.read_text()
    entries = []
    for line in config.splitlines():
        if line.lstrip().startswith('#'):
            continue
        for token in re.findall(r'boot\(\):/EFI/Linux/\S+', line):
            match = re.fullmatch(r'boot\(\):(/EFI/Linux/[^#\s]+\.efi)#([0-9a-fA-F]{128})', token)
            if match is None:
                raise ValueError('EFI-Eintrag ohne gültigen BLAKE2b-Hash: ' + token)
            path, pin = match.groups()
            image = boot_root / path.lstrip('/')
            if not image.resolve().is_relative_to((boot_root / 'EFI/Linux').resolve()):
                raise ValueError('EFI-Pfad außerhalb von EFI/Linux: ' + path)
            if hashlib.blake2b(image.read_bytes()).hexdigest() != pin.lower():
                raise ValueError('EFI-Hash stimmt nicht: ' + path)
            entries.append(path)
    if IMAGE not in entries:
        raise ValueError('P16-Eintrag fehlt im Limine-Menü')
    for key, expected in [('quiet', 'no'), ('timeout', '10')]:
        values = re.findall(r'^' + key + r':\s*(.*?)\s*$', config, re.M)
        if values != [expected]:
            raise ValueError('Limine benötigt genau einmal ' + key + ': ' + expected)
    print('Limine-Menü und', len(entries), 'EFI-Hashes gültig')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=Path('/boot' + IMAGE))
    parser.add_argument('--image-only', action='store_true')
    args = parser.parse_args()
    try:
        verify_image(args.image)
        if not args.image_only:
            verify_menu()
    except (ValueError, OSError, KeyError, struct.error, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Boot-Prüfung fehlgeschlagen: ' + str(error) + '\n')
