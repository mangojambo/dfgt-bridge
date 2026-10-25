# SPDX-License-Identifier: GPL-2.0-only
"""Perfis versionados compartilhados pela CLI e GUI; sem acesso a dispositivos."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile

from .input.mapping import preset, validate_mapping

FFB_DEFAULTS = dict(ffb_strength=100, ffb_max_strength=100, ffb_polarity=-1,
                    ffb_slew_rate=327670, ffb_seconds=None)
DEFAULTS = dict(FFB_DEFAULTS, gas_axis='y', identity='g29', auto_pidff=True, invert_gas=False)


def duration(value):
    if value is None or value == 'unlimited':
        return None
    if isinstance(value, bool):
        raise ValueError('Duração: unlimited ou inteiro de 30..240 s')
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('Duração: unlimited ou inteiro de 30..240 s') from exc
    if str(number) != str(value) or not 30 <= number <= 240:
        raise ValueError('Duração: unlimited ou inteiro de 30..240 s')
    return number


def default_profile():
    return dict(DEFAULTS, preset='g29', mapping=preset())


def validate_profile(value):
    base = default_profile()
    if not isinstance(value, dict) or set(value)-set(base):
        raise ValueError('Campos desconhecidos no perfil')
    base.update(copy.deepcopy(value))
    for key in ('ffb_strength', 'ffb_max_strength'):
        if type(base[key]) is not int or not 0 <= base[key] <= 100:
            raise ValueError(f'{key}: inteiro 0..100')
    if base['ffb_strength'] > base['ffb_max_strength']:
        raise ValueError('Intensidade não pode exceder o máximo')
    if type(base['ffb_polarity']) is not int or base['ffb_polarity'] not in (-1, 1):
        raise ValueError('Polaridade deve ser -1 ou 1')
    rate = base['ffb_slew_rate']
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Slew deve ser finito e positivo')
    base['ffb_seconds'] = duration(base['ffb_seconds'])
    if (base['gas_axis'] not in ('y', 'z', 'both') or base['identity'] != 'g29' or
            base['auto_pidff'] is not True or type(base['invert_gas']) is not bool or
            base['preset'] not in ('g29', 'gears', 'custom')):
        raise ValueError('Identidade, entrada ou preset inválidos')
    base['mapping'] = validate_mapping(base['mapping'])
    return base


def default_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'dfgt-bridge/profiles.json'


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()

    def read(self):
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 1_048_576:
            raise ValueError('Arquivo de perfis excessivo')
        value = json.loads(self.path.read_text())
        if (not isinstance(value, dict) or set(value) != {'version', 'profiles'} or
                type(value['version']) is not int or value['version'] != 1):
            raise ValueError('Formato de perfis incompatível')
        if not isinstance(value['profiles'], dict) or len(value['profiles']) > 100:
            raise ValueError('Perfis inválidos')
        return {self.name(k): validate_profile(v) for k, v in value['profiles'].items()}

    @staticmethod
    def name(name):
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('Nome de perfil inválido')
        return name

    def load(self, name='dirt5'):
        return copy.deepcopy(self.read().get(self.name(name), default_profile()))

    def save(self, name, profile):
        profiles = self.read()
        profiles[self.name(name)] = validate_profile(profile)
        if len(profiles) > 100:
            raise ValueError('Máximo de 100 perfis')
        encoded = json.dumps(dict(version=1, profiles=profiles), ensure_ascii=False, indent=2, allow_nan=False)+'\n'
        if len(encoded.encode('utf-8')) > 1_048_576:
            raise ValueError('Arquivo de perfis excessivo')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Substituição atômica: leitor nunca encontra metade de um JSON.
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, prefix='.profiles-', delete=False) as out:
            temporary = Path(out.name)
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, self.path)
