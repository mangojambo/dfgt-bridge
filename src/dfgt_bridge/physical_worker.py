# SPDX-License-Identifier: GPL-2.0-only
"""Processo EVDEV separado, IPC com um frame pendente e último valor local.

Nada é iniciado/importado pelo caminho passivo. Mailboxes têm um escritor,
seqlock sem espera (SteamOS x86_64); nenhum mutex pode prender o watchdog.
"""
import json
import multiprocessing
import os
import signal
import socket
import time
import math
from contextlib import ExitStack

from .capture import CaptureWriter, canonical, implementation_hash
from .supervised import SupervisedPolicy
from .ffb.safety import validate_percent

MAX_PACKET = 4096


class Mailbox:
    def __init__(self, ctx):
        self.sequence = ctx.RawValue('Q', 0)
        self.size = ctx.RawValue('I', 0)
        self.data = ctx.RawArray('B', MAX_PACKET)

    def write(self, value):
        encoded = canonical(value).encode()
        if len(encoded) > MAX_PACKET:
            raise ValueError('Mailbox físico excedeu limite')
        self.sequence.value += 1
        self.data[:len(encoded)] = encoded
        self.size.value = len(encoded)
        self.sequence.value += 1

    def read(self):
        sequence, size = self.sequence.value, self.size.value
        if sequence & 1 or size == 0 or size > MAX_PACKET:
            return None
        raw = bytes(self.data[:size])
        if self.sequence.value != sequence:
            return None
        return json.loads(raw)


