# SPDX-License-Identifier: GPL-2.0-only
"""Daemon ativado por socket systemd; código instalado root-owned, sem jogo root."""
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import time
import uuid

from .steam_protocol import encode, receive, peer, approved, ready, ProcInfo
from .input.physical_wheel import SOURCE
from .startup_errors import operation, io_error


class DeviceLock:
    def __init__(self, path):
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise RuntimeError('DFGT já reservado por outra sessão')
    def close(self):
        os.close(self.fd)


class RootRunner:
    def __init__(self, profile, *, physical, detailed, identity, runtime, log_dir, run_dir):
        self.directory = Path(run_dir)/('session-'+uuid.uuid4().hex)
        with operation("service.session.mkdir", self.directory):
            self.directory.mkdir(mode=0o700)
        config = self.directory/'profiles.json'
        try:
            with operation("service.profile.write", config):
                config.write_text(json.dumps(dict(version=1, profiles=dict(dirt5=approved(profile)))))
                config.chmod(0o600)
            with operation("service.runner.socketpair"):
                parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        except BaseException:
            config.unlink(missing_ok=True)
            self.directory.rmdir()
            raise
        self.channel = parent
        self.channel.setblocking(False)
        self.state = dict(state='starting')
        self.buffer = b''
        self.stopping_at = None
        self.signalled = False
        self.last_progress = None
        command = ['/usr/bin/python3', '-I', '-B', str(Path(runtime)/'src/dfgt_bridge/main.py'),
                   '--profiles-file', str(config), '--profile', 'dirt5', '--source', SOURCE,
                   '--control-stdin', '--operational-log', '--status-fd', str(child.fileno()),
                   '--game-uid', str(identity['uid']), '--game-after-ticks', str(identity['ticks'])]
        if physical:
            command.append('--enable-ffb')
        self.capture = None
        self.process = None
        try:
            if detailed:
                folder = Path(log_dir)/'captures'
                with operation('service.capture.mkdir', folder):
                    folder.mkdir(mode=0o750, exist_ok=True)
                self.capture = str(folder/(self.directory.name+'.jsonl.gz'))
                command.extend(['--capture', self.capture])
            with operation("service.runner.spawn", Path(runtime)/"src/dfgt_bridge/main.py"):
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, pass_fds=(child.fileno(),), start_new_session=True,
                    cwd=runtime, env={'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, bufsize=0)
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        except BaseException:
            if self.process is not None:
                self._signal(signal.SIGKILL)
                self.process.wait(timeout=2)
                self.process.stdin.close();self.process.stdout.close()
            parent.close()
            config.unlink();self.directory.rmdir()
            raise
        finally:
            child.close()

    def poll(self, now):
        if self.last_progress is None: self.last_progress = now
        # Limites de drenagem; nenhum cliente ou stdout pode monopolizar o lease guard.
        for _ in range(8):
            try:
                event = receive(self.channel)
            except (BlockingIOError, EOFError):
                break
            if event.get('state') in ('ready', 'failed'):
                self.state = event
            if event.get('state') in ('ready', 'heartbeat'):
                self.last_progress = now
        try:
            data = os.read(self.process.stdout.fileno(), 16384)
        except BlockingIOError:
            data = b''
        self.buffer = (self.buffer+data)[-16384:]
        if data:
            # Somente linhas operacionais: diagnósticos periódicos estão desligados.
            for line in data.decode(errors='replace').splitlines()[:12]:
                logging.info('runner: %s', line[:8192])
        if self.stopping_at is not None and self.process.poll() is None:
            elapsed = now-self.stopping_at
            if elapsed >= 1 and not self.signalled:
                self._signal(signal.SIGTERM);self.signalled = True
            if elapsed >= 2:
                self._signal(signal.SIGKILL)
        code = self.process.poll()
        if (code is None and self.stopping_at is None and self.state.get('state') == 'ready'
                and now-self.last_progress >= 1.5):
            self.state = dict(state='failed', error='Renderer sem progresso há 1,5 s; FFB deve parar')
        if code is not None and self.stopping_at is None:
            self.state = dict(state='failed', error=self.state.get('error') or
                              f'Runner encerrou ({code}): '+self.buffer.decode(errors='replace')[-700:])
        return code

    def _signal(self, number):
        try:
            os.killpg(self.process.pid, number)
        except ProcessLookupError:
            pass

    def stop(self, now):
        if self.stopping_at is not None:
            return
        self.stopping_at = now
        try:
            os.write(self.process.stdin.fileno(), b'ffb-stop\nquit\n')
        except (OSError, ValueError):
            pass
        self.process.stdin.close()

    def close(self):
        self._signal(signal.SIGKILL)  # Recolher eventual filho órfão após morte do renderer.
        self.channel.close()
        self.process.stdout.close()
        if not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=.1)
        (self.directory/'profiles.json').unlink()
        self.directory.rmdir()


