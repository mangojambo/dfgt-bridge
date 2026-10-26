# SPDX-License-Identifier: GPL-2.0-only
"""Launcher Steam sem privilégios; preserva argv/env, controla somente seu lease."""
import argparse
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .config import ProfileStore, default_path
from .startup_errors import operation
from .steam_protocol import approved
from .steam_protocol import ControlClient, ProcInfo, APPID, ready


DFGT_INPUT = Path('/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick')
DFGT_SYSFS_INPUT = Path('/sys/class/input')
DFGT_MISSING_MESSAGE = 'DFGT not connected — launching game without bridge'


def dfgt_connected(path=DFGT_INPUT, *, sysfs=DFGT_SYSFS_INPUT, device_root='/dev/input'):
    """Read the stable input symlink and sysfs identity; never open the device node."""
    try:
        target = Path(path).resolve(strict=True)
    except FileNotFoundError:
        return False
    if target.parent != Path(device_root).resolve() or not re.fullmatch(r'event[0-9]+', target.name):
        return False
    identity = Path(sysfs) / target.name / 'device' / 'id'
    try:
        vendor = (identity / 'vendor').read_text(encoding='ascii').strip()
        product = (identity / 'product').read_text(encoding='ascii').strip()
    except FileNotFoundError:
        # A concurrent unplug can remove sysfs between symlink resolution and reads.
        return False
    return vendor.lower().removeprefix('0x') == '046d' and product.lower().removeprefix('0x') == 'c29a'


def _is_dfgt_disconnect_startup_error(exc):
    detail = str(exc)
    return ('EOFError: DFGT desconectado' in detail or
            (str(DFGT_INPUT) in detail and 'errno=2' in detail))


class IntegrationSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path().with_name('integration.json')
    def enabled(self):
        if not self.path.exists():
            return False
        if self.path.stat().st_size > 1024:
            raise ValueError('Preferência de integração excessiva')
        data = json.loads(self.path.read_text())
        if (not isinstance(data, dict) or set(data) != {'version', 'enabled'} or
                type(data['version']) is not int or data['version'] != 1 or type(data['enabled']) is not bool):
            raise ValueError('Preferência de integração inválida')
        return data['enabled']
    def save(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Preferência precisa ser booleana')
        import tempfile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=self.path.parent, delete=False) as stream:
            stream.write(json.dumps(dict(version=1, enabled=enabled))+'\n')
            temporary = stream.name
        os.replace(temporary, self.path)


class GameTree:
    """Identidades (pid,starttime), descendentes e processos Steam AppID da sessão."""
    def __init__(self, pid, uid, after_ticks, *, proc=None):
        self.proc = proc or ProcInfo()
        self.pid, self.uid, self.after = pid, uid, after_ticks
        self.known = {}
        self.game_seen = False
        self.discover = True
        self.excluded = set()
        ancestor = os.getpid()
        while ancestor not in self.excluded:
            self.excluded.add(ancestor)
            info = self.proc.get(ancestor)
            if not info or info['ppid'] <= 0: break
            ancestor = info['ppid']
    def poll(self):
        processes = {p['pid']: p for p in self.proc.all() if p['uid'] == self.uid and p['ticks'] >= self.after
                     and p['pid'] not in self.excluded}
        related = {pid for pid, p in processes.items() if self.known.get(pid) == p['ticks'] or
                   (self.discover and (pid == self.pid or self.proc.app(pid)))}
        for _ in range(len(processes)+1 if self.discover else 0):
            added = {pid for pid, p in processes.items() if p['ppid'] in related}-related
            if not added: break
            related.update(added)
        self.known = {pid: processes[pid]['ticks'] for pid in related}
        game = {pid for pid in related if processes[pid]['comm'].casefold() in ('dirt5.exe', 'dirt5')}
        self.game_seen |= bool(game)
        return [processes[pid] for pid in related], bool(game)
    def terminate(self, number=signal.SIGTERM):
        # Nunca sinalizar PID reutilizado ou processos de outro usuário.
        for pid, ticks in list(self.known.items()):
            p = self.proc.get(pid)
            if p and p['uid'] == self.uid and p['ticks'] == ticks:
                try: os.kill(pid, number)
                except ProcessLookupError: pass


