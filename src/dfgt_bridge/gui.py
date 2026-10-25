# SPDX-License-Identifier: GPL-2.0-only
"""Native Tk interface for shared profiles, mapping, diagnostics, and supervised FFB."""
import argparse
import copy
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .config import ProfileStore, duration, default_profile, validate_profile
from .i18n import (LOCALES, translate, target_key, source_label, localized_error)
from .mapping_editor import MappingEditor, MappingConflict
from .input.mapping import TARGET_NAMES, PRESETS
from .input.physical_wheel import PhysicalWheel, SOURCE
from .wheel_art import WheelArt
from .steam_launcher import IntegrationSettings
from .steam_protocol import ControlClient, SOCKET
from .ui_preferences import LocaleStore


def runner_command(profiles_file, profile='dirt5', source=SOURCE, *, physical=False, elevated=False):
    script = Path(__file__).with_name('main.py')
    command = [sys.executable, '-B', str(script), '--profiles-file', str(Path(profiles_file).absolute()),
               '--profile', profile, '--source', source, '--control-stdin']
    if physical:
        command.append('--enable-ffb')
    return ['pkexec', *command] if elevated else command


class BridgeGUI:
    def __init__(self, root, store, profile='dirt5', wheel_factory=PhysicalWheel, *, locale_store=None):
        self.root, self.store, self.wheel_factory = root, store, wheel_factory
        self.locale_store = locale_store or LocaleStore(store.path.with_name('ui.json'))
        self.locale = self.locale_store.load()
        self._text_widgets, self._notebook_tabs = [], []
        self.editor = MappingEditor(store.load(profile))
        self.active_profile = profile
        self.saved_profile = copy.deepcopy(self.editor.profile)
        self.saved_profile_exists = True
        self.wheel = self.process = None
        self.closing = False
        self.profile_name = tk.StringVar(value=profile)
        self.source = tk.StringVar(value=SOURCE)
        self.elevated = tk.BooleanVar(value=False)
        self.auto_start = tk.BooleanVar(value=self._integration().enabled())
        self.language = tk.StringVar(value=self.t('language.'+('en' if self.locale == 'en' else 'pt_br')))
        self.status_key, self.status_params = 'diagnostics.offline', {}
        self.service_state = None
        self.detected_source = None
        self.status = tk.StringVar(value=self.t('diagnostics.offline'))
        self.integration_state = tk.StringVar(value=self.t('home.service_missing'))
        self.detected = tk.StringVar(value=self.t('mapping.detected_none'))
        self.form, self.axis_form, self._trace_vars = {}, {}, []
        self.root.title(self.t('app.title'))
        self.root.minsize(1000, 700)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.apply_theme()
        self.build_shell(profile)
        self.sync_form()
        self._refresh_service_text()
        self.refresh()
        self.update_dirty()
        self.root.after(16, self.poll)

    def apply_theme(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', background='#111923', foreground='#edf3f8', font=('TkDefaultFont', 10))
        style.configure('TFrame', background='#111923')
        style.configure('TLabel', background='#111923', foreground='#d8e1ea')
        style.configure('Accent.TLabel', foreground='#74d9c2', font=('TkDefaultFont', 10, 'bold'))
        style.configure('TLabelFrame', background='#18222e', bordercolor='#354455')
        style.configure('TLabelFrame.Label', background='#18222e', foreground='#74d9c2')
        style.configure('TButton', background='#263646', foreground='#f1f5f9', padding=(9, 6))
        style.map('TButton', background=[('active', '#344a5d'), ('disabled', '#1b2732')],
                  foreground=[('disabled', '#7d8a96')])
        style.configure('TEntry', fieldbackground='#1c2936', foreground='#f2f5f8', insertcolor='#ffffff')
        style.configure('TCombobox', fieldbackground='#1c2936', foreground='#f2f5f8', background='#263646')
        style.map('TCombobox', fieldbackground=[('readonly', '#1c2936')], foreground=[('readonly', '#f2f5f8')])
        style.configure('TNotebook', background='#111923', borderwidth=0)
        style.configure('TNotebook.Tab', background='#202e3b', foreground='#b9c5d1', padding=(13, 8))
        style.map('TNotebook.Tab', background=[('selected', '#34515b')], foreground=[('selected', '#ffffff')])
        style.configure('TCheckbutton', background='#111923', foreground='#d8e1ea')
        style.map('TCheckbutton', foreground=[('active', '#ffffff')])

    def t(self, key, **params):
        return translate(key, self.locale, **params)

    def _integration(self):
        return IntegrationSettings(self.store.path.with_name('integration.json'))

    def label(self, parent, key, **kwargs):
        widget = ttk.Label(parent, text=self.t(key), **kwargs)
        self._text_widgets.append((widget, key))
        return widget

    def button(self, parent, key, command, **kwargs):
        widget = ttk.Button(parent, text=self.t(key), command=command, **kwargs)
        self._text_widgets.append((widget, key))
        return widget

    def checkbutton(self, parent, key, variable, command, **kwargs):
        widget = ttk.Checkbutton(parent, text=self.t(key), variable=variable, command=command, **kwargs)
        self._text_widgets.append((widget, key))
        return widget

    def tab(self, frame, key):
        self.tabs.add(frame, text=self.t(key))
        self._notebook_tabs.append((frame, key))

    def _variable(self, value='', *, boolean=False):
        variable = tk.BooleanVar(value=value) if boolean else tk.StringVar(value=value)
        if not boolean and hasattr(variable, 'trace_add'):
            variable.trace_add('write', lambda *_: self.update_dirty())
        if not boolean:
            self._trace_vars.append(variable)
        return variable

    def language_labels(self):
        return [self.t('language.en'), self.t('language.pt_br')]

    def target_labels(self):
        return [self.t(target_key(key)) for key in TARGET_NAMES]

    def target_for_label(self, label):
        return next(key for key in TARGET_NAMES if self.t(target_key(key)) == label)

    def preset_labels(self):
        return [self.t('preset.'+key) for key in PRESETS]

    def preset_for_label(self, label):
        return next(key for key in PRESETS if self.t('preset.'+key) == label)

    def build_shell(self, profile):
        bar = ttk.Frame(self.root, padding=(14, 10));bar.pack(fill='x')
        self.label(bar, 'profile.label').pack(side='left')
        names = sorted(self.store.read())
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile_name, values=names, width=20)
        self.profile_box.pack(side='left', padx=7)
        self.profile_box.bind('<Return>', lambda _e: self.load())
        self.profile_box.bind('<<ComboboxSelected>>', lambda _e: self.load())
        self.load_button = self.button(bar, 'profile.load', self.load);self.load_button.pack(side='left')
        self.button(bar, 'profile.manage', self.manage_profiles).pack(side='left', padx=5)
        self.save_button = self.button(bar, 'profile.save', self.save);self.save_button.pack(side='left', padx=(7, 14))
        self.undo_button = self.button(bar, 'profile.undo', lambda: self.history(False));self.undo_button.pack(side='left')
        self.redo_button = self.button(bar, 'profile.redo', lambda: self.history(True));self.redo_button.pack(side='left', padx=5)
        self.label(bar, 'language.label').pack(side='right', padx=(8, 4))
        self.language_box = ttk.Combobox(bar, textvariable=self.language, values=self.language_labels(), state='readonly', width=18)
        self.language_box.pack(side='right')
        self.language_box.bind('<<ComboboxSelected>>', self.change_language)
        self.tabs = ttk.Notebook(self.root);self.tabs.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        self.home = ttk.Frame(self.tabs, padding=20);self.mapping = ttk.Frame(self.tabs, padding=8)
        self.settings = ttk.Frame(self.tabs, padding=18);self.diagnostics = ttk.Frame(self.tabs, padding=18)
        self.tab(self.home, 'nav.home');self.tab(self.mapping, 'nav.mapping')
        self.tab(self.settings, 'nav.settings');self.tab(self.diagnostics, 'nav.diagnostics')
        self.build_home(profile);self.build_setup(self.settings);self.build_mapping(self.mapping);self.build_diagnostics(self.diagnostics)
        self.status_label = ttk.Label(self.root, textvariable=self.status, wraplength=1250, padding=(14, 8), anchor='w')
        self.status_label.pack(fill='x')
        self.tabs.select(self.home)

    def build_home(self, frame):
        self.label(frame, 'home.eyebrow', style='Accent.TLabel').pack(anchor='w')
        self.label(frame, 'home.title', font=('', 22, 'bold')).pack(anchor='w', pady=(3, 7))
        self.label(frame, 'home.intro', wraplength=1000).pack(anchor='w', pady=(0, 18))
        cards = ttk.Frame(frame);cards.pack(fill='x', pady=5)
        overview = ttk.LabelFrame(cards, text=self.t('home.service'), padding=14);overview.pack(side='left', fill='both', expand=True, padx=(0, 8))
        self._text_widgets.append((overview, 'home.service'))
        self.profile_summary = tk.StringVar()
        self.ffb_summary = tk.StringVar()
        ttk.Label(overview, textvariable=self.profile_summary, font=('', 12, 'bold')).pack(anchor='w', pady=4)
        ttk.Label(overview, textvariable=self.ffb_summary).pack(anchor='w', pady=4)
        self.service_summary = tk.StringVar(value=self.integration_state.get())
        ttk.Label(overview, textvariable=self.service_summary, wraplength=600).pack(anchor='w', pady=8)
        actions = ttk.LabelFrame(cards, text=self.t('home.service'), padding=14);actions.pack(side='left', fill='both', expand=True, padx=(8, 0))
        self._text_widgets.append((actions, 'home.service'))
        self.checkbutton(actions, 'home.auto_start', self.auto_start, self.set_auto_start).pack(anchor='w', pady=5)
        self.button(actions, 'home.check', self.check_service).pack(anchor='w', pady=5)
        self.button(actions, 'home.open_mapping', lambda: self.tabs.select(self.mapping)).pack(anchor='w', pady=5)
        self.button(actions, 'home.open_settings', lambda: self.tabs.select(self.settings)).pack(anchor='w', pady=5)
        self.label(frame, 'home.launch_option', wraplength=1000).pack(anchor='w', pady=(22, 10))
        self.label(frame, 'diagnostics.no_motor', wraplength=1000).pack(anchor='w')

    def build_setup(self, frame):
        self.label(frame, 'settings.heading', font=('', 19, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 7))
        self.label(frame, 'settings.identity').grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 14))
        self.label(frame, 'settings.strength').grid(row=2, column=0, sticky='w', pady=5)
        self.form['ffb_strength'] = self._variable()
        ttk.Entry(frame, textvariable=self.form['ffb_strength'], width=24).grid(row=2, column=1, sticky='w')
        self.label(frame, 'settings.input').grid(row=3, column=0, sticky='w', pady=8)
        if hasattr(self.source, 'trace_add'):
            self.source.trace_add('write', lambda *_: self.update_dirty())
        ttk.Entry(frame, textvariable=self.source, width=65).grid(row=3, column=1, columnspan=2, sticky='we')
        self.checkbutton(frame, 'settings.elevated', self.elevated, lambda: None).grid(row=4, column=0, columnspan=3, sticky='w')
        row = ttk.Frame(frame);row.grid(row=5, column=0, columnspan=3, sticky='w', pady=12)
        self.button(row, 'settings.passive_start', lambda: self.start(False)).pack(side='left')
        self.button(row, 'settings.physical_start', lambda: self.start(True)).pack(side='left', padx=7)
        self.button(row, 'settings.stop', self.stop).pack(side='left', padx=7)
        self.button(row, 'settings.copy', self.copy_command).pack(side='left')
        self.label(frame, 'settings.safety', wraplength=1050).grid(row=6, column=0, columnspan=3, sticky='w', pady=(6, 12))
        advanced = ttk.LabelFrame(frame, text=self.t('settings.advanced'), padding=10);advanced.grid(row=7, column=0, columnspan=3, sticky='ew', pady=5)
        self._text_widgets.append((advanced, 'settings.advanced'))
        advanced_fields = (('ffb_max_strength', 'settings.maximum'), ('ffb_polarity', 'settings.polarity'),
                           ('ffb_slew_rate', 'settings.slew'), ('ffb_seconds', 'settings.duration'))
        for column, (name, key) in enumerate(advanced_fields):
            self.label(advanced, key).grid(row=0, column=column, sticky='w', padx=5, pady=(0, 4))
            self.form[name] = self._variable()
            ttk.Entry(advanced, textvariable=self.form[name], width=15).grid(row=1, column=column, sticky='we', padx=5)
        self.label(advanced, 'settings.axes_title').grid(row=2, column=0, columnspan=4, sticky='w', pady=(14, 6))
        for col, (name, key) in enumerate((('steering', 'settings.steering'), ('accelerator', 'settings.accelerator'), ('brake', 'settings.brake'))):
            cell = ttk.Frame(advanced);cell.grid(row=3, column=col, padx=5, sticky='w')
            self.label(cell, key).pack(side='left')
            source, inverse = self._variable(), self._variable(False, boolean=True)
            self.axis_form[name] = source, inverse
            ttk.Combobox(cell, textvariable=source, values=('ABS_X', 'ABS_Y', 'ABS_Z'), state='readonly', width=8).pack(side='left', padx=4)
            self.checkbutton(cell, 'settings.invert', inverse, self.update_dirty).pack(side='left')
        self.button(advanced, 'settings.apply_axes', self.apply_axes).grid(row=3, column=3, padx=8)
        self.form['gas_axis'], self.form['invert_gas'] = self._variable(), self._variable(False, boolean=True)
        self.label(advanced, 'settings.gas_channel').grid(row=4, column=0, sticky='e', pady=(10, 0))
        ttk.Combobox(advanced, textvariable=self.form['gas_axis'], values=('y', 'z', 'both'), state='readonly', width=8).grid(row=4, column=1, sticky='w', pady=(10, 0))
        self.checkbutton(advanced, 'settings.extra_invert', self.form['invert_gas'], self.update_dirty).grid(row=4, column=2, columnspan=2, sticky='w', pady=(10, 0))
        for name in ('gas_axis',):
            self.form[name].trace_add('write', lambda *_: self.update_dirty())
        frame.columnconfigure(2, weight=1)

    def build_mapping(self, frame):
        toolbar = ttk.Frame(frame);toolbar.pack(fill='x', pady=(0, 8))
        self.preset = self._variable()
        self.preset_box = ttk.Combobox(toolbar, textvariable=self.preset, values=self.preset_labels(), state='readonly', width=18)
        self.preset_box.pack(side='left')
        self.button(toolbar, 'mapping.apply', self.apply_preset).pack(side='left', padx=4)
        self.button(toolbar, 'mapping.restore', self.restore).pack(side='left')
        self.button(toolbar, 'mapping.monitor', self.connect).pack(side='left', padx=8)
        self.button(toolbar, 'mapping.stop_monitor', self.disconnect).pack(side='left')
        body = ttk.Frame(frame);body.pack(fill='both', expand=True)
        wheels = ttk.Frame(body);wheels.pack(side='left', fill='both', expand=True)
        for col, name in enumerate(('dfgt', 'g29')):
            canvas = tk.Canvas(wheels, width=360, height=350, bg='#111923', highlightthickness=0)
            canvas.grid(row=0, column=col, sticky='nsew', padx=3, pady=3)
            wheels.columnconfigure(col, weight=1)
            art = WheelArt(canvas, name, self.physical_select if name == 'dfgt' else self.select, self.t)
            setattr(self, name+'_art', art)
        wheels.rowconfigure(0, weight=1)
        side = ttk.Frame(body);side.pack(side='right', fill='y')
        scroll = tk.Canvas(side, width=290, height=390, highlightthickness=0)
        scrollbar = ttk.Scrollbar(side, orient='vertical', command=scroll.yview)
        scroll.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y');scroll.pack(side='left', fill='y', expand=True)
        detail = ttk.Frame(scroll, padding=8);scroll.create_window(0, 0, window=detail, anchor='nw')
        detail.bind('<Configure>', lambda event: scroll.configure(scrollregion=scroll.bbox('all')))
        self.label(detail, 'mapping.target').pack(anchor='w')
        self.target = self._variable()
        self.target_box = ttk.Combobox(detail, textvariable=self.target, values=self.target_labels(), state='readonly', width=24)
        self.target_box.pack(fill='x')
        self.target_box.bind('<<ComboboxSelected>>', lambda _e: self.select(self.target_for_label(self.target.get())))
        self.sources = tk.Listbox(detail, height=5, width=31, exportselection=False);self.sources.pack(fill='x', pady=6)
        self.sources.bind('<<ListboxSelect>>', self.select_source_row)
        self.button(detail, 'mapping.remove', self.remove).pack(fill='x')
        self.detected = self._variable(self.t('mapping.detected_none'))
        self.button(detail, 'mapping.detect', self.detect).pack(fill='x', pady=5)
        self.detected_label = ttk.Label(detail, textvariable=self.detected, wraplength=265);self.detected_label.pack(fill='x')
        self.input_code = self._variable()
        ttk.Entry(detail, textvariable=self.input_code).pack(fill='x', pady=4)
        self.button(detail, 'mapping.add', self.add).pack(fill='x')
        self.button(detail, 'mapping.dial_check', self.detect_dial).pack(fill='x', pady=4)
        self.button(detail, 'mapping.dial_swap', self.swap_dial).pack(fill='x')
        self.label(detail, 'mapping.gears').pack(anchor='w', pady=4)
        self.gear_down, self.gear_up = self._variable(self.t('target.button.2')), self._variable(self.t('target.button.1'))
        self.gear_boxes = []
        for value in (self.gear_down, self.gear_up):
            combo = ttk.Combobox(detail, textvariable=value, values=self.target_labels(), state='readonly', width=24)
            combo.pack(fill='x');self.gear_boxes.append(combo)
        self.notes = self._variable()
        ttk.Label(detail, textvariable=self.notes, wraplength=270).pack(fill='x', pady=8)
        self.button(detail, 'mapping.conflicts', self.show_conflicts).pack(fill='x')
        self.label(frame, 'mapping.axis_title').pack(anchor='w', pady=(8, 3))
        self.live = self._variable()
        self.live_label = ttk.Label(frame, textvariable=self.live, wraplength=1160);self.live_label.pack(fill='x', pady=5)

    def build_diagnostics(self, frame):
        self.label(frame, 'diagnostics.title', font=('', 19, 'bold')).pack(anchor='w', pady=(0, 8))
        self.label(frame, 'diagnostics.intro', wraplength=1100).pack(anchor='w', pady=(0, 7))
        self.diagnostic_state = tk.StringVar(value=self.t('diagnostics.not_checked'))
        ttk.Label(frame, textvariable=self.diagnostic_state, wraplength=1100).pack(anchor='w', pady=6)
        self.label(frame, 'diagnostics.no_motor').pack(anchor='w', pady=(0, 10))
        self.log = tk.Text(frame, height=24, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)

    def change_language(self, _event=None):
        selected_label = self.language.get()
        selected = next((code for code in LOCALES if self.t('language.'+('en' if code == 'en' else 'pt_br')) == selected_label), None)
        if selected not in LOCALES or selected == self.locale:
            return
        self.locale_store.save(selected)
        self.locale = selected
        self.language.set(self.t('language.'+('en' if selected == 'en' else 'pt_br')))
        self.language_box.configure(values=self.language_labels())
        self.root.title(self.t('app.title'))
        for widget, key in self._text_widgets:
            try: widget.configure(text=self.t(key))
            except tk.TclError: pass
        for frame, key in self._notebook_tabs:
            self.tabs.tab(frame, text=self.t(key))
        self.preset_box.configure(values=self.preset_labels())
        self.dfgt_art.translate = self.t
        self.g29_art.translate = self.t
        self.dfgt_art.draw(self.dfgt_art.canvas.winfo_width(), self.dfgt_art.canvas.winfo_height())
        self.g29_art.draw(self.g29_art.canvas.winfo_width(), self.g29_art.canvas.winfo_height())
        self.target_box.configure(values=self.target_labels())
        for combo in self.gear_boxes:
            combo.configure(values=self.target_labels())
        self.sync_form()
        self.update_home_summary()
        self.refresh()
        self.update_dirty()
        if self.detected_source:
            self.detected.set(source_label(self.detected_source, self.locale))
        elif self.editor.detecting:
            self.detected.set(self.t('mapping.waiting'))
        else:
            self.detected.set(self.t('mapping.detected_none'))
        self._refresh_service_text()
        self.set_status(self.status_key, **self.status_params)

    def _warning(self, raw):
        if raw.startswith('EV_KEY 300/301:'):
            return self.t('warning.shared_codes')
        if raw.startswith('Dial 704/705:'):
            return self.t('warning.dial')
        if ': estado agregado de ' in raw:
            name, details = raw.split(': estado agregado de ', 1)
            target = next((key for key in TARGET_NAMES if TARGET_NAMES[key] == name), None)
            return self.t('warning.aggregate', target=self.t(target_key(target)) if target else name,
                          sources=details.removesuffix(' (OR).'))
        if ' alimenta vários eixos: ' in raw:
            axis, targets = raw.split(' alimenta vários eixos: ', 1)
            return self.t('warning.axes', axis=axis.removeprefix('ABS_'), targets=targets)
        return raw

    def _candidate(self):
        value = copy.deepcopy(self.editor.profile)
        for name, variable in self.form.items():
            raw = variable.get()
            value[name] = (duration(raw) if name == 'ffb_seconds' else float(raw) if name == 'ffb_slew_rate'
                           else int(raw) if name.startswith('ffb_') else raw)
        for name, (source, inverse) in self.axis_form.items():
            value['mapping']['axes'][name] = dict(source=('ABS_X', 'ABS_Y', 'ABS_Z').index(source.get()), invert=inverse.get())
        if value['mapping'] != self.editor.profile['mapping']:
            value['preset'] = 'custom'
        return validate_profile(value)

    def is_dirty(self):
        try: return self._candidate() != self.saved_profile or not self.saved_profile_exists
        except (ValueError, TypeError): return True

    def update_dirty(self):
        if not hasattr(self, 'save_button'):
            return
        self.save_button.configure(state='normal' if self.is_dirty() else 'disabled')

    def set_status(self, key, **params):
        self.status_key, self.status_params = key, params
        self.status.set(self.t(key, **params))

    def error(self, exc):
        detail = localized_error(str(exc), self.locale)
        self.set_status('error.operation', detail=detail)
        messagebox.showerror(self.t('error.title'), self.t('error.operation', detail=detail), parent=self.root)

    def set_auto_start(self):
        try:
            self._integration().save(self.auto_start.get())
            self.integration_state.set(self.t('home.preference_saved'))
            self.service_summary.set(self.integration_state.get())
        except (OSError, ValueError) as exc:
            self.error(exc)

    def check_service(self):
        try:
            client = ControlClient(timeout=.5)
            try: value = client.request('status')
            finally: client.close()
            self.service_state = ('ready', value['state'], value.get('error'))
            self._refresh_service_text()
        except (OSError, EOFError, RuntimeError, ValueError) as exc:
            self.service_state = ('error', str(exc), SOCKET)
            self._refresh_service_text()

    def _refresh_service_text(self):
        if self.service_state is None:
            installed = Path('/var/lib/dfgt-bridge/install.json').is_file()
            message = self.t('home.service_missing' if not installed else 'diagnostics.not_checked')
        elif self.service_state[0] == 'ready':
            _, state, detail = self.service_state
            message = self.t('home.service_ready', state=state,
                ready=self.t('home.ready_suffix') if state == 'ready' else '',
                error=self.t('home.error_suffix', error=detail) if detail else '')
        else:
            _, detail, path = self.service_state
            message = self.t('home.service_unavailable', error=detail, path=path)
        self.integration_state.set(message)
        self.service_summary.set(message)
        self.diagnostic_state.set(message)

    def update_home_summary(self):
        self.profile_summary.set(self.t('home.profile_summary', profile=self.profile_name.get()))
        duration_text = self.editor.profile['ffb_seconds']
        duration_text = 'unlimited' if duration_text is None else str(duration_text)+' s'
        self.ffb_summary.set(self.t('home.ffb_summary', strength=self.editor.profile['ffb_strength'],
                                    polarity=self.editor.profile['ffb_polarity'], duration=duration_text))

    def sync_form(self):
        profile = self.editor.profile
        for name, variable in self.form.items():
            value = profile[name]
            variable.set('unlimited' if name == 'ffb_seconds' and value is None else value)
        self.preset.set(self.t('preset.'+profile['preset']))
        if profile['preset'] == 'gears':
            self.gear_down.set(self.t(target_key(profile['mapping']['bindings'].get('key:293', 'button:2'))))
            self.gear_up.set(self.t(target_key(profile['mapping']['bindings'].get('key:292', 'button:1'))))
        for name, (source, inverse) in self.axis_form.items():
            axis = profile['mapping']['axes'][name]
            source.set('ABS_'+'XYZ'[axis['source']]);inverse.set(axis['invert'])

    def collect(self):
        self.editor.commit(self._candidate())
        self.update_dirty()

    def load(self):
        name = self.profile_name.get().strip()
        if not name:
            self.set_status('status.profile_required');return
        if name != getattr(self, 'active_profile', None) and self.is_dirty() and self.saved_profile_exists:
            if not messagebox.askyesno(self.t('error.title'), self.t('profile.unsaved'), parent=self.root):
                self.profile_name.set(self.editor.profile.get('name', self.profile_name.get()))
                return
        try:
            exists = name in self.store.read()
            self.editor = MappingEditor(self.store.load(name) if exists else default_profile())
            self.saved_profile = copy.deepcopy(self.editor.profile);self.saved_profile_exists = exists
            self.active_profile = name
            self.profile_box.configure(values=sorted(set(self.store.read()) | {name}))
            self.sync_form();self.refresh();self.update_home_summary();self.update_dirty()
            self.set_status('profile.loaded')
        except (ValueError, OSError) as exc:
            self.error(exc)

    def manage_profiles(self):
        dialog = tk.Toplevel(self.root)
        dialog.title(self.t('profile.manager.title'))
        dialog.transient(self.root)
        dialog.geometry('430x360')
        ttk.Label(dialog, text=self.t('profile.manager.title'), font=('', 15, 'bold')).pack(anchor='w', padx=14, pady=(12, 7))
        profiles = tk.Listbox(dialog, exportselection=False, height=12)
        profiles.pack(fill='both', expand=True, padx=14, pady=5)
        for name in sorted(self.store.read()):
            profiles.insert('end', name)
        if hasattr(self, 'active_profile'):
            names = list(profiles.get(0, 'end'))
            if self.active_profile in names:
                profiles.selection_set(names.index(self.active_profile))

        def selected():
            selection = profiles.curselection()
            return profiles.get(selection[0]) if selection else self.profile_name.get().strip()

        def ask_name():
            return simpledialog.askstring(self.t('profile.manager.name'), self.t('profile.manager.name_prompt'), parent=dialog)

        def new_profile():
            name = ask_name()
            if name:
                self.profile_name.set(name)
                self.load()
                dialog.destroy()

        def duplicate_profile():
            name = ask_name()
            if not name:
                return
            try:
                snapshot = self._candidate()
                self.store.name(name)
                if name in self.store.read():
                    raise ValueError(self.t('error.profile_exists'))
                self.editor = MappingEditor(snapshot)
                self.saved_profile = copy.deepcopy(default_profile())
                self.saved_profile_exists = False
                self.active_profile = name
                self.profile_name.set(name)
                self.sync_form();self.refresh()
                self.set_status('profile.manager.duplicate_done')
                dialog.destroy()
            except (ValueError, OSError) as exc:
                self.error(exc)

        def rename_profile():
            old_name = selected()
            new_name = ask_name()
            if not old_name or not new_name:
                return
            try:
                if old_name == self.active_profile and not self.save():
                    return
                self.store.rename_profile(old_name, new_name)
                if old_name == self.active_profile:
                    self.active_profile = new_name
                    self.profile_name.set(new_name)
                self.profile_box.configure(values=sorted(self.store.read()))
                self.update_home_summary()
                self.set_status('profile.manager.renamed')
                dialog.destroy()
            except (ValueError, OSError) as exc:
                self.error(exc)

        def delete_profile():
            name = selected()
            if not name or not messagebox.askyesno(self.t('profile.manager.title'),
                    self.t('profile.manager.delete_confirm', profile=name), parent=dialog):
                return
            try:
                self.store.delete_profile(name)
                if name == self.active_profile:
                    self.profile_name.set('dirt5')
                    self.editor = MappingEditor(self.store.load('dirt5'))
                    self.saved_profile = copy.deepcopy(self.editor.profile)
                    self.saved_profile_exists = 'dirt5' in self.store.read()
                    self.active_profile = 'dirt5'
                    self.sync_form();self.refresh()
                self.profile_box.configure(values=sorted(self.store.read()))
                self.set_status('profile.manager.deleted')
                dialog.destroy()
            except (ValueError, OSError) as exc:
                self.error(exc)

        actions = ttk.Frame(dialog);actions.pack(fill='x', padx=14, pady=(4, 12))
        for key, callback in (('profile.manager.new', new_profile), ('profile.manager.duplicate', duplicate_profile),
                              ('profile.manager.rename', rename_profile), ('profile.manager.delete', delete_profile),
                              ('profile.manager.close', dialog.destroy)):
            self.button(actions, key, callback).pack(side='left', padx=(0, 4))

    def save(self):
        if not self.is_dirty():
            return True
        name = self.profile_name.get().strip()
        if not name:
            self.set_status('status.profile_required');return False
        try:
            self.collect()
            self.store.save(name, self.editor.profile)
            self.saved_profile = copy.deepcopy(self.editor.profile);self.saved_profile_exists = True
            self.profile_box.configure(values=sorted(self.store.read()))
            self.update_home_summary();self.update_dirty()
            self.set_status('profile.saved')
            return True
        except (ValueError, OSError) as exc:
            self.error(exc)
            return False

    def refresh(self):
        if not hasattr(self, 'sources'):
            return
        target_id = self.editor.selected
        self.target.set(self.t(target_key(target_id)))
        self.source_keys = self.editor.sources()
        self.sources.delete(0, 'end')
        for source in self.source_keys:
            self.sources.insert('end', source_label(source, self.locale))
        self.preset.set(self.t('preset.'+self.editor.profile['preset']))
        self.preset_box.configure(values=self.preset_labels())
        value = self.editor.preview()
        pulse = value['last_pulse'] or {}
        self.dfgt_art.highlight(value['physical'], detected=self.editor.detected or pulse.get('source'))
        self.g29_art.highlight(value['virtual'], target_id, pulse.get('target'))
        messages = [self._warning(x) for x in [self.editor.notice, *value['warnings']] if x]
        self.notes.set('\n'.join(dict.fromkeys(messages)).strip())
        self.live.set(self.t('mapping.preview', physical=', '.join(source_label(x, self.locale) for x in value['physical']),
            virtual=', '.join(self.t(target_key(x)) for x in value['virtual']), physical_axes=value['physical_axes'],
            hid_axes=value['hid_axes'], report=value['report'], source=source_label(pulse.get('source', ''), self.locale) if pulse.get('source') else '—',
            target=self.t(target_key(pulse['target'])) if pulse.get('target') else '—'))
        self.update_home_summary();self.update_dirty()

    def select_source_row(self, _event=None):
        selection = self.sources.curselection()
        if selection:
            self.set_status('status.source_selected', source=source_label(self.source_keys[selection[0]], self.locale))

    def select(self, target):
        if target.startswith('axis:'):
            self.set_status('mapping.axis_help');return
        self.editor.select(target);self.refresh()

    def physical_select(self, source):
        if source.startswith('axis:'):
            return self.select(source)
        self.detected_source = source
        self.editor.detected = source
        self.input_code.set(source)
        self.detected.set(source_label(source, self.locale));self.refresh()

    def add(self):
        source = self.input_code.get().strip()
        source = 'key:'+source if source.isdigit() else source
        try:
            try: self.editor.bind(source)
            except MappingConflict as exc:
                if not messagebox.askyesno(self.t('mapping.move_title'), str(exc), parent=self.root): return
                self.editor.bind(source, replace=True)
            self.refresh()
        except ValueError as exc:
            self.error(exc)

    def remove(self):
        selection = self.sources.curselection()
        if selection:
            self.editor.remove(self.source_keys[selection[0]]);self.refresh()

    def detect(self):
        if self.wheel is None:
            self.set_status('mapping.monitor_help');return
        self.editor.detect();self.detected.set(self.t('mapping.waiting'))

    def detect_dial(self):
        self.select('button:22');self.detect()
        self.set_status('mapping.dial_instructions')

    def apply_preset(self):
        try:
            key = self.preset_for_label(self.preset.get())
            self.collect()
            self.editor.apply_preset(key, down=self.target_for_label(self.gear_down.get()), up=self.target_for_label(self.gear_up.get()))
            self.sync_form();self.refresh();self.set_status('mapping.preset_applied')
        except (ValueError, StopIteration) as exc:
            self.error(exc)

    def restore(self):
        try:
            self.collect();self.editor.apply_preset('g29');self.sync_form();self.refresh();self.set_status('mapping.restored')
        except ValueError as exc:
            self.error(exc)

    def swap_dial(self):
        try: self.editor.swap_dial();self.refresh()
        except ValueError as exc: self.error(exc)

    def show_conflicts(self):
        warnings = '\n'.join(self._warning(x) for x in self.editor.preview()['warnings'])
        messagebox.showinfo(self.t('mapping.shared_title'), warnings, parent=self.root)

    def history(self, redo):
        self.editor.redo() if redo else self.editor.undo()
        self.sync_form();self.refresh()

    def apply_axes(self):
        try: self.collect();self.refresh()
        except ValueError as exc: self.error(exc)

    def connect(self):
        if self.wheel is not None: return
        try:
            self.wheel = self.wheel_factory(self.source.get())
            self.set_status('mapping.monitoring')
        except (OSError, ValueError) as exc: self.error(exc)

    def disconnect(self):
        if self.wheel:
            self.wheel.close();self.wheel = None
        if hasattr(self, 'editor'):
            self.editor.detecting = False;self.editor.last_pulse = None
            self.editor.observe((*self.editor.state[:3], 0, 0, []))
            self.detected_source = None
            if hasattr(self, 'detected'): self.detected.set(self.t('mapping.detected_none'))
            if hasattr(self, 'dfgt_art'): self.refresh()

    def command(self, physical):
        return runner_command(self.store.path, self.profile_name.get(), self.source.get(),
                              physical=physical, elevated=self.elevated.get())

    def copy_command(self):
        if self.save():
            command = [part for part in self.command(True) if part != '--control-stdin']
            self.root.clipboard_clear();self.root.clipboard_append(shlex.join(command))
            self.set_status('status.command_copied')

    def start(self, physical):
        if self.process is not None:
            self.set_status('status.already_running');return
        if not self.save(): return
        try:
            self.process = subprocess.Popen(self.command(physical), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            os.set_blocking(self.process.stdout.fileno(), False);os.set_blocking(self.process.stdin.fileno(), False)
            self.set_status('status.physical' if physical else 'status.passive')
        except OSError as exc: self.error(exc)

    def stop(self):
        if self.process is not None:
            try: os.write(self.process.stdin.fileno(), b'ffb-stop\nquit\n')
            except (OSError, ValueError): pass
            self.process.stdin.close()
            self.set_status('status.stopping')

    def poll(self):
        if self.wheel:
            try:
                frames = self.wheel.read_frames();before = self.editor.detected
                self.editor.observe(frames[-1] if frames else self.wheel.state(), self.wheel.observed_events)
                if self.editor.detected != before:
                    self.detected_source = self.editor.detected
                    self.input_code.set(self.editor.detected);self.detected.set(source_label(self.editor.detected, self.locale))
                self.refresh()
            except (OSError, EOFError, ValueError) as exc:
                self.disconnect();self.set_status('mapping.disconnected', error=exc)
        if self.process is not None:
            try:
                data = os.read(self.process.stdout.fileno(), 16384)
                if data:
                    self.log.configure(state='normal');self.log.insert('end', data.decode(errors='replace'))
                    if len(self.log.get('1.0', 'end')) > 200000: self.log.delete('1.0', 'end-200000c')
                    self.log.see('end');self.log.configure(state='disabled')
            except BlockingIOError: pass
            if self.process.poll() is not None:
                code = self.process.returncode;self.process.stdout.close()
                if not self.process.stdin.closed: self.process.stdin.close()
                self.process = None;self.set_status('status.stopped', code=code)
        if self.closing and self.process is None:
            self.root.destroy();return
        self.root.after(16, self.poll)

    def close(self):
        self.closing = True;self.disconnect();self.stop()
        if self.process is None: self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles-file')
    parser.add_argument('--profile', default='dirt5')
    parser.add_argument('--geometry', default='1280x800')
    args = parser.parse_args(argv)
    try:
        root = tk.Tk()
        root.geometry(args.geometry)
        BridgeGUI(root, ProfileStore(args.profiles_file), args.profile)
        root.mainloop()
    except (tk.TclError, ValueError, OSError) as exc:
        print('GUI unavailable: '+str(exc), file=sys.stderr)
        return 1
    return 0