class SessionManager:
    def __init__(self, uid, runner_factory, *, proc=None, lock_factory=None):
        if type(uid) is not int or uid <= 0:
            raise ValueError('Conta autorizada deve ser usuário sem privilégios')
        self.uid, self.factory = uid, runner_factory
        self.proc = proc or ProcInfo()
        self.lock_factory = lock_factory
        self.runner = self.lock = self.owner = self.identity = None
        self.started = self.heartbeat = 0
        self.physical = False
        self.state = dict(state='idle')

    def request(self, credentials, connection, message, now):
        if credentials['uid'] != self.uid or credentials['uid'] == 0:
            raise PermissionError('UID não autorizado')
        if type(message.get('v')) is not int or message['v'] != 1:
            raise ValueError('Versão de controle inválida')
        op = message.get('op')
        allowed = {'start': {'v', 'op', 'profile', 'mode', 'settings', 'detailed'},
                   'status': {'v', 'op'}, 'ping': {'v', 'op'}, 'stop': {'v', 'op'}}
        if op not in allowed or set(message) != allowed[op]:
            raise ValueError('Operação/campos não autorizados')
        if op == 'start':
            if self.runner is not None:
                raise RuntimeError('DFGT ocupado; aguarde o encerramento da sessão anterior')
            if message['profile'] != 'dirt5' or message['mode'] not in ('normal', 'passive') or type(message['detailed']) is not bool:
                raise ValueError('Somente perfil dirt5 e modos normal/passive são permitidos')
            settings = approved(message['settings'])
            identity = self.proc.get(credentials['pid'])
            if not identity or identity['uid'] != self.uid:
                raise PermissionError('Identidade do launcher inválida')
            identity = dict(identity)
            with operation("service.device_lock", "/run/dfgt-bridge-backend/device.lock"):
                lock = self.lock_factory() if self.lock_factory else None
            try:
                self.runner = self.factory(settings, physical=message['mode'] == 'normal',
                    detailed=message['detailed'], identity=identity)
            except BaseException:
                if lock: lock.close()
                raise
            self.lock, self.identity, self.owner = lock, identity, connection
            self.started = self.heartbeat = now
            self.physical = message['mode'] == 'normal'
            self.state = dict(state='starting')
            logging.info('Sessão dirt5 iniciada: uid=%d modo=%s', self.uid, message['mode'])
        elif op == 'ping':
            if self.owner != connection:
                raise PermissionError('Ping não pertence ao lease ativo')
            self.heartbeat = now
        elif op == 'stop':
            self.stop('requested_stop', now)
        return dict(ok=True, **self.state)

    def stop(self, reason, now):
        if self.runner is not None and self.state.get('state') != 'stopping':
            self.runner.stop(now)
            self.state = dict(state='stopping', reason=reason)
            logging.info('Sessão encerrando: %s', reason)

    def disconnected(self, connection, now):
        if self.owner == connection:
            self.stop('launcher_disconnected', now)

    def tick(self, now):
        if self.runner is None:
            return
        code = self.runner.poll(now)
        if code is not None:
            failed = self.state.get('state') != 'stopping'
            self.state = dict(self.runner.state) if failed else dict(state='idle', reason=self.state.get('reason'),
                                                                    error=self.state.get('error'))
            self.runner.close()
            if self.lock: self.lock.close()
            self.runner = self.lock = self.identity = None
            return
        if self.state.get('state') == 'stopping':
            return
        current = self.proc.get(self.identity['pid'])
        if (not current or (current['uid'], current['ticks']) != (self.uid, self.identity['ticks']) or
                now-self.heartbeat >= 3):
            self.stop('launcher_dead_or_lease_expired', now)
        elif now-self.started >= 40 and not ready(self.state, self.physical):
            self.stop('readiness_timeout', now)
            self.state['error'] = 'Timeout aguardando PIDFF/EVDEV/FFB reais'
        elif self.runner.state.get('state') == 'failed':
            reason = self.runner.state.get('error', 'Runner falhou')
            self.stop('runner_failed', now);self.state['error'] = reason
        elif ready(self.runner.state, self.physical):
            self.state = dict(self.runner.state, capture=getattr(self.runner, 'capture', None))


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if not address: return
    if address.startswith('@'): address = '\0'+address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.settimeout(.1)
        channel.sendto(message.encode(), address)


