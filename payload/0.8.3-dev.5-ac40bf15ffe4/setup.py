"""Prepare the plugin's private Python runtime; optionally migrate existing data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('DSH_HOME') or Path.home()/'.dsh')/'plugin-data/ask-data')
    parser.add_argument('--import-home', type=Path)
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--wheelhouse', type=Path, help='离线 wheel 目录；仍校验锁文件中的哈希')
    args = parser.parse_args()
    if sys.version_info[:2] not in {(3,11),(3,12)}:
        raise SystemExit('本包支持 Python 3.11 或 3.12。')
    home = args.home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    if args.import_home:
        source = args.import_home.resolve()
        if source == home or not (source/'sources.json').is_file():
            raise SystemExit('迁移来源必须是包含 sources.json 的其他数据目录。')
        if any(p.name != 'runtime' for p in home.iterdir()):
            raise SystemExit('目标已含数据，拒绝覆盖。请使用空目录迁移。')
        for item in source.rglob('*'):
            if item.is_symlink():
                raise SystemExit('迁移来源包含符号链接，拒绝复制。')
        for item in source.iterdir():
            target = home/item.name
            if item.name in {'runtime', 'answer-quality'} or item.name.endswith(('-wal', '-shm')): continue
            if item.is_dir(): shutil.copytree(item, target)
            elif item.is_file():
                if item.suffix in {'.sqlite', '.db'}:
                    with sqlite3.connect(item.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst: src.backup(dst)
                else: shutil.copy2(item, target)
        for item in home.rglob('*'):
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
    venv = home/'runtime/venv'
    python = venv/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    check_command = [str(python), str(ROOT/'check_runtime.py')]
    check = subprocess.run(check_command, capture_output=True)
    stamp = home/'runtime/requirements.txt'
    requirements = (ROOT/'requirements.txt').read_text()
    if check.returncode or not stamp.exists() or stamp.read_text() != requirements:
        command = [str(python), '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                   '--require-hashes', '--only-binary=:all:', '--no-deps',
                   '--cache-dir', str(home/'runtime/pip-cache'), '-r', str(ROOT/'requirements.txt')]
        if args.wheelhouse: command += ['--no-index','--find-links',str(args.wheelhouse.resolve())]
        subprocess.run(command, check=True)
        subprocess.run(check_command, check=True)
        stamp.write_text(requirements)
    if not (home/'sources.json').exists():
        (home/'sources.json').write_text(json.dumps({'sources': [], 'projects': {}}, ensure_ascii=False))
        os.chmod(home/'sources.json', 0o600)
    if not args.quiet: print('问数插件环境已准备：'+str(home))

if __name__ == '__main__': main()
