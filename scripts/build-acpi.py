#!/usr/bin/env python3
"""Derive ACPI overrides from the machine's original tables; no firmware distribution."""
import importlib.util
import json
from pathlib import Path
import hashlib
import shutil
import subprocess
repo = Path(__file__).resolve().parents[1]
archive = repo / 'acpi/acpica-unix-20260408.tar.gz'
if not hashlib.sha256(archive.read_bytes()).hexdigest() == 'e66ceb26d6d514ce164fe22f5a4f7ca165cc38349d7a97f41a21f19b364647a2':
    raise ValueError('Validierung fehlgeschlagen')
build = repo / 'build'
build.mkdir(exist_ok=True)
compiler = build / 'acpica-unix-20260408'
if not compiler.exists():
    subprocess.run(['tar', '-xzf', str(archive), '-C', str(build)], check=True)
subprocess.run(['make', '-C', str(compiler), '-j4', 'iasl'], check=True)
out = build / 'acpi'
provenance = out / 'provenance/hardware'
provenance.mkdir(parents=True, exist_ok=True)
for name in ['power-source.sed', 'power-method-changes.json', 'power-splice.json']:
    shutil.copy2(repo / 'acpi' / name, provenance / name)
spec = importlib.util.spec_from_file_location('acpi_builder', repo / 'acpi/acpi_builder.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = module.build(build / 'firmware', repo=out, iasl=str(compiler / 'generate/unix/bin/iasl'))
(out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
subprocess.run(['bsdtar', '-xf', str(out / module.ARCHIVE_PATH), '-C', str(out)], check=True)
if not hashlib.sha256((out / 'kernel/firmware/acpi/SSDT.aml').read_bytes()).hexdigest() == module.SSDT_OUTPUT:
    raise ValueError('Validierung fehlgeschlagen')
print('ACPI-Dateien:', out / 'kernel/firmware/acpi')