def serve(listener, manager, *, stopping=None, clock=time.monotonic, watchdog=None):
    stopping = stopping or (lambda: False)
    listener.setblocking(False)
    connections = {}
    last_watchdog = clock()
    with selectors.DefaultSelector() as selector:
        selector.register(listener, selectors.EVENT_READ)
        def drop(channel):
            manager.disconnected(channel.fileno(), clock())
            selector.unregister(channel);connections.pop(channel, None);channel.close()
        while not stopping():
            manager.tick(clock())
            if watchdog and clock()-last_watchdog >= 1:
                watchdog();last_watchdog = clock()
            for key, _ in selector.select(.02):
                if key.fileobj is listener:
                    channel, _ = listener.accept();channel.setblocking(False)
                    if len(connections) >= 16:
                        channel.close();continue
                    credentials = peer(channel)
                    if credentials['uid'] != manager.uid:
                        channel.close();continue
                    connections[channel] = credentials
                    selector.register(channel, selectors.EVENT_READ)
                else:
                    channel = key.fileobj
                    try:
                        request = receive(channel)
                        reply = manager.request(connections[channel], channel.fileno(), request, clock())
                    except EOFError:
                        drop(channel);continue
                    except (ValueError, KeyError, TypeError, PermissionError, RuntimeError, OSError, RecursionError) as exc:
                        detail = io_error(exc, "service.request") if isinstance(exc, OSError) and exc.filename else str(exc)
                        logging.error("Falha de controle: %s", detail[:1000])
                        reply = dict(ok=False, error=detail[:1000])
                    try:
                        channel.send(encode(reply))
                    except (OSError, ValueError):
                        drop(channel)
        manager.stop('service_shutdown', clock())
        deadline = clock()+3
        while manager.runner is not None and clock() < deadline:
            manager.tick(clock());time.sleep(.02)
        for channel in list(connections):
            channel.close()


def main():
    if os.geteuid() != 0:
        raise PermissionError('Daemon deve ser iniciado somente pelo systemd como root')
    policy_path = Path('/var/lib/dfgt-bridge/service.json')
    info = policy_path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('Política do serviço precisa ser root-owned e não gravável por usuários')
    policy = json.loads(policy_path.read_text())
    runtime = Path('/var/lib/dfgt-bridge/runtime')
    if policy.get('runtime') != str(runtime):
        raise PermissionError('Runtime do serviço não é o snapshot fixo instalado')
    for path in [runtime, *runtime.parents, *runtime.rglob('*')]:
        entry = path.lstat()
        if path.is_symlink() or entry.st_uid != 0 or entry.st_mode & 0o022:
            raise PermissionError('Código privilegiado não é root-owned/read-only: '+str(path))
    # Só restos privados do próprio serviço, depois do KillMode=control-group.
    for folder in Path('/run/dfgt-bridge-backend').glob('session-*'):
        if (folder.is_dir() and not folder.is_symlink() and folder.stat().st_uid == 0 and
                len(folder.name) == 40 and set(folder.name[8:]) <= set('0123456789abcdef') and
                {p.name for p in folder.iterdir()} <= {'profiles.json'}):
            config = folder/'profiles.json'
            if config.exists(): config.unlink()
            folder.rmdir()
    if int(os.environ.get('LISTEN_PID', '0')) != os.getpid() or os.environ.get('LISTEN_FDS') != '1':
        raise RuntimeError('Serviço exige o único socket passado pelo systemd')
    folder = Path('/var/log/dfgt-bridge');folder.mkdir(mode=0o750, exist_ok=True)
    handler = RotatingFileHandler(folder/'service.log', maxBytes=262144, backupCount=2)
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    factory = lambda profile, **args: RootRunner(profile, **args, runtime=policy['runtime'],
                        log_dir=folder, run_dir='/run/dfgt-bridge-backend')
    manager = SessionManager(policy['uid'], factory,
        lock_factory=lambda: DeviceLock('/run/dfgt-bridge-backend/device.lock'))
    stop = {'value': False}
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.update(value=True))
    listener = socket.socket(fileno=3)
    try:
        notify('READY=1')
        serve(listener, manager, stopping=lambda: stop['value'], watchdog=lambda: notify('WATCHDOG=1'))
    finally:
        listener.close()