def run_game(command, *, settings, client_factory=ControlClient, spawn=subprocess.Popen,
             proc=None, clock=time.monotonic, sleep=time.sleep, cancelled=None,
             passive=False, detailed=False, startup_timeout=180, automatic=True):
    if os.geteuid() == 0:
        raise PermissionError('Steam/Proton/jogo nunca podem ser executados como root')
    cancelled = cancelled or (lambda: False)
    client = process = tree = None
    stopped = acquired = False
    proc = proc or ProcInfo()
    identity = proc.get(os.getpid())
    if identity is None:
        raise RuntimeError('Não foi possível identificar o launcher em /proc')
    try:
        if automatic:
            with operation("launcher.control.connect", "/run/dfgt-bridge/control.sock"):
                client = client_factory()
            response = client.request('start', profile='dirt5', mode='passive' if passive else 'normal',
                                      settings=settings, detailed=detailed)
            acquired = True
        deadline = clock()+70
        while client is not None and not ready(response, not passive):
            if cancelled(): raise InterruptedError('Inicialização cancelada')
            if response.get('state') in ('failed', 'stopping', 'idle'):
                raise RuntimeError(response.get('error') or response.get('reason') or 'Bridge não ficou pronto')
            if clock() >= deadline: raise TimeoutError('Timeout aguardando PIDFF/EVDEV/FFB')
            sleep(.25)
            response = client.request('ping')
        if client:
            permission_deadline = clock()+3
            while not any(os.access(path, os.R_OK | os.W_OK) for path in response['binding']['evdev']):
                if cancelled(): raise InterruptedError('Inicialização cancelada')
                if clock() >= permission_deadline:
                    raise PermissionError('Usuário sem leitura/escrita no EVDEV virtual; conferir permissões udev/logind')
                sleep(.1)
                response = client.request('ping')
                if not ready(response, not passive):
                    raise RuntimeError('Bridge perdeu prontidão durante autorização EVDEV')
            logging.info('Bridge [PRONTO]: %s', response.get('binding'))
        else:
            logging.info('Jogo iniciado sem DFGT Bridge')
        if not command:  # Teste de prontidão passivo, sem iniciar jogo/motor.
            print('[PRONTO] teste passivo, sem motor: '+json.dumps(response.get('binding'), sort_keys=True))
            return 0
        env = dict(os.environ)
        if automatic:
            env['PROTON_ENABLE_HIDRAW'] = '0x046d/0xc24f'
        with operation("launcher.game.spawn"):
            process = spawn(list(command), env=env, start_new_session=True)
        tree = GameTree(process.pid, os.getuid(), identity['ticks'], proc=proc)
        launched = last_ping = clock()
        game_ended = idle_since = None
        while True:
            now = clock()
            if cancelled(): raise InterruptedError('Steam cancelou a sessão')
            if client and not stopped and now-last_ping >= .5:
                response = client.request('ping');last_ping = now
                if not ready(response, not passive):
                    raise RuntimeError(response.get('error') or response.get('reason') or 'Bridge perdeu prontidão')
            related, game = tree.poll()
            code = process.poll()
            if tree.game_seen and not game:
                if game_ended is None:
                    game_ended = now
                    tree.discover = False
                    if client: client.request('stop')
                    stopped = True  # Motor zero antes de esperar Proton.
                if now-game_ended >= 15:
                    tree.terminate();break
            if stopped:
                last_ping = now  # Lease já encerrando: não tratá-lo como falha de startup.
            if code is not None and not related:
                idle_since = idle_since if idle_since is not None else now
                if now-idle_since >= 1 and (tree.game_seen or code != 0 or not automatic):
                    break
            else:
                idle_since = None
            if not tree.game_seen and now-launched >= startup_timeout:
                raise TimeoutError('DiRT5.exe não apareceu no prazo de inicialização')
            sleep(.1)
        try:
            return process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            tree.terminate(signal.SIGKILL)
            return 1
    except Exception as exc:
        # Preserve the original exception type. The entry point uses this marker
        # only for a narrowly classified physical-device startup race.
        if automatic and process is None:
            try:
                exc.bridge_startup_failure = True
            except (AttributeError, TypeError):
                pass
        raise
    finally:
        if client:
            if acquired and not stopped:
                try: client.request('stop')
                except (OSError, EOFError, RuntimeError): pass
            client.close()  # EOF é outra barreira de stop, inclusive SIGKILL do launcher.
        if process is not None:
            if tree:
                tree.discover = False
                tree.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if tree: tree.terminate(signal.SIGKILL)
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
            if tree:
                deadline = clock()+2
                while tree.poll()[0] and clock() < deadline:
                    sleep(.05)
                tree.terminate(signal.SIGKILL)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles-file')
    parser.add_argument('--check-ready', action='store_true', help='UHID/PIDFF passivo; nenhum backend físico')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--capture', action='store_true', help='Captura detalhada explícita no diretório do serviço')
    parser.add_argument('--game-start-timeout', type=float, default=180, help='Prazo de aparecimento do jogo, não duração da sessão')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not math.isfinite(args.game_start_timeout) or args.game_start_timeout <= 0:
        parser.error('--game-start-timeout deve ser finito e positivo')
    if os.geteuid() == 0:
        parser.error('Execute como deck, nunca sudo/root')
    state_dir = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'dfgt-bridge'
    state_dir.mkdir(parents=True, exist_ok=True)
    log_handler = RotatingFileHandler(state_dir/'steam.log', maxBytes=131072, backupCount=1)
    logging.basicConfig(level=logging.INFO, handlers=[log_handler], format='%(asctime)s %(levelname)s %(message)s')
    cancel = {'value': False}
    old = {}
    try:
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old[number] = signal.signal(number, lambda *_: cancel.update(value=True))
        if args.status or args.stop:
            client = ControlClient()
            try: print(json.dumps(client.request('stop' if args.stop else 'status')))
            finally: client.close()
            return 0
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command and not args.check_ready: parser.error('Falta %command% após --')
        if args.check_ready and command: parser.error('--check-ready não aceita comando de jogo')
        if command and not args.check_ready and not dfgt_connected():
            logging.info(DFGT_MISSING_MESSAGE)
            return run_game(command, settings=None, cancelled=lambda: cancel['value'], automatic=False,
                            startup_timeout=args.game_start_timeout)
        preferences = IntegrationSettings(Path(args.profiles_file).with_name('integration.json') if args.profiles_file else None)
        with operation('launcher.integration.read', preferences.path):
            enabled = args.check_ready or preferences.enabled()
        store = ProfileStore(args.profiles_file)
        with operation('launcher.profile.read', store.path):
            settings = store.load('dirt5')
        if enabled:
            settings = approved(settings)
        try:
            return run_game(command, settings=settings,
                            passive=args.check_ready, detailed=args.capture, cancelled=lambda: cancel['value'], automatic=enabled,
                            startup_timeout=args.game_start_timeout)
        except (OSError, EOFError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            if (not args.check_ready and getattr(exc, 'bridge_startup_failure', False) and
                    _is_dfgt_disconnect_startup_error(exc) and not dfgt_connected()):
                logging.info(DFGT_MISSING_MESSAGE)
                return run_game(command, settings=None, cancelled=lambda: cancel['value'], automatic=False,
                                startup_timeout=args.game_start_timeout)
            raise
    except (OSError, EOFError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        if cancel['value']:
            logging.info('Sessão cancelada pela Steam/sinal')
            return 130
        logging.error('%s', exc)
        print('DFGT Bridge: '+str(exc)+'. Consulte '+str(state_dir/'steam.log'), file=sys.stderr)
        # Notificação opcional: Gaming Mode não depende de terminal nem deste diálogo.
        if Path('/usr/bin/notify-send').is_file():
            try:
                subprocess.run(['/usr/bin/notify-send', '--app-name=DFGT Bridge', '--urgency=critical',
                    '--', 'DFGT Bridge não iniciou', str(exc)[:600]], timeout=2,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return 1
    finally:
        for number, handler in old.items(): signal.signal(number, handler)
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