def worker(channel, status, commands, stop_epoch, emergency_epoch, quit_flag, path, config, keyboard_path, trace_path, game_pid=None):
    # Imports físicos somente no processo explicitamente autorizado.
    from .backends.evdev import EvdevBackend
    from .input.global_keys import GlobalKeys
    from .game_monitor import GameMonitor
    writer = backend = keys = policy = None
    signals = {'stop': False, 'quit': False}
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: signals.update(quit=True))
    signal.signal(signal.SIGUSR1, lambda *_: signals.update(stop=True))
    channel.setblocking(False)
    error = None
    try:
        if trace_path:
            writer = CaptureWriter(trace_path, dict(kind='physical_policy', config=config,
                                   implementation_sha256=implementation_hash(), physical_id=[0x046d, 0xc29a]))
        backend = EvdevBackend(path, enable_ffb=True)
        policy = SupervisedPolicy(backend, **config)
        game = GameMonitor(game_pid)
        last_game = None
        keys = GlobalKeys(enable_ffb=True, path=keyboard_path)
        if not keys.devices:
            print('[FFB] Teclas globais INDISPONÍVEIS. Terminal: ffb-stop; '
                  f'outro terminal: kill -USR1 {os.getpid()}', flush=True)
        else:
            print('[FFB] Teclas globais F7/F8: -/+5%; F9: desligar (não alterna); '
                  'F10: diagnóstico. Reativar somente por ffb-arm no terminal.', flush=True)
        print(f'[FFB] Supervisor PID={os.getpid()}; teto inicial={config["strength"]}%, '
              f'máximo={config["maximum"]}%.', flush=True)

        def apply(action, data, now=None):
            stamp = time.monotonic_ns() if now is None else now
            value = policy.apply(action, data, stamp)
            if writer:
                writer.append(dict(kind='physical_step', action=action, data=data,
                                   timestamp_ns=stamp, result=value,
                                   io={k: getattr(backend, k, 0) for k in ('io_calls', 'last_io_ns', 'max_io_ns')}))
            return value

        def guard():
            if policy.last_valid_ns is None or time.monotonic_ns()-policy.last_valid_ns >= 100_000_000:
                raise TimeoutError('Cálculo vencido antes da operação EVDEV')
            if policy.require_game and (game.identity is None or game.identity_for(game.identity[0]) != game.identity):
                raise TimeoutError('Jogo saiu antes da operação EVDEV')
        backend.freshness_guard = guard
        last_stop = last_emergency = 0
        last_command = 0
        last_flush = time.monotonic_ns()
        channel.send(b'ack')
        while not quit_flag.value and not signals['quit']:
            if policy.require_game:
                identity = game.poll(time.monotonic_ns())
                if identity != last_game:
                    apply('game', dict(running=identity is not None, identity=identity))
                    last_game = identity
            if signals['stop']:
                signals['stop'] = False
                apply('stop', dict(reason='terminal_SIGUSR1', latch=True))
                print('[FFB] Emergência do terminal: desligado. Reativar por ffb-arm.', flush=True)
            if stop_epoch.value != last_stop:
                last_stop = stop_epoch.value
                apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=last_stop))
            if emergency_epoch.value != last_emergency:
                last_emergency = emergency_epoch.value
                apply('stop', dict(reason='terminal_emergency', latch=True))
            for key in keys.read():
                if key == 67:
                    apply('stop', dict(reason='F9_or_keyboard_loss', latch=True))
                    print('[FFB] F9: DESLIGADO; reativação exige ffb-arm no terminal.', flush=True)
                elif key in (65, 66):
                    amount = policy.output.strength_percent + (5 if key == 66 else -5)
                    apply('strength', dict(percent=max(0, min(policy.output.max_strength_percent, amount))))
                    print(f'[FFB] Limite={policy.output.strength_percent}%, desligado={policy.latched}', flush=True)
                elif key == 68:
                    print('[FFB F10] ' + canonical(dict(policy.snapshot(),
                          io={k: getattr(backend, k, 0) for k in ('io_calls', 'last_io_ns', 'max_io_ns')})), flush=True)
            command = commands.read()
            if command and command['nonce'] > last_command:
                last_command = command['nonce']
                apply(command['action'], command['data'])
                print('[FFB terminal] ' + canonical(policy.snapshot()), flush=True)
            # Uma mensagem inteira por recv, sem bloquear se o pai parar no send.
            try:
                packet = channel.recv(MAX_PACKET+1)
            except BlockingIOError:
                packet = None
            if packet == b'':
                break
            if packet:
                if len(packet) > MAX_PACKET:
                    raise ValueError('Frame físico excessivo')
                # Uma parada pode ter chegado entre a primeira leitura e recv.
                if stop_epoch.value != last_stop:
                    last_stop = stop_epoch.value
                    apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=last_stop))
                if emergency_epoch.value != last_emergency:
                    last_emergency = emergency_epoch.value
                    apply('stop', dict(reason='terminal_emergency', latch=True))
                apply('frame', json.loads(packet))
                channel.send(b'ack')
            apply('poll', {})  # Só verifica idade/deadline; nunca renova força.
            status.write(dict(ready=True, at_ns=time.monotonic_ns(), pid=os.getpid(),
                              keyboards=len(keys.devices), keyboard_errors=getattr(keys, 'errors', [])[-3:],
                              game_identity=last_game,
                              io={k: getattr(backend, k, 0) for k in ('io_calls', 'last_io_ns', 'max_io_ns')},
                              **policy.snapshot()))
            if writer and time.monotonic_ns()-last_flush >= 1_000_000_000:
                writer.flush()
                last_flush = time.monotonic_ns()
            time.sleep(.005)
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
        status.write(dict(ready=False, fatal=error[:512], at_ns=time.monotonic_ns()))
    finally:
        try:
            if policy is not None and not policy.closed:
                value = policy.apply('close', {}, time.monotonic_ns())
                status.write(dict(ready=False, at_ns=time.monotonic_ns(), pid=os.getpid(),
                                  **({'fatal': error[:512]} if error else {}), **value))
                if writer:
                    writer.append(dict(kind='physical_step', action='close', data={},
                                       timestamp_ns=policy.now_ns, result=value))
            if writer:
                writer.append(dict(kind='footer', complete=error is None,
                                   error=error, result=policy.snapshot() if policy else None))
        finally:
            with ExitStack() as cleanup:
                if writer:
                    cleanup.callback(writer.close)
                cleanup.callback(channel.close)
                if keys:
                    cleanup.callback(keys.close)
                if backend:
                    cleanup.callback(backend.close)


