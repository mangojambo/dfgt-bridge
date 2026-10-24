# SPDX-License-Identifier: GPL-2.0-only
"""Associação HID transitória, com propriedade do UHID e prontidão verificadas.

new_id do HID é global ao driver (não há remove_id no HID upstream). Só se
permite o fallback com exatamente o nosso G29 presente. Nunca altera IDs de
DFGT/G920, parâmetros globais HID, udev ou arquivos de configuração.
"""
import errno
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import threading
import time

from .input.virtual_g29 import NAME

DRIVER = 'hid-universal-pidff'
DYNAMIC_ID = b'0003 0000046D 0000C24F\n'


class BindingError(RuntimeError):
    pass


def bitmap(value):
    result = 0
    for word in value.split():
        number = int(word, 16)
        if not 0 <= number < 2**(struct.calcsize('L')*8):
            raise ValueError('Palavra de bitmap sysfs inválida')
        result = (result << (struct.calcsize('L')*8)) | number
    return result


def load_existing_module():
    """Carrega módulo instalado; não instala pacotes, não grava configuração."""
    try:
        result = subprocess.run(['modprobe', DRIVER], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BindingError(f'Não foi possível carregar o módulo existente {DRIVER}: {exc}') from exc
    if result.returncode:
        raise BindingError(f'Driver {DRIVER} indisponível: {result.stderr.strip()[:1000]}; '
                           'nenhum driver foi instalado')


class PIDBinding:
    def __init__(self, *, timeout=25, root='/sys', dev_root='/dev', clock=time.monotonic,
                 module_loader=load_existing_module, writer=None, event_exists=None):
        if not math.isfinite(timeout) or not 1 <= timeout <= 60:
            raise ValueError('Timeout de associação deve ser 1..60s')
        self.root, self.dev_root = Path(root), Path(dev_root)
        self.devices = self.root / 'bus/hid/devices'
        self.drivers = self.root / 'bus/hid/drivers'
        self.virtual_root = (self.root / 'devices/virtual/misc/uhid').resolve()
        self.driver = self.drivers / DRIVER
        self.timeout, self.clock = timeout, clock
        self.module_loader = module_loader
        self.writer = writer or self._write
        self.event_exists = event_exists or self._event_exists
        self.before = self.nodes()
        self.target = self.real_target = self.token = None
        self.deadline = None
        self.job = None
        self.error = None
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.ready = False
        self.dynamic_id_added = False
        self.dynamic_id_attempted = False
        self.phase = 'preflight'
        self.last = dict(node=None, driver='unbound', evdev=[], ff_capabilities=[], ready=False)

    def nodes(self):
        return set(self.devices.glob('0003:046D:C24F.*'))

    @staticmethod
    def _write(path, payload):
        # Uma escrita inteira; sysfs pode bloquear aguardando GET_REPORT.
        with path.open('wb', buffering=0) as stream:
            if stream.write(payload) != len(payload):
                raise OSError('Escrita sysfs parcial')

    def prepare(self):
        if not self.driver.is_dir():
            if self.before:
                self.fail('Há outro G29 presente; não carregar/registrar driver para ele')
            self.module_loader()
        if not self.driver.is_dir() or not (self.driver / 'bind').exists():
            self.fail(f'Driver {DRIVER} ausente ou sem interface bind neste kernel')

    def start(self, token):
        if not isinstance(token, str) or not token.startswith('dfgt-bridge-'):
            self.fail('UHID não forneceu identificador de propriedade válido')
        self.token = token
        self.deadline = self.clock()+self.timeout
        self.phase = 'waiting_for_owned_uhid'

    @staticmethod
    def uevent(path):
        return dict(line.split('=', 1) for line in (path / 'uevent').read_text().splitlines() if '=' in line)

    def owned(self, path):
        real = path.resolve(strict=True)
        if not real.is_relative_to(self.virtual_root) or real.parent != self.virtual_root:
            raise BindingError('Recusado: alvo não pertence ao barramento UHID virtual')
        values = self.uevent(path)
        if (values.get('HID_ID') != '0003:0000046D:0000C24F' or
                values.get('HID_PHYS') != self.token or values.get('HID_NAME') != NAME):
            raise BindingError('Recusado: identidade/token do UHID não pertence a este processo')
        if self.real_target is not None and real != self.real_target:
            raise BindingError('Recusado: caminho do UHID foi substituído')
        return real

    def current_driver(self):
        link = self.target / 'driver'
        if not link.is_symlink():
            return 'unbound'
        try:
            actual = link.resolve(strict=True)
        except FileNotFoundError:
            return 'unbound'  # Link transitório removido pelo probe anterior.
        if actual.parent != self.drivers.resolve():
            raise BindingError('Driver fora do barramento HID esperado')
        return actual.name

    def _event_exists(self, event):
        # Só stat; nunca abrir EVDEV de entrada/FF para verificar prontidão.
        try:
            major, minor = map(int, (event / 'dev').read_text().strip().split(':'))
            info = (self.dev_root / 'input' / event.name).stat()
            return stat.S_ISCHR(info.st_mode) and info.st_rdev == os.makedev(major, minor)
        except (OSError, ValueError):
            return False

    def inspect(self):
        self.owned(self.target)
        finished = self.done.is_set()
        entries, caps, usable = [], [], []
        for input_path in sorted(self.target.glob('input/input*')):
            if not input_path.resolve().is_relative_to(self.real_target):
                raise BindingError('Entrada virtual aponta para outro dispositivo')
            try:
                ev = bitmap((input_path / 'capabilities/ev').read_text())
                axes = bitmap((input_path / 'capabilities/abs').read_text())
                ff_text = (input_path / 'capabilities/ff').read_text().strip()
                ff = bitmap(ff_text)
            except (OSError, ValueError):
                continue
            caps.append(ff_text)
            for event in sorted(input_path.glob('event*')):
                if not event.resolve().is_relative_to(self.real_target):
                    raise BindingError('EVDEV não pertence ao UHID criado')
                if self.event_exists(event):
                    entries.append(str(self.dev_root / 'input' / event.name))
                    if ev & (1 << 21) and ev & (1 << 3) and axes & 1 and ff & (1 << 0x52):
                        usable.append(event.name)
        actual = self.current_driver()
        self.last = dict(node=self.target.name, driver=actual, evdev=entries,
                         ff_capabilities=caps, ff_input=usable, phase=self.phase,
                         dynamic_id_added=self.dynamic_id_added,
                         dynamic_id_attempted=self.dynamic_id_attempted,
                         ready=actual == DRIVER and bool(usable) and finished)
        return self.last

    def safe_write(self, path, payload):
        if self.cancelled.is_set():
            raise BindingError('Associação cancelada')
        self.owned(self.target)
        if path == self.driver / 'new_id' and self.nodes() != {self.target}:
            raise BindingError('Outro G29 apareceu; registro global recusado')
        self.writer(path, payload)

    def attach(self):
        try:
            actual = self.current_driver()
            if actual not in ('unbound', DRIVER):
                self.phase = 'unbind_owned_virtual_device'
                try:
                    self.safe_write(self.drivers / actual / 'unbind', (self.target.name+'\n').encode())
                except OSError as exc:
                    # O probe logitech pode ter acabado de falhar (-22), removendo o link.
                    if exc.errno != errno.ENODEV or self.current_driver() != 'unbound':
                        raise
            if self.current_driver() != DRIVER:
                self.phase = 'bind_owned_virtual_device'
                try:
                    self.safe_write(self.driver / 'bind', (self.target.name+'\n').encode())
                except OSError as exc:
                    if exc.errno != errno.ENODEV:
                        raise
                    # HID upstream não tem driver_override; não registrar ID se
                    # houver outro C24F, mesmo que seja outro UHID ou esteja ligado.
                    if self.nodes() != {self.target}:
                        raise BindingError('Outro G29 presente: new_id global recusado para preservar seu driver')
                    if not (self.driver / 'new_id').is_file():
                        raise BindingError('Driver não aceita ID e não expõe new_id')
                    self.phase = 'register_guarded_dynamic_id'
                    self.dynamic_id_attempted = True
                    self.safe_write(self.driver / 'new_id', DYNAMIC_ID)
                    self.dynamic_id_added = True
            self.phase = 'verify_input_and_ff'
        except BaseException as exc:
            self.error = exc
        finally:
            self.done.set()

    def poll(self):
        if self.cancelled.is_set():
            self.fail('Associação cancelada')
        if self.deadline is None:
            self.fail('Associação não iniciada')
        try:
            if self.target is None:
                matches = []
                for path in self.nodes()-self.before:
                    try:
                        if self.uevent(path).get('HID_PHYS') == self.token:
                            self.owned(path)
                            matches.append(path)
                    except FileNotFoundError:
                        continue
                if len(matches) > 1:
                    self.fail('Mais de um UHID apresenta o token deste processo')
                if matches:
                    self.target = matches[0]
                    self.real_target = self.owned(self.target)
                    self.job = threading.Thread(target=self.attach, daemon=True, name='owned-g29-pidff-bind')
                    self.job.start()
            if self.target is not None:
                self.inspect()
                if self.error:
                    self.fail(f'Falha de associação: {type(self.error).__name__}: {self.error}')
                if not self.ready and self.clock() >= self.deadline:
                    self.fail(f'Timeout de {self.timeout}s aguardando {DRIVER}, EVDEV virtual e FF_CONSTANT')
                if self.last['ready']:
                    self.ready = True
                    self.phase = self.last['phase'] = 'ready'
                    return True
                if self.ready:
                    self.fail('G29 perdeu driver PIDFF, entrada EVDEV ou capacidades FF')
            if self.clock() >= self.deadline:
                self.fail(f'Timeout de {self.timeout}s aguardando {DRIVER}, EVDEV virtual e FF_CONSTANT')
        except (OSError, ValueError, BindingError) as exc:
            if isinstance(exc, BindingError) and 'diagnóstico=' in str(exc):
                raise
            if isinstance(exc, FileNotFoundError):
                self.last = dict(self.last, driver='missing', evdev=[], ff_capabilities=[], ff_input=[])
            self.fail(f'UHID removido ou associação inválida: {exc}')
        return False

    def fail(self, reason):
        self.ready = False
        self.last = dict(self.last, ready=False, error=reason, failed_phase=self.phase)
        raise BindingError(f'{reason}; diagnóstico={self.last}. '
                           'Conferir o log do kernel para esse nó e erros de probe (por exemplo logitech -22).')

    def cancel(self):
        self.cancelled.set()

    def finish(self):
        self.cancel()
        if self.job is not None:
            # Chamar depois de fechar UHID para liberar GET_REPORT pendente.
            self.job.join(.5)
