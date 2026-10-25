# SPDX-License-Identifier: GPL-2.0-only
"""Instalação pontual em /etc e /var; nunca escreve /usr ou desativa readonly."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import uuid

from .config import ProfileStore
from .steam_launcher import IntegrationSettings
from .steam_protocol import approved
from .steam_service import DeviceLock

MARKER = '# DFGT_BRIDGE_MANAGED_V1'
STATE = '/var/lib/dfgt-bridge'
RUNTIME = STATE+'/runtime'
UNITS = ('dfgt-bridge.socket', 'dfgt-bridge.service', 'dfgt-bridge-modules.service')


def units(uid, gid):
    return {
    UNITS[0]: f'''{MARKER}
[Unit]
Description=DFGT Bridge restricted local control socket
[Socket]
ListenSequentialPacket=/run/dfgt-bridge/control.sock
SocketUser={uid}
SocketGroup={gid}
SocketMode=0600
DirectoryMode=0755
Accept=no
Backlog=16
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
''',
    UNITS[1]: f'''{MARKER}
[Unit]
Description=DFGT Bridge privileged hardware backend
Requires=dfgt-bridge.socket dfgt-bridge-modules.service
After=dfgt-bridge.socket dfgt-bridge-modules.service
[Service]
Type=notify
NotifyAccess=main
ExecStart=/usr/bin/python3 -I -B {RUNTIME}/src/dfgt_bridge/steam_main.py --daemon
User=root
Group={gid}
WorkingDirectory={RUNTIME}
Environment=PATH=/usr/bin:/usr/sbin:/bin:/sbin
UMask=0027
RuntimeDirectory=dfgt-bridge
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
LogsDirectory=dfgt-bridge
LogsDirectoryMode=0750
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadOnlyPaths=/sys
ReadWritePaths=/sys/bus/hid/drivers
RestrictAddressFamilies=AF_UNIX
DevicePolicy=closed
DeviceAllow=/dev/uhid rw
DeviceAllow=char-input rw
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
RestrictSUIDSGID=yes
LockPersonality=yes
LimitCORE=0
TasksMax=32
KillMode=control-group
TimeoutStartSec=25
TimeoutStopSec=4
WatchdogSec=5
Restart=on-failure
RestartSec=1
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30
LogRateLimitBurst=20
''',
    UNITS[2]: f'''{MARKER}
[Unit]
Description=Load only existing UHID and PIDFF modules for DFGT Bridge
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/modprobe uhid
ExecStart=/usr/bin/modprobe hid-universal-pidff
TimeoutStartSec=15
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=CAP_SYS_MODULE
LimitCORE=0
'''}


def as_user(account, function):
    if os.geteuid() == account.pw_uid:
        return function()
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read)
        try:
            os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
            os.environ.update(HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name)
            for key in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME'):
                os.environ.pop(key, None)
            function()
            os._exit(0)
        except BaseException as exc:
            os.write(write, str(exc).encode()[:2000]);os._exit(1)
    os.close(write)
    message = os.read(read, 2000);os.close(read)
    _, status = os.waitpid(child, 0)
    if status:
        raise RuntimeError('Configuração do usuário: '+message.decode(errors='replace'))


class Installer:
    def __init__(self, account, source, *, root='/', command=None, user_action=None, chown=os.chown):
        if account.pw_uid <= 0 or not re.fullmatch(r'[a-z_][a-z0-9_-]*', account.pw_name):
            raise ValueError('Instalação exige conta normal válida, nunca root')
        self.account, self.source, self.root = account, Path(source), Path(root)
        self.command = command or (lambda args: subprocess.run(['/usr/bin/systemctl', *args], check=True, timeout=25))
        self.user_action = user_action or (lambda function: as_user(account, function))
        self.chown = chown

    def path(self, absolute):
        return self.root/str(absolute).lstrip('/')

    @property
    def wrapper(self):
        return self.path(Path(self.account.pw_dir)/'.local/bin/dfgt-steam-launch')

    @property
    def profiles(self):
        return self.path(Path(self.account.pw_dir)/'.config/dfgt-bridge/profiles.json')

    def _managed(self, path):
        if path.is_symlink() or (path.exists() and MARKER not in path.read_text()):
            raise ValueError(f'Arquivo existente não gerenciado, preservado: {path}')

    def _root_write(self, path, text):
        self._trusted(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o644);self.chown(path, 0, 0)

    def _trusted(self, path):
        # Nunca seguir symlink ou diretório gravável por usuário para uma escrita root.
        cursor = self.root
        expected_uid = 0 if self.root == Path('/') else os.geteuid()
        for part in Path(path).relative_to(self.root).parts:
            cursor = cursor/part
            if cursor.is_symlink(): raise ValueError('Destino privilegiado é symlink: '+str(cursor))
            if cursor.exists():
                info = cursor.stat()
                if info.st_uid != expected_uid or info.st_mode & 0o022:
                    raise ValueError('Destino privilegiado não é protegido: '+str(cursor))

    def _not_busy(self):
        lock = self.path('/run/dfgt-bridge/device.lock')
        self._trusted(lock)
        if lock.exists():
            return DeviceLock(lock)

    def _snapshot(self, package, files, state):
        stage = state/('.runtime-'+uuid.uuid4().hex)
        backup = state/('.previous-'+uuid.uuid4().hex)
        runtime = self.path(RUNTIME)
        try:
            for p in files:
                target = stage/'src/dfgt_bridge'/p.relative_to(package)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, target);target.chmod(0o644);self.chown(target, 0, 0)
            for folder in [stage, *stage.rglob('*')]:
                if folder.is_dir(): folder.chmod(0o755);self.chown(folder, 0, 0)
            if runtime.exists(): os.replace(runtime, backup)
            os.replace(stage, runtime)
        except BaseException:
            if backup.exists() and not runtime.exists(): os.replace(backup, runtime)
            raise
        finally:
            if stage.exists(): shutil.rmtree(stage)
        if backup.exists(): shutil.rmtree(backup)  # Somente snapshot próprio, já validado como protegido.

    def install(self):
        unit_dir = self.path('/etc/systemd/system')
        self._trusted(unit_dir);self._trusted(self.path(STATE))
        runtime = self.path(RUNTIME)
        self._trusted(runtime)
        if runtime.exists():
            for p in runtime.rglob('*'): self._trusted(p)
        was_installed = (self.path(STATE)/'install.json').is_file()
        for name in UNITS: self._managed(unit_dir/name)
        self.user_action(lambda: self._managed(self.wrapper))
        # Perfis/arquivos no home são lidos/escritos somente depois de abandonar root.
        def user_files():
            store = ProfileStore(self.profiles)
            profile = approved(store.load('dirt5'))
            store.save('dirt5', profile)
            self.wrapper.parent.mkdir(parents=True, exist_ok=True)
            self.wrapper.write_text(f'#!/bin/sh\n{MARKER}\nexec /usr/bin/python3 -I -B {RUNTIME}/src/dfgt_bridge/steam_main.py "$@"\n')
            self.wrapper.chmod(0o755)
            preferences = IntegrationSettings(store.path.with_name('integration.json'))
            if not was_installed or not preferences.path.exists():
                preferences.save(True)
            else:
                preferences.enabled()  # Validar, preservando a escolha de desativar na GUI.
        # Validar o perfil sem modificar nada antes de parar o serviço instalado.
        self.user_action(lambda: approved(ProfileStore(self.profiles).load('dirt5')))
        package = self.source/'src/dfgt_bridge'
        files = [p for p in package.rglob('*') if p.is_file() and p.suffix in ('.py', '.svg', '.md') and '__pycache__' not in p.parts]
        if not files or any(p.is_symlink() for p in package.rglob('*')):
            raise ValueError('Snapshot de código ausente ou contém symlinks')
        guard = self._not_busy()
        try:
            if any((unit_dir/name).exists() for name in UNITS):
                self.command(['stop', UNITS[0], UNITS[1]])
        finally:
            if guard: guard.close()
        state = self.path(STATE);state.mkdir(parents=True, exist_ok=True)
        state.chmod(0o755);self.chown(state, 0, 0)
        # Somente arquivos do pacote; não copiar .git, home, capturas ou scripts de teste.
        self._snapshot(package, files, state)
        self._root_write(state/'service.json', json.dumps(dict(uid=self.account.pw_uid, runtime=RUNTIME))+'\n')
        for name, content in units(self.account.pw_uid, self.account.pw_gid).items():
            self._root_write(unit_dir/name, content)
        self.user_action(user_files)
        self._root_write(state/'install.json', json.dumps(dict(version=1, user=self.account.pw_name,
            uid=self.account.pw_uid, home=self.account.pw_dir, units=list(UNITS)))+'\n')
        self.command(['daemon-reload']);self.command(['enable', '--now', UNITS[0]])
        return f'PROTON_ENABLE_HIDRAW=0x046d/0xc24f "{Path(self.account.pw_dir)/".local/bin/dfgt-steam-launch"}" -- %command%'

    def uninstall(self):
        self._trusted(self.path('/etc/systemd/system'));self._trusted(self.path(STATE))
        for name in UNITS: self._managed(self.path('/etc/systemd/system')/name)
        self.user_action(lambda: self._managed(self.wrapper))
        guard = self._not_busy()
        installed = any(self.path('/etc/systemd/system').joinpath(name).exists() for name in UNITS)
        try:
            if installed:
                self.command(['disable', '--now', UNITS[0]])
                self.command(['stop', UNITS[1], UNITS[2]])
        finally:
            if guard: guard.close()
        for name in UNITS:
            path = self.path('/etc/systemd/system')/name
            if path.exists(): path.unlink()
        if installed: self.command(['daemon-reload'])
        def remove_wrapper():
            if self.wrapper.exists(): self.wrapper.unlink()
            IntegrationSettings(self.profiles.with_name('integration.json')).save(False)
        self.user_action(remove_wrapper)
        runtime = self.path(RUNTIME)
        self._trusted(runtime)
        if runtime.exists():
            for p in runtime.rglob('*'): self._trusted(p)
            shutil.rmtree(runtime)
        for name in ('service.json', 'install.json'):
            p = self.path(STATE)/name
            if p.exists(): p.unlink()
        # Perfis, capturas, logs, módulos e configurações Steam são preservados.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--user')
    parser.add_argument('--test-ready', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    name = args.user or os.environ.get('SUDO_USER')
    if not name or name == 'root': name = 'deck'
    try:
        account = pwd.getpwnam(name)
        installer = Installer(account, Path(__file__).resolve().parents[2])
        if args.dry_run:
            print(json.dumps(dict(user=name, uid=account.pw_uid, runtime=RUNTIME, units=units(account.pw_uid, account.pw_gid)), indent=2))
            return 0
        if os.geteuid() != 0: raise PermissionError('Execute install.sh/uninstall.sh com sudo uma única vez')
        for binary in ('/usr/bin/systemctl', '/usr/bin/modprobe', '/usr/bin/modinfo', '/usr/bin/python3'):
            if not Path(binary).is_file(): raise RuntimeError('Dependência ausente: '+binary)
        if not args.uninstall:
            for module in ('uhid', 'hid-universal-pidff', 'hid-logitech'):
                subprocess.run(['/usr/bin/modinfo', module], check=True, stdout=subprocess.DEVNULL, timeout=5)
            option = installer.install()
            print('Instalação concluída. Launch Options do DiRT 5:\n'+option)
            print('Teste sem motor: ~/.local/bin/dfgt-steam-launch --check-ready')
            if args.test_ready:
                as_user(account, lambda: subprocess.run(['/usr/bin/python3', '-I', '-B',
                    RUNTIME+'/src/dfgt_bridge/steam_main.py', '--check-ready'], check=True, timeout=90))
        else:
            installer.uninstall();print('Serviços removidos; perfis/capturas/logs preservados. Retire a Launch Option na Steam.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Instalação: '+str(exc), file=sys.stderr);return 1
