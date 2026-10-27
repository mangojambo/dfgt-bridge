"""Profiles, mappings, and GUI models are verified offline with labeled vectors."""
import copy
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge.config import DEFAULTS, ProfileStore, default_profile, validate_profile, duration
from dfgt_bridge.input.mapping import preset
from dfgt_bridge.mapping_editor import MappingEditor, MappingConflict
from dfgt_bridge.supervised import SupervisedPolicy
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge import runner


def state(*keys, hx=0, hy=0, x=8192, gas=255, brake=255):
    return (x, gas, brake, hx, hy, list(keys))


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.editor = MappingEditor()

    def test_target_selection_shows_all_aggregated_sources(self):
        self.assertEqual(self.editor.select('button:6'), ['key:293', 'key:301'])

    def test_conflict_requires_explicit_move_and_undo_redo_restores(self):
        original = copy.deepcopy(self.editor.profile)
        with self.assertRaises(MappingConflict):
            self.editor.bind('key:293', 'button:1')
        self.assertEqual(self.editor.profile, original)
        self.editor.bind('key:293', 'button:1', replace=True)
        self.assertEqual(self.editor.profile['preset'], 'custom')
        self.editor.undo()
        self.assertEqual(self.editor.profile, original)
        self.editor.redo()
        self.assertEqual(self.editor.profile['mapping']['bindings']['key:293'], 'button:1')

    def test_detector_ignores_release_repeat_and_steering(self):
        self.editor.detect()
        self.editor.observe(state(), [(1, 293, 0), (1, 293, 2), (3, 0, 100)])
        self.assertTrue(self.editor.detecting)
        self.editor.observe(state(), [(1, 704, 1), (1, 704, 0)])  # Pulso completo num lote.
        self.assertEqual(self.editor.detected, 'key:704')
        self.assertFalse(self.editor.detecting)

    def test_detector_does_not_recapture_already_held_button(self):
        self.editor.observe(state(293))
        self.editor.detect()
        self.editor.observe(state(293))
        self.assertIsNone(self.editor.detected)
        self.editor.observe(state(293, 302))
        self.assertEqual(self.editor.detected, 'key:302')

    def test_real_time_preview_and_removal_use_same_report_function(self):
        value = self.editor.observe(state(293, 301))
        self.assertIn('button:6', value['virtual'])
        self.editor.remove('key:293')
        self.assertIn('button:6', self.editor.observe(state(301))['virtual'])
        self.editor.remove('key:301')
        self.assertEqual(int.from_bytes(bytes.fromhex(self.editor.preview()['report'])[7:11], 'little'), 0)

    def test_presets_restore_and_keep_approved_ffb_defaults(self):
        self.editor.apply_preset('gears')
        self.assertIn('conflitos', self.editor.notice)
        self.editor.apply_preset('g29')
        self.assertEqual(self.editor.profile, default_profile())


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.store = ProfileStore(Path(self.directory)/'profiles.json')

    def test_approved_defaults_are_shared_and_no_file_is_created_on_load(self):
        self.assertEqual(self.store.load(), default_profile())
        self.assertEqual(DEFAULTS, dict(ffb_strength=100, ffb_max_strength=100, ffb_polarity=-1,
            ffb_slew_rate=327670, ffb_seconds=None, ffb_spring_gain=3.0,
            gas_axis='y', identity='g29', auto_pidff=True, invert_gas=False))
        self.assertFalse(self.store.path.exists())

    def test_profiles_round_trip_and_are_isolated_per_game(self):
        profile = default_profile()
        profile['mapping'] = preset('gears', down='button:13', up='button:14')
        profile['preset'] = 'gears'
        self.store.save('dirt5', profile)
        self.store.save('outro jogo', default_profile())
        self.assertEqual(self.store.load('dirt5'), profile)
        self.assertEqual(self.store.load('outro jogo'), default_profile())
        changed = self.store.load('dirt5')
        changed['mapping']['bindings'].clear()
        self.assertEqual(self.store.load('dirt5'), profile)

    def test_invalid_profiles_do_not_replace_valid_configuration(self):
        self.store.save('dirt5', default_profile())
        before = self.store.path.read_bytes()
        for update in ({'ffb_strength': 101}, {'ffb_seconds': 0}, {'identity': 'g920'}, {'auto_pidff': False}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.store.save('dirt5', dict(default_profile(), **update))
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_profile_rename_and_delete_are_atomic_and_preserve_other_profiles(self):
        self.store.save('dirt5', default_profile())
        other = default_profile();other['ffb_strength'] = 75
        self.store.save('other', other)
        before = self.store.path.stat().st_mode & 0o777
        self.store.rename_profile('other', 'renamed')
        self.assertEqual(self.store.load('renamed'), other)
        self.assertEqual(self.store.load('dirt5'), default_profile())
        with self.assertRaises(ValueError):self.store.rename_profile('renamed', 'dirt5')
        self.store.delete_profile('renamed')
        self.assertEqual(self.store.read(), {'dirt5': default_profile()})
        with self.assertRaises(ValueError):self.store.delete_profile('renamed')
        self.assertEqual(self.store.path.stat().st_mode & 0o777, before)

    def test_unlimited_profile_keeps_watchdog_and_emergency(self):
        dev = MockBackend(clock=lambda: policy.now_ns/1e9)
        policy = SupervisedPolicy(dev)
        self.assertIsNone(policy.output.test_limit_s)
        self.assertEqual((policy.output.strength_percent, policy.output.max_strength_percent, policy.polarity,
                          policy.output.slew_per_s), (100, 100, -1, 327670))
        for i in range(1, 20002):  # > 500 s: nenhum limite global oculto de 120/240 s.
            now = i*25_000_000
            policy.apply('frame', dict(generation=i, calculated_ns=now,
                components={k: 32767 if k == 'constant' else 0 for k in OUTPUT_COMPONENTS}), now)
        self.assertEqual(dev.level, -32767)
        policy.apply('poll', {}, now+100_000_000)
        self.assertTrue(policy.latched)
        self.assertEqual(dev.level, 0)
        policy.apply('close', {}, policy.now_ns)

    def test_cli_resolves_same_profile_and_explicit_overrides(self):
        profile = default_profile()
        profile['mapping'] = preset('gears')
        profile['preset'] = 'gears'
        self.store.save('dirt5', profile)
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(['--profiles-file', str(self.store.path)]), 0)
        args = run.call_args.args[0]
        self.assertEqual(args.mapping, profile['mapping'])
        self.assertEqual((args.ffb_strength, args.ffb_max_strength, args.ffb_polarity,
                          args.ffb_slew_rate, args.ffb_seconds), (100, 100, -1, 327670, None))
        self.assertFalse(args.enable_ffb)
        self.assertIsNone(args.capture)
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            runner.main(['--profiles-file', str(self.store.path), '--ffb-seconds', '60', '--ffb-polarity', '1'])
        self.assertEqual((run.call_args.args[0].ffb_seconds, run.call_args.args[0].ffb_polarity), (60, 1))
        self.assertEqual(self.store.load()['ffb_polarity'], -1)

    def test_duration_parsing_has_no_implicit_finite_timeout(self):
        self.assertIsNone(duration('unlimited'))
        for value in (30, '120', 240):
            self.assertEqual(duration(value), int(value))
        for value in ('infinity', 0, -1, 120.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                duration(value)

    def test_cli_save_and_invalid_arguments_preserve_shared_profile_file(self):
        with patch.object(runner, 'run'), patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(['--profiles-file', str(self.store.path), '--save-profile',
                                         '--ffb-seconds', 'unlimited', '--preset', 'g29']), 0)
        self.assertEqual(self.store.load(), default_profile())
        original = self.store.path.read_bytes()
        with redirect_stderr(io.StringIO()), patch.object(runner, 'run') as run, self.assertRaises(SystemExit):
            runner.main(['--profiles-file', str(self.store.path), '--save-profile', '--bind-timeout', '0'])
        run.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), original)
