"""Regression tests using temporary roots; no live installation is performed."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
# The scripts read the release from the assembled recipe. The reader runs here
# from a temporary repository root that holds a small one.
_ROOT = tempfile.TemporaryDirectory()
ROOT = Path(_ROOT.name)
(ROOT / 'scripts').mkdir()
for _name in ('common.sh', 'p16_release.py'):
    shutil.copy2(REPO / 'scripts' / _name, ROOT / 'scripts' / _name)
(ROOT / 'build/kernel').mkdir(parents=True)
(ROOT / 'build/kernel/PKGBUILD').write_text('pkgver=7.3\npkgrel=26\n')
sys.path.insert(0, str(ROOT / 'scripts'))
import p16_release as release  # noqa: E402
from p16_release import IMAGE  # noqa: E402

P16_EFI = Path(IMAGE).name
CMDLINE = 'root=/dev/mapper/root rootflags=subvol=@nv620 rw acpi_osi="!Windows 2022" console=tty0'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    loaded = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


state = module('install_state', 'install_state.py')  # verify-boot imports it
boot = module('verify_boot', 'verify-boot.py')
cmdline = module('kernel_cmdline', 'kernel_cmdline.py')


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / 'EFI/Linux'
        self.images.mkdir(parents=True)
        self.config = self.root / 'limine.conf'
        for name in ['stock.efi', P16_EFI]:
            (self.images / name).write_bytes(name.encode())

    def entry(self, name, cmdline=CMDLINE):
        pin = hashlib.blake2b((self.images / name).read_bytes()).hexdigest()
        return '  //' + name + '\n  path: boot():/EFI/Linux/' + name + '#' + pin + '\n  cmdline: ' + cmdline + '\n'

    def verify(self, extra, options='quiet: no\ntimeout: 10\n'):
        self.config.write_text(options + self.entry('stock.efi', 'rootflags=subvol=@') + extra)
        boot.verify_menu(CMDLINE, self.config, self.root)

    def test_valid_stock_and_p16(self):
        self.verify(self.entry(P16_EFI))

    def test_p16_entry_must_boot_the_uki_cmdline(self):
        # limine-entry-tool gave a 7.3 entry the default cmdline: root @, no RMSkipACPIBattCap. With Secure Boot off it wins.
        for line in ['rootflags=subvol=@ rw acpi_osi="!Windows 2022"', CMDLINE + ' mem_sleep_default=s2idle']:
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, 'Befehlszeile des UKI'):
                    self.verify(self.entry(P16_EFI, line))

    def test_missing_p16_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'P16-Eintrag fehlt'):
            self.verify('')

    def test_malformed_and_missing_hashes_are_rejected(self):
        for suffix in ['', '#INVALID', '#'+'a'*127, '#'+'a'*129, '#'+'a'*128+'junk']:
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, 'gültigen BLAKE2b'):
                    self.verify('path: boot():' + IMAGE + suffix+'\n')

    def test_wrong_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Hash stimmt nicht'):
            self.verify('path: boot():' + IMAGE + '#'+'0'*128+'\n')

    def test_missing_image_is_rejected(self):
        entry = self.entry(P16_EFI)
        (self.images / P16_EFI).unlink()
        with self.assertRaises(OSError):
            self.verify(entry)

    def test_comments_do_not_supply_missing_entry(self):
        with self.assertRaisesRegex(ValueError, 'P16-Eintrag fehlt'):
            self.verify(''.join('# ' + line + '\n' for line in self.entry(P16_EFI).splitlines()))

    def test_conflicting_menu_options_are_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(self.entry(P16_EFI), 'quiet: yes\nquiet: no\ntimeout: 10\n')

    def test_path_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'außerhalb'):
            self.verify('path: boot():/EFI/Linux/../../elsewhere.efi#'+'0'*128+'\n')


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.backup = self.base / 'backup'
        self.backup.mkdir()
        self.menu = self.root / 'boot/limine.conf'
        self.menu.parent.mkdir(parents=True)
        self.menu.write_text('default_entry: 2\noriginal menu\n')
        self.efi = self.root / 'boot/EFI/Linux/stock.efi'
        self.efi.parent.mkdir(parents=True)
        self.efi.write_bytes(b'original complete EFI bytes')
        self.enabled = self.root / 'etc/systemd/system/multi-user.target.wants/p16-charge-limit.service'
        self.enabled.parent.mkdir(parents=True)
        self.enabled.symlink_to('/original/charge.service')
        self.console=self.root/'etc/vconsole.conf'
        self.console.write_text('KEYMAP=us\n')
        self.stock_modules = self.root/'usr/lib/modules/7.2.8-23-omarchy'
        self.stock_modules.mkdir(parents=True)
        (self.stock_modules/'vmlinuz').write_bytes(b'original stock kernel')
        self.nvidia_desc = self.root/'var/lib/pacman/local/nvidia-utils-615/desc'
        self.nvidia_desc.parent.mkdir(parents=True)
        self.nvidia_desc.write_text('%NAME%\nnvidia-utils\n\n%VERSION%\n615.78.08-1\n')
        self.packages = [self.base/'kernel.pkg.tar.zst', self.base/'headers.pkg.tar.zst']
        for package in self.packages:
            package.write_bytes(package.name.encode())
        state.begin(self.backup, self.packages, self.root)

    def change_live_files(self):
        self.efi.write_bytes(b'broken EFI')
        self.console.write_text('KEYMAP=de\n')
        self.menu.write_text('default_entry: 3\nbroken menu\n')
        (self.root / state.TARGET.lstrip('/')).write_bytes(b'new incomplete P16 image')
        self.enabled.unlink()
        self.enabled.symlink_to('/new/charge.service')
        helper = self.root / 'usr/local/libexec/p16-charge-limit'
        helper.parent.mkdir(parents=True)
        helper.write_text('new helper')

    def test_new_driver_configs_are_removed_on_rollback(self):
        for name in state.ADDED_CONFIG_PATHS:
            path = self.root / name.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('new configuration')
        state.restore(self.backup, self.root, status='failed')
        for name in state.ADDED_CONFIG_PATHS:
            self.assertFalse((self.root / name.lstrip('/')).exists())

    def test_older_checkpoints_remain_readable(self):
        checkpoint = state.load(self.backup)
        for name in state.ADDED_CONFIG_PATHS:
            del checkpoint['files'][name]
        state.save(self.backup, checkpoint)
        state.load(self.backup)
        with self.assertRaisesRegex(ValueError, 'older checkpoint'):
            state.resume(self.backup, self.packages, self.root)

    def test_full_efi_bytes_are_backed_up(self):
        self.assertEqual((self.backup/'files/boot/EFI/Linux/stock.efi').read_bytes(), self.efi.read_bytes())

    def test_failure_restores_menu_images_symlinks_and_absent_files(self):
        self.change_live_files()
        state.restore(self.backup, self.root, status='failed')
        self.assertEqual(self.menu.read_text(), 'default_entry: 2\noriginal menu\n')
        self.assertEqual(self.efi.read_bytes(), b'original complete EFI bytes')
        self.assertFalse((self.root / state.TARGET.lstrip('/')).exists())
        self.assertFalse((self.root / 'usr/local/libexec/p16-charge-limit').exists())
        self.assertEqual(os.readlink(self.enabled), '/original/charge.service')
        self.assertEqual(self.console.read_text(), 'KEYMAP=us\n')
        self.assertEqual(state.load(self.backup)['status'], 'failed')

    def test_resume_after_interruption_keeps_packages_and_original_backup(self):
        self.change_live_files()
        installed = self.root / 'usr/lib/modules' / state.RELEASE / 'vmlinuz'
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b'installed kernel')
        state.resume(self.backup, self.packages, self.root)
        self.assertEqual(state.load(self.backup)['status'], 'running')
        self.assertEqual(installed.read_bytes(), b'installed kernel')
        self.assertEqual(self.efi.read_bytes(), b'original complete EFI bytes')
        state.verify_preserved(self.backup, self.root)

    def test_shell_failure_handler_restores_and_preserves_exit_code(self):
        self.change_live_files()
        commands = self.base/'commands'
        commands.mkdir()
        python = commands/'python3'
        python.write_text('#!/usr/bin/python3\n'
                          'import importlib.util, os, pathlib, sys\n'
                          'sys.path.insert(0, os.environ["TEST_RELEASE_DIR"])\n'
                          'spec=importlib.util.spec_from_file_location("state", os.environ["TEST_STATE_MODULE"])\n'
                          'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
                          'm.restore(pathlib.Path(sys.argv[3]), pathlib.Path(os.environ["TEST_ROOT"]), status="failed")\n')
        python.chmod(0o755)
        calls=self.base/'systemctl.calls'
        for name in ['systemctl', 'sync']:
            executable=commands/name
            executable.write_text(f'#!/bin/sh\necho "$0 $*" >> {calls}\nexit 0\n')
            executable.chmod(0o755)
        source=(REPO/'scripts/install-kernel.sh').read_text()
        handler=source[source.index('finish() {'):source.index('if [[ -n $resume_backup ]]; then')]
        env=dict(os.environ, backup=str(self.backup), hooks='',
                 PATH=str(commands)+':'+os.environ['PATH'], TEST_ROOT=str(self.root),
                 TEST_STATE_MODULE=str(REPO/'scripts/install_state.py'), TEST_RELEASE_DIR=str(ROOT/'scripts'))
        script='source '+str(REPO/'scripts/common.sh')+'\n'+handler+'\nexit 7\n'
        process=subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
        self.assertEqual(process.returncode, 7, process.stderr)
        self.assertIn('--resume', process.stderr)
        # Rollback restores UPower's file, so the daemon must read it again.
        self.assertIn('systemctl try-restart upower.service', calls.read_text())
        self.assertEqual(self.efi.read_bytes(), b'original complete EFI bytes')
        self.assertEqual(state.load(self.backup)['status'], 'failed')

    def test_resume_refuses_changed_packages_before_restoration(self):
        self.change_live_files()
        self.packages[0].write_bytes(b'changed package')
        with self.assertRaisesRegex(ValueError, 'unveränderten Kernelpakete'):
            state.resume(self.backup, self.packages, self.root)
        self.assertEqual(self.menu.read_text(), 'default_entry: 3\nbroken menu\n')

    def test_manual_restore_can_be_resumed_with_original_packages(self):
        self.change_live_files()
        state.restore(self.backup, self.root)
        self.assertEqual(state.load(self.backup)['status'], 'restored')
        state.resume(self.backup, self.packages, self.root)
        self.assertEqual(state.load(self.backup)['status'], 'running')

    def test_missing_stock_modules_block_restore_before_any_write(self):
        self.change_live_files()
        shutil.rmtree(self.stock_modules)
        with self.assertRaisesRegex(ValueError, 'Stock kernels or NVIDIA'):
            state.restore(self.backup, self.root)
        self.assertEqual(self.efi.read_bytes(), b'broken EFI')
        self.assertEqual(self.menu.read_text(), 'default_entry: 3\nbroken menu\n')

    def test_changed_stock_kernel_blocks_restore(self):
        (self.stock_modules/'vmlinuz').write_bytes(b'updated stock kernel')
        with self.assertRaisesRegex(ValueError, 'Stock kernels or NVIDIA'):
            state.restore(self.backup, self.root)

    def test_nvidia_update_blocks_restore_before_any_write(self):
        self.change_live_files()
        self.nvidia_desc.write_text('%NAME%\nnvidia-utils\n\n%VERSION%\n615.84.00-1\n')
        with self.assertRaisesRegex(ValueError, 'Stock kernels or NVIDIA'):
            state.restore(self.backup, self.root)
        self.assertEqual(self.efi.read_bytes(), b'broken EFI')

    def test_legacy_backup_without_compatibility_metadata_is_refused(self):
        saved=state.load(self.backup)
        del saved['boot_environment']
        state.save(self.backup, saved)
        with self.assertRaisesRegex(ValueError, 'lacks boot compatibility'):
            state.restore(self.backup, self.root)

    def test_unrelated_package_changes_do_not_block_restore(self):
        desc=self.root/'var/lib/pacman/local/bottom-1/desc'
        desc.parent.mkdir()
        desc.write_text('%NAME%\nbottom\n\n%VERSION%\n1.0-1\n')
        state.restore(self.backup, self.root)

    def test_complete_checkpoint_cannot_be_resumed(self):
        saved = state.load(self.backup)
        saved['status'] = 'complete'
        state.save(self.backup, saved)
        with self.assertRaises(ValueError):
            state.resume(self.backup, self.packages, self.root)

    def test_corrupt_backup_stops_restore_before_any_live_write(self):
        self.change_live_files()
        (self.backup/'files/boot/EFI/Linux/stock.efi').write_bytes(b'corrupt backup')
        with self.assertRaisesRegex(ValueError, 'beschädigt'):
            state.restore(self.backup, self.root)
        self.assertEqual(self.efi.read_bytes(), b'broken EFI')
        self.assertEqual(self.menu.read_text(), 'default_entry: 3\nbroken menu\n')

    def test_preservation_detects_changed_existing_image(self):
        self.efi.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'EFI-Image verändert'):
            state.verify_preserved(self.backup, self.root)

    def test_preservation_requires_every_saved_menu_entry(self):
        stock = '/+Omarchy\n//linux-p16-asus-24\n  protocol: efi\n  path: boot():/EFI/Linux/stock.efi#' + 'a' * 128 + '\n'
        self.menu.write_text('default_entry: 2\n' + stock)
        backup = self.base / 'menu-backup'
        backup.mkdir()
        state.begin(backup, self.packages, self.root)
        added = '//linux-p16-asus-26\n  protocol: efi\n  path: boot():' + IMAGE + '#' + 'b' * 128 + '\n'
        self.menu.write_text('timeout: 10\ndefault_entry: 2\n' + stock + added)
        state.verify_preserved(backup, self.root)
        self.menu.write_text('default_entry: 2\n/+Omarchy\n' + added)
        with self.assertRaisesRegex(ValueError, 'linux-p16-asus-24'):
            state.verify_preserved(backup, self.root)


class ValidationTests(unittest.TestCase):
    def test_fresh_build_directory_has_compiling_users_owner(self):
        if os.geteuid() == 0:
            self.skipTest('Run permission regression as the compiling user')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'scripts').mkdir()
            shutil.copy2(REPO/'scripts/common.sh', root/'scripts/common.sh')
            env = dict(os.environ, SUDO_UID=str(os.getuid()), SUDO_GID=str(os.getgid()))
            subprocess.run(['bash', '-c', 'source "$1"; prepare_build_dir', 'bash', str(root/'scripts/common.sh')], env=env, check=True)
            build = root/'build'
            self.assertEqual(build.stat().st_uid, os.getuid())
            (build/'compiler').mkdir()

    def test_model_check_allows_updated_bios_but_install_check_rejects_it(self):
        shell = r'''uname() { printf aarch64; }
cat() {
  case "$1" in
    */sys_vendor) printf ASUS ;;
    */product_name) printf 'ProArt P16 H7607BA' ;;
    */bios_version) printf H7607BA.301 ;;
    *) return 1 ;;
  esac
}
source "$1"
'''
        for check, expected in [('check_model', 0), ('check_machine', 1)]:
            result=subprocess.run(['bash','-c',shell+check,'bash',str(REPO/'scripts/common.sh')], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_wrong_kernel_identity_is_rejected(self):
        # Minimal ARM64 PE carrying a .uname section, independent of a host kernel.
        raw = bytearray(256)
        raw[:2] = b'MZ'
        struct.pack_into('<I', raw, 0x3c, 64)
        raw[64:68] = b'PE\0\0'
        struct.pack_into('<HH', raw, 68, 0xaa64, 1)
        raw[88:96] = b'.uname\0\0'
        name = b'wrong-kernel\0'
        struct.pack_into('<II', raw, 104, len(name), 192)
        raw[192:192+len(name)] = name
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/'image.efi'
            image.write_bytes(raw)
            with self.assertRaises(ValueError):
                boot.verify_image(image)

    def test_corrupt_acpica_archive_is_rejected_before_build(self):
        with patch.object(Path, 'read_bytes', return_value=b'corrupt archive'), patch('subprocess.run') as run:
            with self.assertRaises(ValueError):
                runpy.run_path(str(REPO/'scripts/build-acpi.py'))
            run.assert_not_called()

    def test_charge_helper_rejects_wrong_model_before_write(self):
        with patch.object(Path, 'read_text', return_value='another laptop'), patch.object(Path, 'write_text') as write:
            with self.assertRaises(ValueError):
                runpy.run_path(str(REPO/'scripts/p16-charge-limit'))
            write.assert_not_called()

    def test_downloader_rejects_wrong_architecture_before_curl(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp)/'scripts/download-nightly.py'
            script.parent.mkdir()
            shutil.copy2(REPO/'scripts/download-nightly.py', script)
            response = io.BytesIO(json.dumps({'arch':'x86_64', 'channel':'edge'}).encode())
            with patch('urllib.request.urlopen', return_value=response), patch('subprocess.run') as run:
                with self.assertRaises(ValueError):
                    runpy.run_path(str(script))
                run.assert_not_called()


class CommandLineTests(unittest.TestCase):
    def test_fresh_install_preserves_encryption_and_adds_platform_options(self):
        host = 'cryptdevice=UUID=example:root root=/dev/mapper/root rootflags=subvol=@ rw'
        result = cmdline.build(host)
        args = shlex.split(result)
        for arg in shlex.split(host) + cmdline.PLATFORM_OPTIONS:
            self.assertIn(arg, args)
        self.assertIn('initcall_blacklist=simpledrm_platform_driver_init', args)
        self.assertIn('acpi_osi="!Windows 2022"', result)
        self.assertEqual(cmdline.build(result), result)

    def test_conflicts_and_recovery_flags_are_replaced(self):
        args = shlex.split(cmdline.build(
            'root=/dev/mapper/root pci=nomsi mem_sleep_default=deep acpi=off '
            'power_wrap_drv.usb4_release=0 '
            'module_blacklist=nvidia systemd.unit=emergency.target '
            'initcall_blacklist=other_init'))
        self.assertNotIn('pci=nomsi', args)
        self.assertNotIn('acpi=off', args)
        self.assertNotIn('systemd.unit=emergency.target', args)
        self.assertNotIn('module_blacklist=nvidia', args)
        # Omarchy 1112 and 1113 make these the defaults; the new entry carries neither.
        self.assertFalse([a for a in args if a.startswith(('mem_sleep_default=', 'power_wrap_drv.usb4_release='))])
        self.assertIn('initcall_blacklist=simpledrm_platform_driver_init,other_init', args)

    def test_limine_dropins_preserve_option_boundaries(self):
        files = [REPO/'config'/name for name in (
            '00-omarchy-n1x-usb4.conf', '00-omarchy-n1x-gpu.conf',
            '00-omarchy-n1x-sleep.conf', 'limine-p16-keyboard.conf')]
        script = 'declare -A KERNEL_CMDLINE; KERNEL_CMDLINE[default]="root=/dev/mapper/root"; '
        script += '; '.join('source ' + shlex.quote(str(p)) for p in files)
        script += '; printf "%s" "${KERNEL_CMDLINE[default]}"'
        args = shlex.split(subprocess.check_output(['bash', '-c', script], text=True))
        self.assertIn('root=/dev/mapper/root', args)
        for arg in cmdline.PLATFORM_OPTIONS[2:]:
            self.assertIn(arg, args)

    def test_upower_dropin_parses_as_poweroff(self):
        from gi.repository import GLib
        path = REPO/'config/50-p16-critical.conf'
        # UPower skips a drop-in whose name does not match its pattern.
        self.assertRegex(path.name, r'^([0-9][0-9])-([a-zA-Z0-9-_])*\.conf$')
        keyfile = GLib.KeyFile()
        keyfile.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        self.assertEqual(keyfile.get_groups()[0], ['UPower'])
        self.assertEqual(keyfile.get_keys('UPower')[0], ['CriticalPowerAction'])
        self.assertEqual(keyfile.get_string('UPower', 'CriticalPowerAction'), 'PowerOff')

    def test_quoted_dropin_argument_is_replaced(self):
        # A 7.2.8-23 boot got zz-p16-keyboard.conf's value with its single quotes, and the install refused it.
        result = cmdline.build("root=/dev/mapper/root ' acpi_osi=\"!Windows 2022\"' 'rootflags=subvol=system ' quiet")
        self.assertEqual(result.count('acpi_osi='), 1)
        self.assertIn('acpi_osi="!Windows 2022"', result)
        # A kept argument keeps its exact value, spaces included.
        self.assertIn('"rootflags=subvol=system "', result)

    def test_live_iso_is_rejected(self):
        with self.assertRaises(ValueError):
            cmdline.build('quiet splash')


class ReleaseTests(unittest.TestCase):
    SHELL = 'source "$1"; release=$(python3 "$REPO/scripts/p16_release.py" --shell); eval "$release"; '

    def test_shell_reads_the_release_the_kernel_reports(self):
        # pkgver 7.3 builds the kernel 7.3.0. A plain join gave 7.3-26, which no kernel reports.
        self.assertEqual(release.RELEASE, '7.3.0-26-omarchy-p16-asus')
        script = self.SHELL + 'printf "%s\\n" "$PACKAGE" "$RELEASE" "$IMAGE" "${PACKAGES[@]}"'
        shell = subprocess.check_output(['bash', '-c', script, 'bash', str(ROOT/'scripts/common.sh')], text=True)
        self.assertEqual(shell.splitlines(), [release.PACKAGE, release.RELEASE, release.IMAGE, *map(str, release.PACKAGES)])

    def test_missing_recipe_stops_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)/'scripts'
            scripts.mkdir()
            for name in ('common.sh', 'p16_release.py'):
                shutil.copy2(REPO/'scripts'/name, scripts/name)
            result = subprocess.run(['bash', '-c', self.SHELL + 'echo reached', 'bash', str(scripts/'common.sh')],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('reached', result.stdout)
        self.assertIn('FileNotFoundError', result.stderr)


class KernelInputsTests(unittest.TestCase):
    def test_delta_patches_are_in_the_recipe_with_their_sums_and_none_is_orphaned(self):
        kernel = REPO / 'kernel'
        added = ''.join(line[1:] + '\n' for line in (kernel/'PKGBUILD.patch').read_text().splitlines()
                        if line.startswith('+') and not line.startswith('+++'))
        names, sha256, blake2 = (
            shlex.split(re.search(rf'^{array}\+=\((.*?)^\)', added, re.M | re.S)[1])
            for array in ('source', 'sha256sums', 'b2sums'))
        self.assertEqual(len(names), len(sha256))
        self.assertEqual(len(names), len(blake2))
        for name, sha, b2 in zip(names, sha256, blake2):
            data = (kernel/name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha, name)
            self.assertEqual(hashlib.blake2b(data).hexdigest(), b2, name)
        bundled = {p.name for p in kernel.iterdir()} - {'PKGBUILD.patch'}
        self.assertEqual(bundled, set(names), 'Every delta file is a listed patch')


if __name__ == '__main__':
    unittest.main()
