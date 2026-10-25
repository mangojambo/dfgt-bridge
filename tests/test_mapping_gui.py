"""Widgets/Canvas/reader/processo falsos: exercita a GUI sem display nem hardware."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_ffb_audit import ROOT
from dfgt_bridge import gui
from dfgt_bridge.config import ProfileStore, default_profile
from dfgt_bridge.input.mapping import TARGET_NAMES, PHYSICAL_NAMES
from dfgt_bridge.wheel_art import WheelArt, controls


class Variable:
    def __init__(self, *args, value=None, **kwargs):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Widget:
    """Contrato mínimo de widgets Tk; todos os callbacks reais permanecem ativos."""
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.events, self.items, self.rows = {}, {}, []
        self.text = ''
        self.destroyed = False
        self.scheduled = []
    def __getattr__(self, name):
        if name.startswith('create_'):
            def create(*args, **kwargs):
                i = len(self.items)+1
                self.items[i] = dict(kwargs)
                return i
            return create
        if name in ('pack', 'grid', 'rowconfigure', 'columnconfigure', 'title', 'geometry',
                    'minsize', 'protocol', 'add', 'select', 'addtag_withtag', 'see',
                    'clipboard_clear', 'clipboard_append', 'yview', 'set'):
            return lambda *a, **k: None
        raise AttributeError(name)
    def bind(self, key, callback):
        self.events[key] = callback
    def tag_bind(self, key, event, callback):
        self.events[(key, event)] = callback
    def configure(self, **kwargs):
        self.options.update(kwargs)
    def itemconfigure(self, i, **kwargs):
        self.items[i].update(kwargs)
    def delete(self, *args):
        self.rows.clear();self.items.clear();self.text = ''
    def insert(self, index, value):
        self.rows.append(value);self.text += str(value)
    def get(self, *args):
        return self.text
    def curselection(self):
        return ()
    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
    def destroy(self):
        self.destroyed = True


class GUITests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.store = ProfileStore(Path(self.directory)/'profiles.json')
        for name in ('Frame', 'Label', 'Button', 'Notebook', 'Combobox', 'Entry', 'Checkbutton', 'LabelFrame', 'Scrollbar'):
            self.enterContext(patch.object(gui.ttk, name, Widget))
        for name in ('Canvas', 'Listbox', 'Text'):
            self.enterContext(patch.object(gui.tk, name, Widget))
        for name in ('StringVar', 'BooleanVar'):
            self.enterContext(patch.object(gui.tk, name, Variable))
        self.errors = self.enterContext(patch.object(gui.messagebox, 'showerror'))
        self.enterContext(patch.object(gui.messagebox, 'showinfo'))
        self.popen = self.enterContext(patch.object(gui.subprocess, 'Popen'))
        self.factory = Mock()
        self.root = Widget()
        self.app = gui.BridgeGUI(self.root, self.store, wheel_factory=self.factory)

    def test_opening_gui_never_opens_device_spawns_motor_or_writes_profile(self):
        self.factory.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.app.form['ffb_strength'].get(), 100)
        self.assertEqual(self.app.form['ffb_polarity'].get(), -1)
        self.assertEqual(self.app.form['ffb_seconds'].get(), 'unlimited')

    def test_all_virtual_buttons_have_clickable_front_hotspots(self):
        self.assertTrue(set(TARGET_NAMES) <= controls('g29'))
        self.assertTrue({f'key:{code}' for code in PHYSICAL_NAMES} <= controls('dfgt'))
        for target in TARGET_NAMES:
            callback = self.app.g29_art.canvas.events[('control:'+target, '<Button-1>')]
            callback(None)
            self.assertEqual(self.app.editor.selected, target)
            self.assertEqual(self.app.target.get(), TARGET_NAMES[target])

    def test_aggregated_physical_sources_highlight_the_same_virtual_button(self):
        self.app.editor.observe((8192, 255, 255, 0, 0, [293, 301]))
        self.app.refresh()
        self.assertIn('key:293', self.app.dfgt_art.active)
        self.assertIn('key:301', self.app.dfgt_art.active)
        self.assertEqual(self.app.g29_art.active, {'button:6'})
        self.assertEqual(len(self.app.source_keys), 2)

    def test_readonly_monitor_and_detection_are_explicit_and_pulses_not_lost(self):
        self.app.detect()
        self.factory.assert_not_called()
        wheel = SimpleNamespace(read_frames=lambda: [], state=lambda: (8192, 255, 255, 0, 0, []),
                                observed_events=[(1, 704, 1), (1, 704, 0)], close=Mock())
        self.factory.return_value = wheel
        self.app.connect();self.app.detect_dial();self.app.poll()
        self.assertEqual(self.app.input_code.get(), 'key:704')
        self.assertEqual(self.app.dfgt_art.detected, 'key:704')
        self.assertIn('704', self.app.detected.get())
        self.popen.assert_not_called()
        self.app.disconnect();wheel.close.assert_called_once()

    def test_disconnect_clears_preview_and_cancels_detection(self):
        wheel = SimpleNamespace(read_frames=Mock(side_effect=EOFError('unplugged')), close=Mock())
        self.factory.return_value = wheel
        self.app.connect();self.app.editor.detect();self.app.poll()
        self.assertIsNone(self.app.wheel)
        self.assertFalse(self.app.editor.detecting)
        self.assertEqual(self.app.dfgt_art.active, set())
        wheel.close.assert_called_once()

    def test_ui_moving_source_requires_conflict_decision_and_undo_restores(self):
        self.app.select('button:1');self.app.input_code.set('293')
        with patch.object(gui.messagebox, 'askyesno', return_value=False):
            self.app.add()
        self.assertEqual(self.app.editor.profile['mapping']['bindings']['key:293'], 'button:6')
        with patch.object(gui.messagebox, 'askyesno', return_value=True):
            self.app.add()
        self.assertEqual(self.app.editor.profile['mapping']['bindings']['key:293'], 'button:1')
        self.app.history(False)
        self.assertEqual(self.app.editor.profile['mapping']['bindings']['key:293'], 'button:6')

    def test_dial_swap_and_undo_keep_click_independent(self):
        self.app.swap_dial()
        bindings = self.app.editor.profile['mapping']['bindings']
        self.assertEqual((bindings['key:704'], bindings['key:705'], bindings['key:302']), ('button:23', 'button:22', 'button:24'))
        self.app.history(False)
        self.assertEqual(self.app.editor.profile['mapping']['bindings']['key:704'], 'button:22')

    def test_presets_save_load_and_gears_destination_widgets_match_profile(self):
        self.app.gear_down.set('Marcha 1');self.app.gear_up.set('Marcha 2')
        self.app.preset.set('Marchas prontas');self.app.apply_preset()
        self.assertTrue(self.app.save())
        self.app.restore()
        self.app.load()
        self.assertEqual(self.app.gear_down.get(), 'Marcha 1')
        self.assertEqual(self.app.gear_up.get(), 'Marcha 2')
        self.assertEqual(self.app.editor.profile['mapping']['bindings']['key:293'], 'button:13')
        self.assertIn('conflitos', self.app.notes.get())

    def test_invalid_form_does_not_save_or_spawn(self):
        self.app.form['ffb_strength'].set('101')
        self.app.start(True)
        self.popen.assert_not_called()
        self.assertFalse(self.store.path.exists())
        self.errors.assert_called_once()

    def test_physical_start_is_explicit_uses_shared_profile_and_no_capture(self):
        stdin, stdout = Mock(), Mock()
        stdin.closed = False;stdin.fileno.return_value = 71;stdout.fileno.return_value = 72
        self.popen.return_value = SimpleNamespace(stdin=stdin, stdout=stdout, poll=lambda: None)
        with patch.object(gui.os, 'set_blocking'):
            self.app.start(True)
        command = self.popen.call_args.args[0]
        self.assertIn('--enable-ffb', command)
        self.assertIn('--control-stdin', command)
        self.assertIn(str(self.store.path), command)
        self.assertNotIn('--capture', command)
        self.assertEqual(self.store.load(), default_profile())
        with patch.object(gui.os, 'write') as write:
            self.app.stop()
        write.assert_called_once_with(71, b'ffb-stop\nquit\n')
        stdin.close.assert_called_once()

    def test_runner_commands_are_argv_not_shell_and_passive_has_no_ffb(self):
        path = Path(self.directory)/'profile with spaces.json'
        passive = gui.runner_command(path, profile='my game')
        active = gui.runner_command(path, physical=True, elevated=True)
        self.assertNotIn('--enable-ffb', passive)
        self.assertEqual(active[0], 'pkexec')
        self.assertIn(str(path), active)
        self.assertIn('my game', passive)
        self.assertTrue(Path(passive[2]).exists())

    def test_closing_offline_gui_does_not_start_anything(self):
        self.app.close()
        self.popen.assert_not_called()
        self.factory.assert_not_called()
        self.assertTrue(self.root.destroyed)
