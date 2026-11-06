#!/usr/bin/env python3
"""Persistent installation checkpoint and paired configuration/EFI backups."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from p16_release import IMAGE, PACKAGE, RELEASE

TARGET = '/boot' + IMAGE
CONFIG_PATHS = [
    '/boot/limine.conf', '/etc/vconsole.conf', '/etc/initcpio/install/p16-acpi',
    '/etc/mkinitcpio.conf.d/zz-p16-hardware-fixes.conf',
    '/etc/mkinitcpio.conf.d/zz-p16-keyboard.conf',
    '/etc/mkinitcpio.conf.d/zz-p16-input.conf',
    '/etc/limine-entry-tool.d/zz-p16-keyboard.conf',
    '/etc/systemd/system/p16-asus-controls.service',
    '/etc/systemd/system/p16-charge-limit.service',
    '/etc/systemd/system/power-profiles-daemon.service.d/20-p16-asus.conf',
    '/etc/systemd/system/multi-user.target.wants/p16-charge-limit.service',
    '/usr/local/libexec/p16-charge-limit', '/usr/local/bin/p16-temperatures',
    '/etc/initcpio/p16-acpi/DSDT.aml', '/etc/initcpio/p16-acpi/SSDT.aml',
]


# Older checkpoints predate these files; keep them restorable. New checkpoints
# include every additional path before the installer writes it.
ADDED_CONFIG_PATHS = [
    '/etc/modprobe.d/nvidia.conf',
    '/etc/modprobe.d/omarchy-n1x-nvidiafb.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-usb4.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-gpu.conf',
    '/etc/limine-entry-tool.d/00-omarchy-n1x-sleep.conf',
    '/etc/libinput/local-overrides.quirks',
    '/etc/mkinitcpio.conf.d/zz-p16-speakers.conf',
    '/etc/UPower/UPower.conf.d/50-p16-critical.conf',
    '/usr/lib/firmware/cirrus/cs35l56-b0-dsp1-misc-10431484-spkid0.wmfw',
    '/usr/lib/firmware/cirrus/cs35l56-b0-dsp1-misc-10431484-spkid0-l1u0.bin',
    '/usr/lib/firmware/cirrus/cs35l56-b0-dsp1-misc-10431484-spkid0-l1u1.bin',
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'blake2b').hexdigest()


def boot_environment(root):
    """Only versions relevant to restored UKIs; exclude our added kernel."""
    packages = {}
    for desc in (root / 'var/lib/pacman/local').glob('*/desc'):
        fields = desc.read_text().splitlines()
        name = fields[fields.index('%NAME%') + 1]
        version = fields[fields.index('%VERSION%') + 1]
        if name in (PACKAGE, PACKAGE + '-headers'):
            continue
        if name.startswith(('linux-', 'nvidia', 'libnvidia', 'opencl-nvidia')):
            packages[name] = version
    kernels = {p.parent.name: digest(p) for p in (root / 'usr/lib/modules').glob('*/vmlinuz')
               if p.parent.name != RELEASE}
    return {'packages': packages, 'kernels': kernels}


def require_compatible_boot(state, root):
    require('boot_environment' in state,
            'This backup lacks boot compatibility metadata; automatic restoration is refused.')
    require(state['boot_environment'] == boot_environment(root),
            'Stock kernels or NVIDIA packages changed since this backup. '
            'Restoration is refused before changing files; keep the current stock boot images.')


def save(backup, state):
    temporary = backup / 'state.json.tmp'
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    temporary.replace(backup / 'state.json')
    directory = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load(backup):
    state = json.loads((backup / 'state.json').read_text())
    require(state['schema'] == 1 and state['release'] == RELEASE, 'Unpassender Installationsstand')
    require(set(state['files']).issuperset(CONFIG_PATHS + [TARGET]), 'Unvollständige Sicherung')
    for name, info in state['files'].items():
        path = Path(name)
        require(name in CONFIG_PATHS + ADDED_CONFIG_PATHS or (path.parent == Path('/boot/EFI/Linux') and path.suffix == '.efi'),
                'Unzulässiger Sicherungspfad: ' + name)
        require(info['kind'] in ('file', 'link', 'absent'), 'Ungültiger Dateityp')
        if info['kind'] == 'file':
            saved = backup / 'files' / name.lstrip('/')
            require(saved.is_file() and not saved.is_symlink() and digest(saved) == info['digest'],
                    'Sicherung fehlt oder ist beschädigt: ' + name)
    return state


def begin(backup, packages, root=Path('/')):
    require(not (backup / 'state.json').exists(), 'Checkpoint existiert bereits')
    environment = boot_environment(root)
    require(environment['packages'] and environment['kernels'], 'Stock kernel/package metadata is required for a restorable backup.')
    images = root / 'boot/EFI/Linux'
    names = list(dict.fromkeys(CONFIG_PATHS + ADDED_CONFIG_PATHS + ['/' + p.relative_to(root).as_posix()
                for p in sorted(images.glob('*.efi'))] + [TARGET]))
    state = {'schema': 1, 'release': RELEASE, 'status': 'running',
             'packages': {p.name: digest(p) for p in packages},
             'boot_environment': environment, 'files': {}}
    for name in names:
        source = root / name.lstrip('/')
        if source.is_symlink():
            require(not name.startswith('/boot/EFI/Linux/'), 'EFI-Symlinks werden nicht gesichert')
            state['files'][name] = {'kind': 'link', 'target': os.readlink(source)}
        elif source.exists():
            require(source.is_file(), 'Keine reguläre Datei: ' + name)
            saved = backup / 'files' / name.lstrip('/')
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, saved)
            with saved.open('rb') as stream:
                os.fsync(stream.fileno())
            pin = digest(saved)
            require(digest(source) == pin, 'Datei wurde während der Sicherung verändert: ' + name)
            state['files'][name] = {'kind': 'file', 'digest': pin}
        else:
            state['files'][name] = {'kind': 'absent'}
    save(backup, state)
    return state


def resume(backup, packages, root=Path('/')):
    state = load(backup)
    require(set(ADDED_CONFIG_PATHS).issubset(state['files']),
            'This older checkpoint can be restored, but resume requires the original installer revision.')
    require(state['status'] in ('running', 'failed', 'restored'), 'Installation ist bereits abgeschlossen')
    require({p.name: digest(p) for p in packages} == state['packages'],
            'Zur Wiederaufnahme dieselben unveränderten Kernelpakete verwenden')
    restore(backup, root=root, status='failed')
    state['status'] = 'running'
    save(backup, state)


def verify_preserved(backup, root=Path('/')):
    state = load(backup)
    for name, info in state['files'].items():
        if name.startswith('/boot/EFI/Linux/') and info['kind'] == 'file':
            current = root / name.lstrip('/')
            require(current.is_file() and digest(current) == info['digest'], 'Vorhandenes EFI-Image verändert: ' + name)
    old = (backup / 'files/boot/limine.conf').read_text()
    current = (root / 'boot/limine.conf').read_text()
    import re
    require(re.findall(r'^default_entry:.*$', old, re.M) == re.findall(r'^default_entry:.*$', current, re.M),
            'Bootstandard wurde geändert')


def restore(backup, root=Path('/'), status='restored'):
    # Check installed boot versions before changing any live file. Restore menu last.
    state = load(backup)
    require_compatible_boot(state, root)
    for name in sorted(state['files'], key=lambda name: name == '/boot/limine.conf'):
        info = state['files'][name]
        destination = root / name.lstrip('/')
        if info['kind'] == 'absent':
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix='.p16-restore-', dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            if info['kind'] == 'link':
                temporary.unlink()
                temporary.symlink_to(info['target'])
            else:
                shutil.copy2(backup / 'files' / name.lstrip('/'), temporary)
                with temporary.open('rb') as stream:
                    os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    state['status'] = status
    save(backup, state)


def trusted_backup(path):
    require(not path.is_symlink() and path.parent == Path('/root') and path.name.startswith('p16-install.'),
            'Eine Sicherung unter /root/p16-install.* angeben')
    require(path.stat().st_uid == 0 and path.stat().st_mode & 0o022 == 0, 'Unsichere Sicherungsrechte')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['begin', 'resume', 'verify', 'complete', 'restore', 'fail'])
    parser.add_argument('backup', type=Path)
    parser.add_argument('packages', nargs='*', type=Path)
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, 'Mit sudo ausführen')
        backup = trusted_backup(args.backup)
        if args.action in ('begin', 'resume'):
            require(len(args.packages) == 2, 'Kernel und Headerpaket erforderlich')
            globals()[args.action](backup, args.packages)
        elif args.action == 'verify':
            verify_preserved(backup)
        elif args.action in ('restore', 'fail'):
            restore(backup, status='failed' if args.action == 'fail' else 'restored')
        else:
            state = load(backup)
            state['status'] = 'complete'
            save(backup, state)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
