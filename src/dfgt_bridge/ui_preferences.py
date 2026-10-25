# SPDX-License-Identifier: GPL-2.0-only
"""Persistent GUI preferences, kept separate from game profiles."""
import json
import os
from pathlib import Path
import tempfile

from .i18n import DEFAULT_LOCALE, LOCALES


class LocaleStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            if self.path.stat().st_size > 1024:
                return DEFAULT_LOCALE
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or set(value) != {'version', 'locale'} or
                    type(value['version']) is not int or value['version'] != 1 or value['locale'] not in LOCALES):
                return DEFAULT_LOCALE
            return value['locale']
        except (FileNotFoundError, OSError, ValueError):
            return DEFAULT_LOCALE

    def save(self, locale):
        if locale not in LOCALES:
            raise ValueError('Unsupported interface locale')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({'version': 1, 'locale': locale}, separators=(',', ':'))+'\n'
        fd, name = tempfile.mkstemp(prefix='.ui-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        except BaseException:
            try: os.unlink(name)
            except FileNotFoundError: pass
            raise