class PhysicalClient:
    def __init__(self, path, *, enable_ffb=False, config=None, keyboard_path=None, trace_path=None, game_pid=None):
        if enable_ffb is not True:
            raise PermissionError('Processo físico exige --enable-ffb')
        self.config = dict(config or dict(strength=10, maximum=20, polarity=1, session_s=120), require_game=True)
        # Validar antes de criar processo/fd usando somente o mock.
        from .backends.mock import MockBackend
        probe = SupervisedPolicy(MockBackend(), **self.config)
        probe.apply('close', {}, 0)
        ctx = multiprocessing.get_context('spawn')
        self.status, self.commands = Mailbox(ctx), Mailbox(ctx)
        self.stop_epoch, self.emergency_epoch = ctx.RawValue('Q', 0), ctx.RawValue('Q', 0)
        self.quit_flag = ctx.RawValue('i', 0)
        self.channel, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.channel.setblocking(False)
        self.process = ctx.Process(target=worker, args=(child, self.status, self.commands,
                                   self.stop_epoch, self.emergency_epoch, self.quit_flag, path, self.config, keyboard_path, trace_path, game_pid))
        self.ready = False
        self.latest = None
        self.generation = self.coalesced = self.nonce = 0
        self.cached_status = {}
        self.started_ns = time.monotonic_ns()
        self.closed = False
        self.last_zero = True
        try:
            self.process.start()
        except BaseException:
            self.channel.close()
            self.process.close()
            raise
        finally:
            child.close()

    def service(self):
        if self.closed:
            return
        try:
            ack = self.channel.recv(16)
        except BlockingIOError:
            ack = None
        if ack == b'ack':
            self.ready = True
        elif ack is not None:
            raise OSError('Supervisor físico encerrou canal')
        state = self.status.read()
        if state:
            self.cached_status = state
            if state.get('fatal'):
                raise OSError('Supervisor físico: ' + state['fatal'])
        now = time.monotonic_ns()
        if self.cached_status.get('at_ns') and now-self.cached_status['at_ns'] > 250_000_000:
            raise TimeoutError('Supervisor EVDEV não avança há 250ms')
        if not self.cached_status and now-self.started_ns > 5_000_000_000:
            raise TimeoutError('Supervisor EVDEV não iniciou em 5s')
        if not self.process.is_alive():
            raise OSError('Supervisor EVDEV morreu; efeito não será renovado')
        if self.ready and self.latest is not None:
            try:
                if self.channel.send(self.latest) != len(self.latest):
                    raise OSError('Pacote de IPC parcial')
            except BlockingIOError:
                return
            self.latest, self.ready = None, False

    def publish(self, result, session):
        if session.core.closed:
            self.stop_epoch.value += 1
            return
        self.generation += 1
        info = result['info']
        packet = bytes.fromhex(info.get('packet_hex') or '')
        urgent = (info.get('event_type') in (3, 5) or
                  (info.get('report_type') == 1 and not result['error'] and packet and (
                   (packet[0] == 0x50 and packet[1] in (2, 3, 4, 5)) or packet[0] == 0x5b or
                   (packet[0] == 0x5a and packet[2] == 3))))
        zero = math.fsum(result['components'].values()) == 0
        urgent = urgent or (zero and not self.last_zero)
        self.last_zero = zero
        if urgent:
            self.stop_epoch.value += 1
        engine = session.core.engine
        zero_reason = ('PID_disabled_or_stopped' if urgent else
                       'PID_paused' if getattr(engine, 'paused', False) else
                       'PID_actuators_disabled' if not getattr(engine, 'actuators_enabled', True) else None)
        value = dict(generation=self.generation, calculated_ns=session.timestamp_ns,
                     stop_epoch=self.stop_epoch.value,
                     components=result['components'], zero_reason=zero_reason,
                     game_force=dict(pid_gain=session.core.engine.gain,
                                     constant_levels=[[i, v['level']] for i, v in sorted(session.core.engine.effects.items())
                                                      if v['kind'] == 1 and v['running']]),
                     effects=[[i, v['kind'], v['running'], v['gain']] for i, v in sorted(session.core.engine.effects.items())])
        encoded = canonical(value).encode()
        if len(encoded) > MAX_PACKET:
            raise ValueError('Frame de força excedeu limite de IPC')
        if self.latest is not None:
            self.coalesced += 1
        self.latest = encoded
        self.service()

    def command(self, action, data):
        if action == 'strength':
            validate_percent(data['percent'])
        self.nonce += 1
        if action == 'stop':
            self.stop_epoch.value += 1
            self.emergency_epoch.value += 1
        self.commands.write(dict(nonce=self.nonce, action=action, data=data))

    def diagnostic(self):
        return dict(self.cached_status, coalesced=self.coalesced, generation=self.generation)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_epoch.value += 1
        self.quit_flag.value = 1
        self.channel.close()
        self.process.join(.5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(.5)
        if not self.process.is_alive():
            self.process.close()
        state = self.status.read()
        if state:
            self.cached_status = state

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
