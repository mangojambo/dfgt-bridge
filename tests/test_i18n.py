"""Locale catalog and preference behavior are independent of game profiles."""
import tempfile
import unittest
from pathlib import Path

from test_ffb_audit import ROOT
from dfgt_bridge.i18n import CATALOGS, EN, PT_BR, LOCALES, missing_translations, translate, localized_error
from dfgt_bridge.wheel_art import ASSETS
import xml.etree.ElementTree as ET
from dfgt_bridge.ui_preferences import LocaleStore


class LocalizationTests(unittest.TestCase):
    def test_catalogs_have_full_coverage_and_stable_keys(self):
        self.assertEqual(missing_translations(), {'pt-BR': []})
        self.assertEqual(set(EN), set(PT_BR))
        self.assertEqual(LOCALES, ('en', 'pt-BR'))
        self.assertEqual(translate('settings.strength', 'en'), 'Strength (%)')
        self.assertEqual(translate('settings.strength', 'pt-BR'), 'Intensidade (%)')

    def test_unknown_locale_or_missing_key_falls_back_to_english(self):
        partial = {'en': {'known': 'Hello {name}'}, 'pt-BR': {}}
        self.assertEqual(translate('known', 'pt-BR', catalogs=partial, name='Ada'), 'Hello Ada')
        self.assertEqual(translate('missing', 'pt-BR', catalogs=partial), 'missing')
        self.assertEqual(translate('known', 'unknown', catalogs=partial, name='Ada'), 'Hello Ada')

    def test_common_validation_errors_are_localized(self):
        self.assertEqual(localized_error('ffb_strength: inteiro 0..100', 'en'), 'Strength must be an integer from 0 to 100.')
        self.assertEqual(localized_error('Polaridade deve ser -1 ou 1', 'pt-BR'), 'A polaridade deve ser -1 ou 1.')
        self.assertEqual(localized_error('custom technical detail', 'pt-BR'), 'custom technical detail')

    def test_parameter_interpolation_is_localized_and_safe_on_missing_values(self):
        self.assertEqual(translate('home.profile_summary', 'pt-BR', profile='DiRT 5'), 'Perfil ativo: DiRT 5')
        self.assertEqual(translate('home.profile_summary', 'pt-BR'), 'Perfil ativo: {profile}')

    def test_button_names_are_stable_and_wheel_art_labels_are_catalogued(self):
        for key in (*[f'target.button.{i}' for i in range(1, 26)],
                    'target.hat.up', 'target.hat.down', 'target.hat.left', 'target.hat.right',
                    'source.288', 'source.289', 'source.290', 'source.291', 'source.292',
                    'source.293', 'source.296', 'source.297', 'source.707', 'source.708'):
            self.assertEqual(translate(key, 'en'), translate(key, 'pt-BR'), key)
        for model in ('dfgt', 'g29'):
            root = ET.parse(ASSETS/(model+'-front.svg')).getroot()
            for element in root.iter():
                key = element.attrib.get('data-ui-key')
                if key:
                    self.assertIn(key, EN)
                    self.assertIn(key, PT_BR)

    def test_locale_persistence_is_atomic_private_and_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'ui.json';store = LocaleStore(path)
            self.assertEqual(store.load(), 'en')
            store.save('pt-BR')
            self.assertEqual(store.load(), 'pt-BR')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            store.save('en');self.assertEqual(store.load(), 'en')
            with self.assertRaises(ValueError):store.save('es')

    def test_corrupt_or_unknown_preferences_safely_fall_back_to_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ui.json';store=LocaleStore(path)
            path.write_text('{invalid')
            self.assertEqual(store.load(),'en')
            path.write_text('{"version":1,"locale":"es"}')
            self.assertEqual(store.load(),'en')
