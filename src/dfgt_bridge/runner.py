# SPDX-License-Identifier: GPL-2.0-only
"""Runner G29 passivo por padrão; saída EVDEV supervisionada exige --enable-ffb."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import selectors
import signal
import sys
import time
import struct
from collections import Counter

from .capture import CaptureWriter, Recorder
from .diagnostics import classify_uhid_result
from .input.physical_wheel import PhysicalWheel, SOURCE
from .passive import PassiveSession
from .uhid_io import VirtualDevice
from .pid_binding import PIDBinding, BindingError
from .config import ProfileStore, DEFAULTS, duration
from .input.mapping import preset


def virtual_nodes():
    return set(Path('/sys/bus/hid/devices').glob('0003:046D:C24F.*'))


def bindings(before):
    result = []
    for node in sorted(virtual_nodes()-before):
        driver = node / 'driver'
        result.append(dict(node=node.name,
                           driver=driver.resolve().name if driver.is_symlink() else 'unbound',
                           ff_capabilities=[p.read_text().strip() for p in
                                            node.glob('input/input*/capabilities/ff')]))
    return result


def diagnostic(session, runtime, elapsed, previous_events):
    summary = session.summary()
    result = session.last_result or {}
    counts = summary['counts']
    events = counts.get('uhid', 0)
    print(json.dumps(dict(passive=not bool(runtime.get('physical')), phase=session.phase,
                          allocated=summary['allocated'], active=summary['active'], paused=summary['paused'],
                          created=counts.get('created', 0), freed=counts.get('freed', 0),
                          types=summary['types'], output=counts.get('uhid_6', 0),
                          set_report=counts.get('uhid_13', 0), get_report=counts.get('uhid_9', 0),
                          raw_rejections=counts.get('invalid', 0), allocation_failures=counts.get('allocation_failures', 0),
                          components=result.get('components', {}), force=result.get('final_force', 0),
                          renderer_total=result.get('renderer_total', 0), clipping=counts.get('clipping', 0),
                          events_per_s=(events-previous_events)/max(elapsed, 1e-9), **runtime),
                     sort_keys=True), flush=True)
    return events


def run(args, *, wheel_factory=PhysicalWheel, virtual_factory=VirtualDevice,
        selector_factory=selectors.DefaultSelector, stop=None, physical_factory=None, binding_factory=None,
        status_callback=None):
    """Factories injetáveis para testes offline. Não há factory de backend."""
    origin = time.monotonic_ns()
    stop = {'reason': None} if stop is None else stop
    session = PassiveSession(origin, capacity=args.capacity, strength_percent=args.strength,
                             gas_axis=args.gas_axis, invert_gas=args.invert_gas,
                             mapping=getattr(args, 'mapping', None))
    runtime = dict(max_processing_ns=0, slow_actions=0, missed_ticks=0,
                   input_resyncs=0, binding=[], ready=False, report_classification={}, proprietary_commands={})
    classifications, proprietary_commands = Counter(), Counter()
    writer = recorder = physical = binding = None
    enable_ffb = getattr(args, 'enable_ffb', False)
    physical_config = dict(strength=getattr(args, 'ffb_strength', DEFAULTS['ffb_strength']),
                           maximum=getattr(args, 'ffb_max_strength', DEFAULTS['ffb_max_strength']),
                           polarity=getattr(args, 'ffb_polarity', DEFAULTS['ffb_polarity']),
                           session_s=getattr(args, 'ffb_seconds', DEFAULTS['ffb_seconds']), require_game=True,
                           slew_per_s=getattr(args, 'ffb_slew_rate', DEFAULTS['ffb_slew_rate']))
    trace_path = getattr(args, 'physical_trace', None)
    if enable_ffb and not trace_path and args.capture:
        trace_path = args.capture + '.ffb.jsonl.gz'
    complete, failure = False, None
    try:
        # Recursos externos são fechados mesmo se captura/setup/renderer falhar.
        with ExitStack() as stack:
            stack.callback(session.core.close)
            wheel = stack.enter_context(wheel_factory(args.source))
            if args.capture:
                metadata = session.metadata()
                metadata.update(kernel=platform.release(), python=platform.python_version(),
                                proton=args.proton, hid_route=args.hid_route, scenario=args.scenario,
                                source_id=[0x046d, 0xc29a], input_clock='CLOCK_MONOTONIC',
                                session_origin='live_passive', physical_output='unavailable')
                if enable_ffb:
                    metadata.update(session_origin='supervised_evdev', physical_output='opt_in_evdev',
                                    physical_config=physical_config, physical_trace=trace_path)
                writer = CaptureWriter(args.capture, metadata)
                stack.callback(writer.close)
            binding = (binding_factory or PIDBinding)(timeout=getattr(args, 'bind_timeout', 25))
            stack.callback(binding.finish)  # Depois de DESTROY, libera qualquer probe síncrono.
            binding.prepare()  # Não há UHID ainda: carregar módulo não bloqueia seu GET_REPORT.
            virtual = stack.enter_context(virtual_factory())
            stack.callback(binding.cancel)  # Antes de fechar UHID: não iniciar novas escritas.
            binding.start(virtual.owner_token)
            recorder = Recorder(session, writer, virtual.write)
            selector = stack.enter_context(selector_factory())
            selector.register(virtual.fd, selectors.EVENT_READ, 'uhid')
            selector.register(wheel.fd, selectors.EVENT_READ, 'wheel')
            console = b''
            if sys.stdin.isatty() or getattr(args, 'control_stdin', False):
                selector.register(sys.stdin.fileno(), selectors.EVENT_READ, 'console')
            def start_physical():
                nonlocal physical_factory, physical
                if physical_factory is None:
                    from .physical_worker import PhysicalClient
                    physical_factory = PhysicalClient
                scope = {}
                if getattr(args, 'game_uid', None) is not None:
                    scope['game_scope'] = dict(uid=args.game_uid, after_ticks=args.game_after_ticks)
                physical = stack.enter_context(physical_factory(
                    args.source, enable_ffb=True, config=physical_config,
                    keyboard_path=getattr(args, 'keyboard_event', None), trace_path=trace_path,
                    game_pid=getattr(args, 'game_pid', None), **scope))

            def dispatch(kind, data, stamp=None):
                started = time.monotonic_ns()
                value = recorder.dispatch(kind, data, started if stamp is None else stamp)
                if physical:
                    if kind in ('input', 'uhid', 'tick'):
                        physical.publish(value, session)
                    elif kind == 'close':
                        physical.close()
                    runtime['physical'] = physical.diagnostic()
                if kind == 'uhid':
                    classification = classify_uhid_result(value)
                    classifications[classification['category']] += 1
                    if classification['command']:
                        proprietary_commands[classification['command']] += 1
                    runtime['report_classification'] = dict(classifications)
                    runtime['proprietary_commands'] = dict(proprietary_commands)
                elif kind == 'input':
                    hid_x, hid_z, hid_rz, hid_y, _, _ = struct.unpack('<HBBBBI', session.last_input[1:])
                    runtime['input'] = dict(abs_x=data['state'][0], abs_y=data['state'][1],
                                            abs_z=data['state'][2], hid_x=hid_x, hid_z=hid_z,
                                            hid_rz=hid_rz, hid_y=hid_y, gas_axis=args.gas_axis,
                                            invert_gas=args.invert_gas)
                duration = time.monotonic_ns()-started
                runtime['max_processing_ns'] = max(runtime['max_processing_ns'], duration)
                runtime['slow_actions'] += int(duration > 10_000_000)
                return value

            print('Inicializando G29 046d:c24f; aguardando PIDFF, EVDEV e capacidades FFB. '
                  'Saída física bloqueada até prontidão.', flush=True)
            print(f'Acelerador ABS_Y → HID {args.gas_axis.upper()} (invertido={args.invert_gas}); '
                  'freio ABS_Z → HID Rz; direção ABS_X → HID X16.', flush=True)
            print('Console: race | menu | init | strength 0..60 | summary | quit. Ctrl+C encerra.', flush=True)
            if enable_ffb:
                print('FFB: ffb-stop | ffb-arm | ffb-strength 0..máximo | ffb-status. '
                      'strength continua controlando somente a simulação.', flush=True)
            dispatch('binding', {'ready': False})
            dispatch('input', {'state': wheel.state()})
            next_tick = time.monotonic_ns()
            last_summary = next_tick
            previous_events = 0
            reported_ready = False
            last_progress_report = origin
            while stop['reason'] is None and not session.core.closed:
                try:
                    bound = binding.poll()
                finally:
                    runtime['binding'] = [binding.last]
                if stop['reason'] is not None:
                    break
                if bound and not runtime['ready']:
                    dispatch('binding', {'ready': True})
                    dispatch('resend_input', {})
                    if stop['reason'] is not None:
                        break
                    if not binding.poll():
                        continue
                    if stop['reason'] is not None:
                        break
                    runtime['ready'] = True
                    runtime['binding'] = [binding.last]
                    print('[PRONTO] ' + json.dumps(binding.last, sort_keys=True), flush=True)
                    if getattr(binding, 'dynamic_id_added', False):
                        print('[PIDFF] ID dinâmico C24F registrado; permanece no módulo até descarregá-lo '
                              'ou reiniciar. Não é regra por instância; não conectar outro G29 nessa condição.', flush=True)
                    if enable_ffb:
                        start_physical()
                if physical:
                    physical.service()
                if (runtime['ready'] and not reported_ready and
                        (not enable_ffb or physical.diagnostic().get('ready') is True)):
                    if status_callback:
                        status_callback(dict(state='ready', binding=binding.last,
                                             physical_ready=enable_ffb))
                    reported_ready = True
                for key, _ in selector.select(.01):
                    if key.data == 'uhid':
                        # Não drenar infinitamente: dar oportunidade a input/tick.
                        for _ in range(32):
                            event = virtual.read()
                            if event is None:
                                break
                            dispatch('uhid', {'raw_hex': event.hex()})
                            if session.core.closed:
                                break
                        if session.core.closed:
                            break
                    elif key.data == 'wheel':
                        frames = wheel.read_frames()
                        for state, stamp in zip(frames, wheel.frame_times_ns):
                            dispatch('input', {'state': state, 'sample_ns': stamp})
                        runtime['input_resyncs'] = wheel.resyncs
                    elif key.data == 'console':
                        import os
                        data = os.read(key.fd, 256)
                        if not data:
                            selector.unregister(key.fd)
                            if getattr(args, 'control_stdin', False):
                                stop['reason'] = 'GUI_control_EOF'
                            continue
                        console += data
                        if len(console) > 1024:
                            console = b''
                        while b'\n' in console:
                            command, console = console.split(b'\n', 1)
                            words = command.decode(errors='replace').strip().split()
                            if words in (['race'], ['menu'], ['init']):
                                phase = {'race': 'game_operator_marked', 'menu': 'menu_operator_marked',
                                         'init': 'initialization_unattributed'}[words[0]]
                                dispatch('mark', {'phase': phase})
                            elif words == ['quit']:
                                stop['reason'] = 'operator_quit'
                            elif words == ['summary']:
                                print(json.dumps(session.summary(), sort_keys=True), flush=True)
                            elif physical and words == ['ffb-stop']:
                                physical.command('stop', dict(reason='terminal_emergency', latch=True))
                            elif physical and words == ['ffb-arm']:
                                physical.command('arm', dict(requested_ns=time.monotonic_ns()))
                            elif physical and words == ['ffb-status']:
                                print(json.dumps(physical.diagnostic(), sort_keys=True), flush=True)
                            elif (physical and len(words) == 2 and words[0] == 'ffb-strength'
                                  and words[1].isdigit() and 0 <= int(words[1]) <= physical_config['maximum']):
                                physical.command('strength', dict(percent=int(words[1])))
                            elif len(words) == 2 and words[0] == 'strength' and words[1].isdigit() and 0 <= int(words[1]) <= 60:
                                dispatch('strength', {'percent': int(words[1])})
                            else:
                                print('Comando inválido: race | menu | init | strength 0..60 | summary | quit', flush=True)
                now = time.monotonic_ns()
                if session.core.closed:
                    stop['reason'] = 'UHID_STOP'
                    break
                if now >= next_tick:
                    runtime['missed_ticks'] += max(0, (now-next_tick)//22_222_223)
                    dispatch('tick', {})
                    next_tick = now + 22_222_223  # Sem recuperar ticks antigos por rajada.
                if status_callback and reported_ready and now-last_progress_report >= 500_000_000:
                    status_callback(dict(state='heartbeat'))
                    last_progress_report = now
                if now-last_summary >= 1_000_000_000:
                    runtime['binding'] = [binding.last]
                    if not getattr(args, 'operational_log', False):
                        previous_events = diagnostic(session, runtime, (now-last_summary)/1e9, previous_events)
                    if writer:
                        writer.flush()
                    last_summary = now
                if args.duration is not None and now-origin >= args.duration*1e9:
                    if not runtime['ready']:
                        raise BindingError(f'Duração encerrou antes da prontidão PIDFF; diagnóstico={binding.last}')
                    stop['reason'] = 'duration'
            # Ainda dentro dos contextos para registrar cleanup antes do DESTROY.
            dispatch('close', {'reason': stop['reason']})
            if writer:
                writer.append(dict(kind='footer', complete=True, summary=session.summary(),
                                   runtime=runtime, reason=stop['reason']))
                writer.flush()
            complete = True
    except BaseException as exc:
        runtime['ready'] = False
        if binding is not None:
            runtime['binding'] = [binding.last]
        failure = type(exc).__name__ + ': ' + str(exc)
        if status_callback:
            status_callback(dict(state='failed', error=failure[:1000], binding=runtime['binding']))
        # ExitStack já removeu UHID/entrada; não tentar entregar mais pacotes.
        # Se o stream fechou, a falta do footer denuncia captura interrompida.
        raise
    finally:
        session.core.close()
        if getattr(args, 'operational_log', False):
            print(json.dumps(dict(event='runner_exit', reason=stop['reason'], failure=failure, complete=complete)), flush=True)
        else:
            print(json.dumps(dict(summary=session.summary(), runtime=runtime,
                                  complete=complete, failure=failure), sort_keys=True), flush=True)
    return session.summary()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=SOURCE)
    parser.add_argument('--capture', metavar='CAPTURA.jsonl')
    parser.add_argument('--profile', default='dirt5', help='Perfil de jogo compartilhado com a GUI')
    parser.add_argument('--profiles-file', help='JSON de perfis; padrão XDG_CONFIG_HOME/dfgt-bridge/profiles.json')
    parser.add_argument('--save-profile', action='store_true', help='Salvar opções explícitas no perfil antes de executar')
    parser.add_argument('--control-stdin', action='store_true', help='Console via pipe da GUI; EOF encerra o bridge')
    parser.add_argument('--operational-log', action='store_true', help='Somente eventos operacionais, sem diagnóstico periódico')
    parser.add_argument('--status-fd', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--game-uid', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--game-after-ticks', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--preset', choices=('g29', 'gears', 'custom'))
    parser.add_argument('--auto-pidff', action='store_true', default=None,
                        help='Compatibilidade v0.8: associação PIDFF automática já é padrão')
    parser.add_argument('--identity', choices=('g29',), help='Identidade virtual fixa preservada')
    parser.add_argument('--bind-timeout', type=float, default=25, help='Prazo de prontidão PIDFF/EVDEV/FF, 1..60s')
    parser.add_argument('--enable-ffb', action='store_true', help='Autoriza saída física DFGT supervisionada')
    parser.add_argument('--ffb-strength', type=int, choices=range(0, 101), metavar='0..100', help='Padrão: 100')
    parser.add_argument('--ffb-max-strength', type=int, choices=range(0, 101), metavar='0..100', help='Padrão: 100')
    parser.add_argument('--ffb-slew-rate', type=float, metavar='UNIDADES/S', help='Padrão: 327670 unidades/s')
    parser.add_argument('--ffb-polarity', type=int, choices=(-1, 1), help='Padrão oficial: -1')
    parser.add_argument('--ffb-seconds', type=str, metavar='unlimited|30..240', help='Padrão: unlimited')
    parser.add_argument('--keyboard-event', help='Teclado evdev explícito; somente com --enable-ffb')
    parser.add_argument('--physical-trace', help='Trace da política física (replay só offline)')
    parser.add_argument('--game-pid', type=int, help='PID de DiRT5.exe; padrão: descobrir e supervisionar automaticamente')
    parser.add_argument('--gas-axis', choices=('z', 'y', 'both'),
                        help='Eixo virtual do acelerador: y corresponde à v0.8 usada no DiRT 5 (padrão)')
    parser.add_argument('--invert-gas', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--strength', type=int, choices=range(0, 61), default=20, metavar='0..60',
                        help='Ganho da simulação; não controla o motor')
    parser.add_argument('--capacity', type=int, choices=range(1, 41), default=40, metavar='1..40')
    parser.add_argument('--duration', type=float, help='Encerrar após N segundos, opcional')
    parser.add_argument('--proton', default='unknown', help='Versão anotada pelo operador')
    parser.add_argument('--hid-route', choices=('unknown', 'evdev', 'hidraw'), default='unknown',
                        help='Rota anotada; não é detectada a partir dos bytes')
    parser.add_argument('--scenario', default='unknown', help='Carro/pista/ajustes anotados')
    args = parser.parse_args(argv)
    try:
        store = ProfileStore(args.profiles_file)
        profile = store.load(args.profile)
        for name in DEFAULTS:
            value = getattr(args, name)
            if value is not None:
                profile[name] = duration(value) if name == 'ffb_seconds' else value
        if args.preset is not None:
            if args.preset != 'custom':
                profile['mapping'] = preset(args.preset)
            profile['preset'] = args.preset
        from .config import validate_profile
        profile = validate_profile(profile)
        for name in DEFAULTS:
            setattr(args, name, profile[name])
        args.mapping = profile['mapping']
        args.preset = profile['preset']
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.ffb_strength > args.ffb_max_strength:
        parser.error('--ffb-strength não pode exceder --ffb-max-strength')
    if not __import__('math').isfinite(args.ffb_slew_rate) or args.ffb_slew_rate <= 0:
        parser.error('--ffb-slew-rate deve ser finito e positivo')
    if not __import__('math').isfinite(args.bind_timeout) or not 1 <= args.bind_timeout <= 60:
        parser.error('--bind-timeout deve ser 1..60 segundos')
    if args.game_pid is not None and args.game_pid < 1:
        parser.error('--game-pid deve ser positivo e identificar DiRT5.exe')
    if args.game_uid is not None and (args.game_uid <= 0 or args.game_after_ticks < 1):
        parser.error('Escopo de jogo inválido')
    if args.duration is not None and (not __import__('math').isfinite(args.duration) or args.duration <= 0):
        parser.error('--duration deve ser finita e positiva')
    if args.save_profile:
        try:
            store.save(args.profile, profile)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    stop = {'reason': None}
    if args.preset == 'gears':
        print('Marchas prontas: possíveis conflitos com menus/ações dos botões; configure as marchas no jogo.', flush=True)
    previous = {}
    def interrupted(number, frame):
        stop['reason'] = signal.Signals(number).name
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, interrupted)
        channel = None
        if args.status_fd is not None:
            import socket
            channel = socket.socket(fileno=args.status_fd)
            channel.settimeout(.1)
        try:
            callback = (lambda value: channel.send(json.dumps(value, allow_nan=False).encode())) if channel else None
            if callback:
                run(args, stop=stop, status_callback=callback)
            else:
                run(args, stop=stop)
        finally:
            if channel:
                channel.close()
    except (OSError, EOFError, ValueError, BindingError) as exc:
        print('Runner encerrado: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
