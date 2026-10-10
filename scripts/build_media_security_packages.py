#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


INPUTS = Path(__file__).resolve().parent / 'media-security'
WORK = Path('/work/media-security')
OUTPUT = Path('/out')


def run(command, cwd=None, log=None, env=None):
    print(' '.join(map(str, command)), flush=True)
    if log is None:
        subprocess.run(command, cwd=cwd, env=env, check=True)
        return
    with log.open('w') as handle:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        with log.open(errors='replace') as handle:
            shutil.copyfileobj(handle, sys.stdout)
        sys.stdout.flush()
        result.check_returncode()


def fetch(entry, directory, inputs):
    if 'path' in entry:
        data = (inputs / entry['path']).read_bytes()
    else:
        with urllib.request.urlopen(entry['url'], timeout=60) as response:
            data = response.read()
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError(f"Checksum mismatch: {entry['file']}")
    target = directory / entry['file']
    target.write_bytes(data)
    return target


def main(inputs, prepare_only=False):
    manifest = json.loads((inputs / 'manifest.json').read_text())
    provenance = OUTPUT / 'provenance'
    for directory in (WORK, OUTPUT / 'packages', provenance / 'sources', provenance / 'logs'):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(inputs / 'manifest.json', provenance / 'manifest.json')
    shutil.copy2(Path(__file__), provenance / 'build_media_security_packages.py')
    env = os.environ | {
        'QUILT_PATCHES': 'debian/patches',
        'DEBFULLNAME': 'MinusPod contributors',
        'DEBEMAIL': 'noreply@users.noreply.github.com',
        'SOURCE_DATE_EPOCH': str(manifest['source_date_epoch']),
    }
    packages = []
    for package in manifest['packages']:
        print(f"Building {package['name']} {package['version']}", flush=True)
        parent = WORK / package['name']
        files = parent / 'inputs'
        files.mkdir(parents=True)
        downloaded = {entry['file']: fetch(entry, files, inputs) for entry in package['source_files']}
        source = parent / 'source'
        if 'upstream_archive' in package:
            source.mkdir()
            run(['tar', '-xf', downloaded[package['upstream_archive']], '--strip-components=1', '-C', source])
            if 'debian_diff' in package:
                diff = files / 'debian.diff'
                data = gzip.decompress(downloaded[package['debian_diff']].read_bytes()).decode()
                if package['name'] == 'pcre2':
                    version = package['ubuntu_packaging_version'].split('-')[0]
                    data, config_patch = data.split(f'--- pcre2-{version}.orig/pcre2-config.in\n')
                    config = (source / 'pcre2-config.in').read_text()
                    if '\n--- ' in config_patch or '/usr/lib/*-gnu*)' not in config:
                        raise ValueError('PCRE2 packaging config adaptation needs review')
                diff.write_text(data)
                run(['patch', '-p1', '--batch', '--input', diff], cwd=source)
            else:
                run(['tar', '-xf', downloaded[package['debian_archive']], '-C', source])
        else:
            run(['dpkg-source', '--no-check', '-x', downloaded[package['dsc']], source])
        if package['name'] == 'pcre2':
            rules = source / 'debian/rules'
            marker = '--disable-pcre2grep-callout'
            if rules.read_text().count(marker) != 1 or '--disable-symvers' not in (source / 'm4/pcre2_check_vscript.m4').read_text():
                raise ValueError('PCRE2 symbol compatibility adaptation needs review')
            rules.write_text(rules.read_text().replace(marker, marker + ' --disable-symvers'))
        if package['name'] == 'srt':
            cmake = (source / 'CMakeLists.txt').read_text()
            if 'cmake_minimum_required (VERSION 3.5 FATAL_ERROR)' not in cmake:
                raise ValueError('SRT CMake compatibility fix is missing')
            series = source / 'debian/patches/series'
            series.write_text(series.read_text().replace('FTBFS_with_CMake_4.patch\n', ''))
            rules = source / 'debian/rules'
            rules.write_text(rules.read_text().replace('ENABLE_UINTTESTS', 'ENABLE_UNITTESTS'))
        if 'upstream_archive' in package and (source / 'debian/patches/series').exists():
            run(['quilt', 'push', '-a'], cwd=source, env=env)
        for entry in package['patches']:
            patch = fetch(entry, files, inputs)
            run(['quilt', 'import', '-P', entry['file'], patch], cwd=source, env=env)
            run(['quilt', 'push'], cwd=source, env=env)

        run(['dch', '--newversion', package['version'], '--distribution', 'resolute', package['summary']], cwd=source, env=env)
        run(['tar', '-cJf', provenance / 'sources' / f"{package['name']}.tar.xz", '-C', source, '.'])
        if prepare_only:
            continue
        package_env = env | {'DEB_BUILD_PROFILES': ' '.join(package.get('build_profiles', []))}
        build_flag = {'native': '-B', 'binary': '-b'}[package.get('build_mode', 'native')]
        run(['dpkg-buildpackage', build_flag, '-uc', '-us', '-jauto'], cwd=source, env=package_env,
            log=provenance / 'logs' / f"{package['name']}.log")
        expected = set(package['runtime_packages'])
        found = set()
        for archive in parent.glob('*.deb'):
            name, version, arch = subprocess.check_output(
                ['dpkg-deb', '--show', '--showformat=${Package}\n${Version}\n${Architecture}', archive], text=True,
            ).splitlines()
            if name not in expected:
                continue
            found.add(name)
            shutil.copy2(archive, OUTPUT / 'packages' / archive.name)
            packages.append({'name': name, 'version': version, 'architecture': arch,
                             'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        if found != expected:
            raise ValueError(f"Missing runtime packages: {sorted(expected - found)}")
        shutil.rmtree(parent)
    if not prepare_only:
        (provenance / 'built-packages.json').write_text(json.dumps(packages, indent=2) + '\n')


def verify_installed():
    packages = json.loads((Path(__file__).parent / 'built-packages.json').read_text())
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    for package in packages:
        version, arch = subprocess.check_output(
            ['dpkg-query', '--show', '--showformat=${Version}\n${Architecture}', package['name']], text=True,
        ).splitlines()
        if (version, arch) != (package['version'], package['architecture']) or arch not in (architecture, 'all'):
            raise ValueError(f"Installed security package differs from build: {package['name']}")
    print(f'Verified {len(packages)} security packages for {architecture}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, default=INPUTS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify-installed', action='store_true')
    mode.add_argument('--prepare-only', action='store_true')
    options = parser.parse_args()
    if options.verify_installed:
        verify_installed()
    else:
        main(options.input_dir, prepare_only=options.prepare_only)
