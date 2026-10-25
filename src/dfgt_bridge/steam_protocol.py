# SPDX-License-Identifier: GPL-2.0-only
"""Protocolo local fechado. Nunca transporta argv, shell, ambiente ou código."""
import json
import socket
import struct
from pathlib import Path

from .config import DEFAULTS, validate_profile
from .input.mapping import preset

SOCKET = '/run/dfgt-bridge/control.sock'
APPID = '1038250'
MAX_MESSAGE = 65536


def encode(value):
    data = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError('Mensagem de controle excessiva')
    return data


def receive(channel):
    data, _, flags, _ = channel.recvmsg(MAX_MESSAGE)
    if not data:
        raise EOFError('Canal de controle encerrado')
    if flags & socket.MSG_TRUNC:
        raise ValueError('Mensagem truncada')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Mensagem precisa ser objeto JSON')
    return value


def peer(channel):
    pid, uid, gid = struct.unpack('3i', channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return dict(pid=pid, uid=uid, gid=gid)


def approved(profile):
    value = validate_profile(profile)
    if any(value[k] != v for k, v in DEFAULTS.items()):
        raise ValueError('Perfil dirt5 deve conservar 100%/100%, -1, 327670, unlimited, Y, G29 e auto PIDFF')
    baseline = preset()
    bindings = value['mapping']['bindings']
    if (value['mapping']['axes'] != baseline['axes'] or
            any(bindings.get(k) != v for k, v in baseline['bindings'].items() if k not in ('key:704', 'key:705')) or
            {bindings.get('key:704'), bindings.get('key:705')} != {'button:22', 'button:23'}):
        raise ValueError('Perfil dirt5 deve preservar os mapeamentos aprovados; confirme o preset G29 na GUI')
    return value


class ProcInfo:
    def __init__(self, root='/proc'):
        self.root = Path(root)

    def get(self, pid):
        try:
            base = self.root/str(pid)
            fields = base.joinpath('stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in ('Z', 'X'):
                return None
            return dict(pid=int(pid), uid=base.stat().st_uid, ppid=int(fields[1]),
                        ticks=int(fields[19]), comm=base.joinpath('comm').read_text().strip())
        except (OSError, ValueError, IndexError):
            return None

    def all(self):
        for p in self.root.iterdir():
            if p.name.isdecimal():
                value = self.get(p.name)
                if value:
                    yield value

    def app(self, pid):
        try:
            env = (self.root/str(pid)/'environ').read_bytes().split(b'\0')
            return any(f'{key}={APPID}'.encode() in env for key in ('SteamAppId', 'SteamGameId'))
        except OSError:
            return False


class ControlClient:
    def __init__(self, path=SOCKET, *, timeout=20, factory=socket.socket):
        self.channel = factory(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.channel.settimeout(timeout)
        try:
            self.channel.connect(str(path))
            if peer(self.channel)['uid'] != 0:
                raise PermissionError('Socket de controle não pertence a um servidor root confiável')
        except BaseException:
            self.channel.close()
            raise

    def request(self, op, **data):
        payload = encode(dict(v=1, op=op, **data))
        if self.channel.send(payload) != len(payload):
            raise OSError('Controle parcial')
        result = receive(self.channel)
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'Serviço recusou operação'))
        return result

    def close(self):
        self.channel.close()


def ready(value, physical):
    b = value.get('binding') or {}
    return (value.get('state') == 'ready' and b.get('driver') == 'hid-universal-pidff' and
            b.get('ready') is True and bool(b.get('evdev')) and bool(b.get('ff_capabilities')) and
            bool(b.get('ff_input')) and (not physical or value.get('physical_ready') is True))
