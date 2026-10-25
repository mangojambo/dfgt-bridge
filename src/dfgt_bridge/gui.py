# SPDX-License-Identifier: GPL-2.0-only
"""GUI Tk local. Monitor abre somente entrada readonly mediante clique explícito.

FFB só pode começar pelo botão físico explícito, em subprocesso do runner.
"""
import argparse
import copy
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from .config import ProfileStore, duration, validate_profile
from .mapping_editor import MappingEditor, MappingConflict
from .input.mapping import TARGET_NAMES, PRESETS, source_name
from .input.physical_wheel import PhysicalWheel, SOURCE
from .wheel_art import WheelArt


def runner_command(profiles_file, profile='dirt5', source=SOURCE, *, physical=False, elevated=False):
    script = Path(__file__).with_name('main.py')
    command = [sys.executable, '-B', str(script), '--profiles-file', str(Path(profiles_file).absolute()),
               '--profile', profile, '--source', source, '--control-stdin']
    if physical:
        command.append('--enable-ffb')
    return ['pkexec', *command] if elevated else command


class BridgeGUI:
    def __init__(self, root, store, profile='dirt5', wheel_factory=PhysicalWheel):
        self.root, self.store, self.wheel_factory = root, store, wheel_factory
        self.editor = MappingEditor(store.load(profile))
        self.wheel = self.process = None
        self.closing = False
        self.root.title('DFGT Bridge · Perfis, FFB e mapeamento G29')
        self.root.geometry('1220x780')
        self.root.minsize(1000, 700)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.status = tk.StringVar(value='Offline · motor desligado · prévia de mapeamento')
        self.profile_name = tk.StringVar(value=profile)
        self.source = tk.StringVar(value=SOURCE)
        self.elevated = tk.BooleanVar(value=False)
        self.form, self.axis_form = {}, {}
        bar = ttk.Frame(root, padding=8); bar.pack(fill='x')
        ttk.Label(bar, text='Perfil do jogo').pack(side='left')
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile_name, values=sorted(store.read()), width=20)
        self.profile_box.pack(side='left', padx=6)
        ttk.Button(bar, text='Carregar / novo', command=self.load).pack(side='left')
        ttk.Button(bar, text='Salvar perfil', command=self.save).pack(side='left', padx=6)
        ttk.Button(bar, text='Desfazer', command=lambda: self.history(False)).pack(side='left')
        ttk.Button(bar, text='Refazer', command=lambda: self.history(True)).pack(side='left', padx=6)
        self.tabs = ttk.Notebook(root); self.tabs.pack(fill='both', expand=True, padx=8)
        setup = ttk.Frame(self.tabs, padding=12)
        mapping = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(setup, text='Perfil e FFB')
        self.tabs.add(mapping, text='Botões e Eixos')
        self.build_setup(setup)
        self.build_mapping(mapping)
        ttk.Label(root, textvariable=self.status, wraplength=1180, padding=8).pack(fill='x')
        self.sync_form()
        self.refresh()
        self.tabs.select(mapping)
        self.root.after(16, self.poll)

    def build_setup(self, frame):
        ttk.Label(frame, text='G29 · 046d:c24f · auto PIDFF · sem captura por padrão', font=('', 13, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=8)
        labels = {'ffb_strength': 'Intensidade %', 'ffb_max_strength': 'Máximo %',
                  'ffb_polarity': 'Polaridade (−1 oficial)', 'ffb_slew_rate': 'Slew (unidades/s)',
                  'ffb_seconds': 'Duração (unlimited ou 30..240 s)'}
        for row, (name, label) in enumerate(labels.items(), 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=4)
            self.form[name] = tk.StringVar()
            ttk.Entry(frame, textvariable=self.form[name], width=24).grid(row=row, column=1, sticky='w')
        ttk.Label(frame, text='Entrada física DFGT').grid(row=6, column=0, sticky='w', pady=8)
        ttk.Entry(frame, textvariable=self.source, width=65).grid(row=6, column=1, columnspan=2, sticky='we')
        ttk.Checkbutton(frame, text='Elevar somente o runner com pkexec (se necessário)', variable=self.elevated).grid(row=7, column=0, columnspan=3, sticky='w')
        row = ttk.Frame(frame);row.grid(row=8, column=0, columnspan=3, sticky='w', pady=10)
        ttk.Button(row, text='Iniciar passivo', command=lambda: self.start(False)).pack(side='left')
        ttk.Button(row, text='Ativar FFB físico', command=lambda: self.start(True)).pack(side='left', padx=8)
        ttk.Button(row, text='PARAR FFB / bridge', command=self.stop).pack(side='left')
        ttk.Button(row, text='Copiar comando físico', command=self.copy_command).pack(side='left', padx=8)
        ttk.Label(frame, text='F9 / ffb-stop permanecem disponíveis. Fechar esta GUI encerra seu runner.\nAlterações salvas são aplicadas no próximo início; não remapeiam um runner em execução.', wraplength=1000).grid(row=9, column=0, columnspan=3, sticky='w')
        self.log = tk.Text(frame, height=10, wrap='word', state='disabled')
        self.log.grid(row=10, column=0, columnspan=3, sticky='nsew', pady=10)
        frame.rowconfigure(10, weight=1);frame.columnconfigure(2, weight=1)

    def build_mapping(self, frame):
        toolbar = ttk.Frame(frame);toolbar.pack(fill='x')
        self.preset = tk.StringVar()
        ttk.Combobox(toolbar, textvariable=self.preset, values=list(PRESETS.values()), state='readonly', width=20).pack(side='left')
        ttk.Button(toolbar, text='Aplicar preset', command=self.apply_preset).pack(side='left', padx=4)
        ttk.Button(toolbar, text='Restaurar G29 padrão', command=self.restore).pack(side='left')
        ttk.Button(toolbar, text='Monitorar DFGT (leitura)', command=self.connect).pack(side='left', padx=8)
        ttk.Button(toolbar, text='Parar monitor', command=self.disconnect).pack(side='left')
        body = ttk.Frame(frame);body.pack(fill='both', expand=True)
        wheels = ttk.Frame(body);wheels.pack(side='left', fill='both', expand=True)
        for col, name in enumerate(('dfgt', 'g29')):
            canvas = tk.Canvas(wheels, width=400, height=400, bg='#0f172a', highlightthickness=0)
            canvas.grid(row=0, column=col, sticky='nsew', padx=2)
            wheels.columnconfigure(col, weight=1)
            art = WheelArt(canvas, name, self.physical_select if name == 'dfgt' else self.select)
            setattr(self, name+'_art', art)
        wheels.rowconfigure(0, weight=1)
        side = ttk.Frame(body);side.pack(side='right', fill='y')
        scroll = tk.Canvas(side, width=290, height=390, highlightthickness=0)
        scrollbar = ttk.Scrollbar(side, orient='vertical', command=scroll.yview)
        scroll.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y');scroll.pack(side='left', fill='y', expand=True)
        detail = ttk.Frame(scroll, padding=8)
        scroll.create_window(0, 0, window=detail, anchor='nw')
        detail.bind('<Configure>', lambda event: scroll.configure(scrollregion=scroll.bbox('all')))
        ttk.Label(detail, text='Destino virtual selecionado').pack(anchor='w')
        self.target = tk.StringVar()
        target = ttk.Combobox(detail, textvariable=self.target, values=list(TARGET_NAMES.values()), state='readonly', width=24)
        target.pack(fill='x');target.bind('<<ComboboxSelected>>', lambda event: self.select(next(k for k, v in TARGET_NAMES.items() if v == self.target.get())))
        self.sources = tk.Listbox(detail, height=5, width=31, exportselection=False)
        self.sources.pack(fill='x', pady=6)
        self.sources.bind('<<ListboxSelect>>', lambda event: self.status.set(source_name(self.source_keys[self.sources.curselection()[0]])) if self.sources.curselection() else None)
        ttk.Button(detail, text='Remover associação selecionada', command=self.remove).pack(fill='x')
        self.detected = tk.StringVar(value='Nenhuma entrada detectada')
        ttk.Button(detail, text='Detectar botão', command=self.detect).pack(fill='x', pady=5)
        ttk.Label(detail, textvariable=self.detected, wraplength=265).pack(fill='x')
        self.input_code = tk.StringVar()
        ttk.Entry(detail, textvariable=self.input_code).pack(fill='x', pady=4)
        ttk.Button(detail, text='Adicionar / mover EVDEV', command=self.add).pack(fill='x')
        ttk.Button(detail, text='Conferir giro para direita', command=self.detect_dial).pack(fill='x', pady=4)
        ttk.Button(detail, text='Trocar sentidos A/B do dial', command=self.swap_dial).pack(fill='x')
        ttk.Label(detail, text='Marchas prontas: reduzir / aumentar').pack(anchor='w', pady=4)
        self.gear_down, self.gear_up = tk.StringVar(value='Quadrado'), tk.StringVar(value='X')
        for value in (self.gear_down, self.gear_up):
            ttk.Combobox(detail, textvariable=value, values=list(TARGET_NAMES.values()), state='readonly', width=24).pack(fill='x')
        self.notes = tk.StringVar()
        ttk.Label(detail, textvariable=self.notes, wraplength=270).pack(fill='x', pady=8)
        ttk.Button(detail, text='Ver conflitos / agregações', command=self.show_conflicts).pack(fill='x')
        axes = ttk.LabelFrame(frame, text='Eixos · prévia sem alterar ranges ou drivers', padding=6);axes.pack(fill='x')
        for col, (name, label) in enumerate((('steering', 'Direção'), ('accelerator', 'Acelerador'), ('brake', 'Freio'))):
            cell = ttk.Frame(axes);cell.grid(row=0, column=col, padx=8)
            ttk.Label(cell, text=label).pack(side='left')
            source, inverse = tk.StringVar(), tk.BooleanVar()
            self.axis_form[name] = source, inverse
            ttk.Combobox(cell, textvariable=source, values=('ABS_X', 'ABS_Y', 'ABS_Z'), state='readonly', width=7).pack(side='left', padx=3)
            ttk.Checkbutton(cell, text='Inverter', variable=inverse).pack(side='left')
        ttk.Button(axes, text='Aplicar eixos', command=self.apply_axes).grid(row=0, column=3)
        self.form['gas_axis'], self.form['invert_gas'] = tk.StringVar(), tk.BooleanVar()
        ttk.Label(axes, text='Canal HID do acelerador').grid(row=1, column=0, sticky='e')
        ttk.Combobox(axes, textvariable=self.form['gas_axis'], values=('y', 'z', 'both'), state='readonly', width=7).grid(row=1, column=1, sticky='w')
        ttk.Checkbutton(axes, text='Inversão HID adicional do acelerador', variable=self.form['invert_gas']).grid(row=1, column=2, columnspan=2, sticky='w')
        self.live = tk.StringVar()
        ttk.Label(frame, textvariable=self.live, wraplength=1160).pack(fill='x', pady=5)

    def error(self, exc):
        self.status.set(str(exc))
        messagebox.showerror('DFGT Bridge', str(exc), parent=self.root)

    def sync_form(self):
        profile = self.editor.profile
        for name, variable in self.form.items():
            value = profile[name]
            variable.set('unlimited' if name == 'ffb_seconds' and value is None else value)
        self.preset.set(PRESETS[profile['preset']])
        if profile['preset'] == 'gears':
            self.gear_down.set(TARGET_NAMES[profile['mapping']['bindings'].get('key:293', 'button:2')])
            self.gear_up.set(TARGET_NAMES[profile['mapping']['bindings'].get('key:292', 'button:1')])
        for name, (source, inverse) in self.axis_form.items():
            axis = profile['mapping']['axes'][name]
            source.set('ABS_'+'XYZ'[axis['source']]);inverse.set(axis['invert'])

    def collect(self):
        value = copy.deepcopy(self.editor.profile)
        for name, variable in self.form.items():
            raw = variable.get()
            value[name] = (duration(raw) if name == 'ffb_seconds' else float(raw) if name == 'ffb_slew_rate'
                           else int(raw) if name.startswith('ffb_') else raw)
        for name, (source, inverse) in self.axis_form.items():
            value['mapping']['axes'][name] = dict(source=('ABS_X', 'ABS_Y', 'ABS_Z').index(source.get()), invert=inverse.get())
        if value['mapping'] != self.editor.profile['mapping']:
            value['preset'] = 'custom'
        self.editor.commit(validate_profile(value))

    def load(self):
        try:
            self.editor = MappingEditor(self.store.load(self.profile_name.get()))
            self.sync_form();self.refresh()
            self.status.set('Perfil carregado; runner em execução não foi alterado.')
        except (ValueError, OSError) as exc:
            self.error(exc)

    def save(self):
        try:
            self.collect()
            self.store.save(self.profile_name.get(), self.editor.profile)
            self.profile_box.configure(values=sorted(self.store.read()))
            self.refresh()
            self.status.set('Perfil salvo em '+str(self.store.path)+'; aplicado no próximo início.')
            return True
        except (ValueError, OSError) as exc:
            self.error(exc)
            return False

    def refresh(self):
        self.target.set(TARGET_NAMES[self.editor.selected])
        self.source_keys = self.editor.sources()
        self.sources.delete(0, 'end')
        for source in self.source_keys:
            self.sources.insert('end', source_name(source))
        self.preset.set(PRESETS[self.editor.profile['preset']])
        value = self.editor.preview()
        self.dfgt_art.highlight(value['physical'], detected=self.editor.detected)
        self.g29_art.highlight(value['virtual'], self.editor.selected)
        self.notes.set('\n'.join(dict.fromkeys([self.editor.notice, *value['warnings'][:2]])).strip())
        self.live.set('PRÉVIA HID do perfil em edição · físico: '+', '.join(value['physical'])+
                      ' → virtual: '+', '.join(TARGET_NAMES[t] for t in value['virtual'])+
                      '\nABS_X/Y/Z: '+str(value['physical_axes'])+' · HID: '+str(value['hid_axes'])+' · bytes: '+value['report'])

    def select(self, target):
        if target.startswith('axis:'):
            self.status.set('Edite a origem e inversão desse eixo na seção inferior; a mudança vale para o próximo início.')
            return
        self.editor.select(target);self.refresh()

    def physical_select(self, source):
        if source.startswith('axis:'):
            return self.select(source)
        self.editor.detected = source
        self.input_code.set(source)
        self.detected.set(source_name(source));self.refresh()

    def add(self):
        source = self.input_code.get().strip()
        source = 'key:'+source if source.isdigit() else source
        try:
            try:
                self.editor.bind(source)
            except MappingConflict as exc:
                if not messagebox.askyesno('Mover associação', str(exc), parent=self.root):
                    return
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
            self.status.set('Clique em Monitorar DFGT para habilitar leitura. Detectar não ativa o motor.')
            return
        self.editor.detect();self.detected.set('Aguardando próximo botão ou D-pad…')

    def detect_dial(self):
        self.select('button:22')
        self.detect()
        self.status.set('Gire o dial para a direita, confira o código detectado e clique em Adicionar / mover. Depois confira a esquerda.')

    def apply_preset(self):
        try:
            key = next(k for k, name in PRESETS.items() if name == self.preset.get())
            self.collect()
            targets = {name: key for key, name in TARGET_NAMES.items()}
            self.editor.apply_preset(key, down=targets[self.gear_down.get()], up=targets[self.gear_up.get()])
            self.sync_form();self.refresh()
        except ValueError as exc:
            self.error(exc)

    def restore(self):
        try:
            self.collect();self.editor.apply_preset('g29');self.sync_form();self.refresh()
        except ValueError as exc:
            self.error(exc)

    def swap_dial(self):
        try:
            self.editor.swap_dial();self.refresh()
        except ValueError as exc:
            self.error(exc)

    def show_conflicts(self):
        messagebox.showinfo('Códigos compartilhados e agregações', '\n'.join(self.editor.preview()['warnings']), parent=self.root)

    def history(self, redo):
        self.editor.redo() if redo else self.editor.undo()
        self.sync_form();self.refresh()

    def apply_axes(self):
        try:
            self.collect();self.refresh()
        except ValueError as exc:
            self.error(exc)

    def connect(self):
        if self.wheel is not None:
            return
        try:
            self.wheel = self.wheel_factory(self.source.get())
            self.status.set('Monitor DFGT em O_RDONLY. Clique em Detectar botão. Nenhuma saída física foi aberta.')
        except (OSError, ValueError) as exc:
            self.error(exc)

    def disconnect(self):
        if self.wheel:
            self.wheel.close();self.wheel = None
        self.editor.detecting = False
        self.editor.observe((*self.editor.state[:3], 0, 0, []));self.refresh()

    def command(self, physical):
        return runner_command(self.store.path, self.profile_name.get(), self.source.get(),
                              physical=physical, elevated=self.elevated.get())

    def copy_command(self):
        if self.save():
            # Comando manual sem --control-stdin: o terminal não pertence à GUI.
            command = [part for part in self.command(True) if part != '--control-stdin']
            self.root.clipboard_clear();self.root.clipboard_append(shlex.join(command))
            self.status.set('Comando físico copiado; nenhuma execução realizada.')

    def start(self, physical):
        if self.process is not None:
            self.status.set('Pare o runner atual antes de iniciar outro.');return
        if not self.save():
            return
        try:
            self.process = subprocess.Popen(self.command(physical), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.process.stdin.fileno(), False)
            self.status.set('Runner iniciado por ação explícita; aguarde [PRONTO]. Sem captura automática.')
        except OSError as exc:
            self.error(exc)

    def stop(self):
        if self.process is not None:
            try:
                os.write(self.process.stdin.fileno(), b'ffb-stop\nquit\n')
            except (OSError, ValueError):
                pass
            self.process.stdin.close()  # EOF também encerra, mesmo sob pkexec.
            self.status.set('Parada solicitada; aguardando limpeza do runner.')

    def poll(self):
        if self.wheel:
            try:
                frames = self.wheel.read_frames()
                before = self.editor.detected
                self.editor.observe(frames[-1] if frames else self.wheel.state(), self.wheel.observed_events)
                if self.editor.detected != before:
                    self.input_code.set(self.editor.detected);self.detected.set(source_name(self.editor.detected))
                self.refresh()
            except (OSError, EOFError, ValueError) as exc:
                self.disconnect();self.status.set('Monitor desconectado: '+str(exc))
        if self.process is not None:
            try:
                data = os.read(self.process.stdout.fileno(), 16384)
                if data:
                    self.log.configure(state='normal');self.log.insert('end', data.decode(errors='replace'))
                    if len(self.log.get('1.0', 'end')) > 200000:
                        self.log.delete('1.0', 'end-200000c')
                    self.log.see('end');self.log.configure(state='disabled')
            except BlockingIOError:
                pass
            if self.process.poll() is not None:
                code = self.process.returncode
                self.process.stdout.close()
                if not self.process.stdin.closed:
                    self.process.stdin.close()
                self.process = None
                self.status.set(f'Runner encerrado ({code}); motor não será reativado automaticamente.')
        if self.closing and self.process is None:
            self.root.destroy();return
        self.root.after(16, self.poll)

    def close(self):
        self.closing = True
        self.disconnect();self.stop()
        if self.process is None:
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles-file')
    parser.add_argument('--profile', default='dirt5')
    args = parser.parse_args(argv)
    try:
        root = tk.Tk()
        BridgeGUI(root, ProfileStore(args.profiles_file), args.profile)
        root.mainloop()
    except (tk.TclError, ValueError, OSError) as exc:
        print('GUI indisponível: '+str(exc), file=sys.stderr)
        return 1
    return 0
