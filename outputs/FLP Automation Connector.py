"""Add connected automation to a copy of the user's saved FL Studio song."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / 'flp_connector_settings.json'
LEGACY_SETTINGS = ROOT / 'settings.json'
DEFAULT_OUT = ROOT / 'outputs' / 'projects'
sys.path.insert(0, str(ROOT / 'work'))
sys.path.insert(0, str(ROOT / 'outputs'))

from flp_energy_painter import FLPEnergyPainterPanel, inspect_flp_timeline
from fx_controls import FX_PRESETS, DEFAULT_FX_CONTROLS, validate_fx_controls

MOVEMENTS = {
    'Full song — straight ramps': ('song', 'Straight ramps and holds follow your parts. Use Delay & Reverb above for more FX, and paint the overall energy below.'),
}
DROP_FX = {'Balanced': 'reduced', 'Dry': 'dry', 'Let FX through': 'keep'}
FX_STYLES = {'Full-range FX (100%)': 'full_range', 'Fuller tails': 'fuller', 'Original short throws': 'original'}
THROW_TIMINGS = {'Off': 'off', 'Phrase ends': 'phrases', 'Every 8 bars': 'every8', 'Every 4 bars': 'every4'}
LANE_NAMES = {'cutoff': 'Filter', 'volume': 'Level movement', 'delay': 'Delay', 'reverb': 'Reverb',
              'distortion': 'Distortion', 'sylenth': 'Sylenth knobs'}
DEFAULTS_REVISION = 2
FULL_SONG_DEFAULTS = {'movement': 'song', 'intensity': 1.6, 'strength': 1.0,
                     'phrase_bars': 16, 'drop_length': 8, 'drop_fx': 'reduced',
                     'fx_style': 'full_range', 'division': 16,
                     'sylenth_movement': True, 'max_velocity': 127,
                     'note': 60, 'invert': False}


def _read_preferences(path: Path) -> dict:
    try:
        if path.stat().st_size > 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def load_preferences() -> dict:
    """Only folder/options migrate; selecting a source is always explicit."""
    data = _read_preferences(SETTINGS)
    if data:
        result = {key: data[key] for key in ('source_folder', 'output_folder', 'options') if key in data}
    else:
        legacy = _read_preferences(LEGACY_SETTINGS)
        result = {'output_folder': legacy.get('flp_output_folder', DEFAULT_OUT),
                  'options': legacy.get('flp_connector_options', {})}
        previous = legacy.get('flp_source')
        if isinstance(previous, str) and previous.strip():
            result['source_folder'] = str(Path(previous).expanduser().parent)
    saved = result.get('options')
    saved = dict(saved) if isinstance(saved, dict) else {}
    # Fill missing controls without resetting a saved intensity or timing.
    # Reading preferences never writes to the user's files.
    if data.get('defaults_revision') != DEFAULTS_REVISION:
        for key, value in FULL_SONG_DEFAULTS.items():
            saved.setdefault(key, copy.deepcopy(value))
    # The connector now consistently uses straight ramps. Keep other saved
    # preferences; older styles remain in the separate legacy application.
    saved['movement'] = 'song'
    saved.pop('energy_curve', None)
    try:
        saved['fx_controls'] = validate_fx_controls(saved.get('fx_controls'))
    except (ValueError, TypeError, KeyError):
        saved['fx_controls'] = copy.deepcopy(DEFAULT_FX_CONTROLS)
    result['options'] = saved
    result['defaults_revision'] = DEFAULTS_REVISION
    return result


def file_signature(path: Path) -> tuple[int, int]:
    value = path.stat()
    return value.st_size, value.st_mtime_ns


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_folder(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
    for number in range(1000):
        target = base / (f'flp_{stamp}' + (f'_{number}' if number else ''))
        try:
            target.mkdir()
            return target
        except FileExistsError:
            continue
    raise OSError('Could not create a fresh output folder. Choose another folder.')


def _messages(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return []


def _saved_number(value, default, minimum, maximum):
    try:
        number = float(value)
        return number if math.isfinite(number) and minimum <= number <= maximum else default
    except (ValueError, TypeError):
        return default


class FLPConnectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('FLP Automation Connector 2.5.0')
        self.geometry('920x850')
        self.minsize(740, 620)
        self.preferences = load_preferences()
        saved = self.preferences.get('options', {})
        saved = saved if isinstance(saved, dict) else {}
        self.flp_path = tk.StringVar()
        self.source_folder = str(self.preferences.get('source_folder', Path.home()))
        self.out_path = tk.StringVar(value=str(self.preferences.get('output_folder', DEFAULT_OUT)))
        self.source_summary = tk.StringVar(value='No song selected')
        self.source_location = tk.StringVar(value='Choose the saved FLP you want to automate.')
        self.result_summary = tk.StringVar(value='No automated copy for the selected song.')
        self.intensity = tk.DoubleVar(value=100 * _saved_number(saved.get('intensity', 1.6), 1.6, 0, 2))
        self.intensity_label = tk.StringVar(value=f'{self.intensity.get():.0f}%')
        self.movement = tk.StringVar(value='Full song — straight ramps')
        self.movement_hint = tk.StringVar()
        self.phrase_bars = tk.StringVar(value=str(saved.get('phrase_bars', 16)) if saved.get('phrase_bars', 16) in (4, 8, 16) else '16')
        self.drop_bars = tk.StringVar()
        self.drop_length = tk.StringVar(value=str(saved.get('drop_length', 8)) if saved.get('drop_length', 8) in (4, 8, 16) else '8')
        self.drop_fx = tk.StringVar(value=next((label for label, value in DROP_FX.items() if value == saved.get('drop_fx')), 'Balanced'))
        self.fx_style = tk.StringVar(value=next((label for label, value in FX_STYLES.items() if value == saved.get('fx_style')), 'Full-range FX (100%)'))
        self.division = tk.StringVar(value=str(saved.get('division', 16)) if saved.get('division', 16) in (8, 16, 32) else '16')
        self.sylenth_movement = tk.BooleanVar(value=saved.get('sylenth_movement', True) is not False)
        fx = saved['fx_controls']
        self.fx_vars = {lane: {
            'amount': tk.StringVar(value=f"{fx[lane]['amount'] * 100:g}"),
            'peak': tk.StringVar(value=f"{fx[lane]['peak'] * 100:g}"),
            'rise_beats': tk.StringVar(value=f"{fx[lane]['rise_beats']:g}"),
            'fade_bars': tk.StringVar(value=f"{fx[lane]['fade_bars']:g}"),
            'throws': tk.StringVar(value=next(label for label, value in THROW_TIMINGS.items() if value == fx[lane]['throws']))
        } for lane in ('delay', 'reverb')}
        self.fx_duck = tk.StringVar(value=f"{fx['duck'] * 100:g}")
        self.fx_bass_full_range = tk.BooleanVar(value=fx['bass_full_range'])
        self.fx_preset_label = tk.StringVar()
        self._fx_updating = False
        self.fx_percent_controls = {}
        self.seed = tk.IntVar(value=1)
        self.seed_label = tk.StringVar(value='Variation 1')
        self.status = tk.StringVar(value='Choose your saved FLP')
        self.preview_status = tk.StringVar(value='The instruments and song arrangement come from your FLP.')
        self.preview_notice = tk.StringVar()
        self.instrument_detail = tk.StringVar(value='Select an instrument to see its automation coverage.')
        self.project_info = None
        self._preview_path = None
        self._source_signature = None
        self._inspect_token = 0
        self._selection_token = 0
        self._inspect_after = None
        self._poll_after = None
        self._closed = False
        self._instrument_rows = {}
        self._results = queue.Queue()
        self.loading = False
        self.running = False
        self.last_successful_flp = None
        self.last_successful_output = None
        self._build()
        for variable in [*(value for lane in self.fx_vars.values() for value in lane.values()), self.fx_duck, self.fx_bass_full_range]:
            variable.trace_add('write', self._fx_changed)
        self.drop_fx.trace_add('write', self._sync_fx_duck_state)
        self._fx_changed()
        self._sync_fx_duck_state()
        self.flp_path.trace_add('write', self._source_changed)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._poll_after = self.after(100, self._poll_results)

    def destroy(self):
        self._closed = True
        for callback in (self._inspect_after, self._poll_after):
            if callback is not None:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass
        super().destroy()

    def close(self):
        if self.running:
            self.status.set('The new project is still being saved. Leave this window open until it finishes.')
            return
        self.destroy()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='FLP Automation Connector 2.5.0', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(header, text='Give your whole song connected automation, with its instruments and arrangement preserved.', wraplength=680).pack(anchor='w', pady=(4, 0))

        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky='nsew', padx=(18, 10))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, borderwidth=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        controls = ttk.Frame(self.canvas)
        controls.columnconfigure(0, weight=1)
        self._controls = controls
        window = self.canvas.create_window((0, 0), window=controls, anchor='nw')
        controls.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.bind('<MouseWheel>', self._scroll_controls, add='+')

        files = ttk.LabelFrame(controls, text='Your saved song', padding=12)
        files.grid(row=0, column=0, sticky='ew', pady=5)
        files.columnconfigure(1, weight=1)
        for row, label, variable, callback in ((0, 'Load song FLP', self.flp_path, self.pick_flp),
                                                (1, 'Save copies in', self.out_path, self.pick_output)):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=4)
            ttk.Entry(files, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=4)
            ttk.Button(files, text='Browse', command=callback).grid(row=row, column=2, padx=(10, 0), pady=4)
        ttk.Label(files, text='Uses the instruments, presets and arrangement already saved in your project. Up to 20 instruments; use only as many as your song needs.', wraplength=660).grid(row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))
        ttk.Label(files, textvariable=self.source_summary, font=('Segoe UI', 12, 'bold'), wraplength=660).grid(row=3, column=0, columnspan=3, sticky='w', pady=(10, 0))
        ttk.Label(files, textvariable=self.source_location, wraplength=660).grid(row=4, column=0, columnspan=3, sticky='w', pady=(3, 0))

        preview = ttk.LabelFrame(controls, text='Instruments and automation coverage', padding=12)
        preview.grid(row=2, column=0, sticky='ew', pady=5)
        preview.columnconfigure(0, weight=1)
        ttk.Label(preview, textvariable=self.preview_status, wraplength=660).grid(row=0, column=0, columnspan=2, sticky='w')
        self.instrument_table = ttk.Treeview(preview, columns=('instrument', 'mixer', 'coverage'), show='headings', height=7, selectmode='browse')
        for key, title, width in (('instrument', 'Instrument already in the song', 205), ('mixer', 'Mixer', 65), ('coverage', 'Automation', 325)):
            self.instrument_table.heading(key, text=title)
            self.instrument_table.column(key, width=width, minwidth=45, stretch=key != 'mixer')
        self.instrument_table.grid(row=1, column=0, sticky='ew', pady=(8, 4))
        table_scroll = ttk.Scrollbar(preview, orient='vertical', command=self.instrument_table.yview)
        table_scroll.grid(row=1, column=1, sticky='ns', pady=(8, 4))
        self.instrument_table.configure(yscrollcommand=table_scroll.set)
        self.instrument_table.bind('<<TreeviewSelect>>', self._show_instrument_detail)
        ttk.Label(preview, textvariable=self.instrument_detail, wraplength=660).grid(row=2, column=0, columnspan=2, sticky='w', pady=(3, 3))
        ttk.Label(preview, textvariable=self.preview_notice, foreground='#80551a', wraplength=660).grid(row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(preview, text='Check project again', command=self.inspect_source).grid(row=4, column=0, sticky='w', pady=(7, 0))

        self._build_fx_controls(controls).grid(row=1, column=0, sticky='ew', pady=5)

        movement = ttk.LabelFrame(controls, text='Other movement', padding=12)
        movement.grid(row=3, column=0, sticky='ew', pady=5)
        movement.columnconfigure(1, weight=1)
        ttk.Label(movement, text='Intensity').grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.intensity_scale = ttk.Scale(movement, from_=0, to=200, variable=self.intensity,
                                         command=lambda value: self.intensity_label.set(f'{float(value):.0f}%'))
        self.intensity_scale.grid(row=0, column=1, sticky='ew')
        ttk.Label(movement, textvariable=self.intensity_label, width=6).grid(row=0, column=2, padx=(10, 0))
        ttk.Label(movement, text='Intensity shapes filters and synth movement. Delay and reverb use their own amounts above. At 0%, the entire project stays unchanged.', wraplength=660).grid(row=1, column=0, columnspan=3, sticky='w', pady=(5, 10))
        ttk.Label(movement, text='Style').grid(row=2, column=0, sticky='w')
        ttk.Label(movement, textvariable=self.movement).grid(row=2, column=1, sticky='w')
        ttk.Button(movement, text='Use full-song defaults', command=self.use_full_song_defaults).grid(row=2, column=2, sticky='e', padx=(8, 0))
        ttk.Label(movement, textvariable=self.movement_hint, wraplength=660).grid(row=3, column=0, columnspan=3, sticky='w', pady=(5, 8))
        ttk.Label(movement, text='Phrase length').grid(row=4, column=0, sticky='w')
        phrase = ttk.Frame(movement)
        phrase.grid(row=4, column=1, columnspan=2, sticky='ew')
        ttk.Combobox(phrase, textvariable=self.phrase_bars, values=(4, 8, 16), state='readonly', width=5).pack(side='left')
        ttk.Label(phrase, text='bars').pack(side='left', padx=(6, 15))
        ttk.Label(phrase, textvariable=self.seed_label).pack(side='left')
        ttk.Button(phrase, text='New variation', command=self.new_variation).pack(side='left', padx=(10, 0))
        ttk.Label(movement, text='Drop bars').grid(row=5, column=0, sticky='w', pady=(8, 0))
        drops = ttk.Frame(movement)
        drops.grid(row=5, column=1, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Entry(drops, textvariable=self.drop_bars, width=13).pack(side='left')
        ttk.Label(drops, text='Length').pack(side='left', padx=(15, 6))
        ttk.Combobox(drops, textvariable=self.drop_length, values=(4, 8, 16), state='readonly', width=5).pack(side='left')
        ttk.Label(drops, text='bars').pack(side='left', padx=(5, 0))
        ttk.Label(movement, text='Optional bar numbers, starting at 1; for example 17,49. Full song follows saved section markers and changes in the played parts when this is blank.', wraplength=660).grid(row=6, column=0, columnspan=3, sticky='w', pady=(5, 8))
        for row, label, variable, values in ((9, 'Density', self.division, (8, 16, 32)),):
            ttk.Label(movement, text=label).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Combobox(movement, textvariable=variable, values=values, state='readonly', width=24 if row != 9 else 7).grid(row=row, column=1, sticky='w', pady=4)
        ttk.Checkbutton(movement, text='Move compatible Sylenth knobs to suit their saved presets', variable=self.sylenth_movement).grid(row=10, column=0, columnspan=3, sticky='w', pady=(9, 0))
        ttk.Label(movement, text='Other instruments keep their saved sounds. The coverage list shows where mixer automation can be added and where it is skipped.', wraplength=660).grid(row=11, column=0, columnspan=3, sticky='w', pady=(5, 0))
        self._movement_changed()

        self.energy_painter = FLPEnergyPainterPanel(controls)
        self.energy_painter.grid(row=4, column=0, sticky='ew', pady=5)

        result = ttk.LabelFrame(self, text='Result', padding=10)
        result.grid(row=2, column=0, sticky='ew', padx=18, pady=(8, 4))
        result.columnconfigure(0, weight=1)
        ttk.Label(result, textvariable=self.result_summary, wraplength=660).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 5))
        self.log = tk.Text(result, height=4, state='disabled', wrap='word')
        self.log.grid(row=1, column=0, sticky='ew')
        scroll = ttk.Scrollbar(result, orient='vertical', command=self.log.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.log.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(self, padding=(18, 8, 18, 14))
        actions.grid(row=3, column=0, sticky='ew')
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, textvariable=self.status, wraplength=210).grid(row=0, column=0, sticky='w')
        self.generate_button = ttk.Button(actions, text='Create automated copy', command=self.generate, state='disabled')
        self.generate_button.grid(row=0, column=1, padx=(0, 8))
        self.open_flp_button = ttk.Button(actions, text='Open generated FLP', command=self.open_flp, state='disabled')
        self.open_flp_button.grid(row=0, column=2, padx=(0, 8))
        self.open_button = ttk.Button(actions, text='Open folder', command=self.open_output, state='disabled')
        self.open_button.grid(row=0, column=3)
        self.write_log('Choose a saved song FLP. Review its automation coverage, then create a new copy.')

    def _build_fx_controls(self, parent):
        frame = ttk.LabelFrame(parent, text='Delay & Reverb', padding=12)
        frame.columnconfigure((0, 1), weight=1, uniform='effects')
        presets = ttk.Frame(frame)
        presets.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 9))
        self.fx_preset_buttons = {}
        for label in FX_PRESETS:
            button = ttk.Button(presets, text=label, command=lambda name=label: self.apply_fx_preset(name))
            button.pack(side='left', padx=(0, 7))
            self.fx_preset_buttons[label] = button
        ttk.Label(presets, textvariable=self.fx_preset_label).pack(side='left', padx=(4, 0))
        for column, lane in enumerate(('delay', 'reverb')):
            panel = ttk.LabelFrame(frame, text=lane.title(), padding=9)
            panel.grid(row=1, column=column, sticky='nsew', padx=(0, 5) if column == 0 else (5, 0))
            panel.columnconfigure(0, weight=1)
            for row, key, title in ((0, 'amount', 'Normal amount'), (2, 'peak', 'Throw level')):
                ttk.Label(panel, text=title).grid(row=row, column=0, sticky='w')
                self._fx_percent_row(panel, self.fx_vars[lane][key], (lane, key)).grid(row=row + 1, column=0, sticky='ew', pady=(2, 7))
            times = ttk.Frame(panel)
            times.grid(row=4, column=0, sticky='ew')
            times.columnconfigure(1, weight=1)
            for row, key, title, values in ((0, 'rise_beats', 'Rise (beats)', (.5, 1, 2, 4, 8)),
                                            (1, 'fade_bars', 'Fade (bars)', (1, 2, 4, 8, 16)),
                                            (2, 'throws', 'Throws', tuple(THROW_TIMINGS))):
                ttk.Label(times, text=title).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=3)
                ttk.Combobox(times, textvariable=self.fx_vars[lane][key], values=values,
                             state='readonly', width=15).grid(row=row, column=1, sticky='ew', pady=3)
        shared = ttk.Frame(frame)
        shared.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        shared.columnconfigure(1, weight=1)
        ttk.Label(shared, text='At drops').grid(row=0, column=0, sticky='w', padx=(0, 12))
        ttk.Combobox(shared, textvariable=self.drop_fx, values=tuple(DROP_FX),
                     state='readonly', width=18).grid(row=0, column=1, sticky='w')
        ttk.Label(shared, text='Pump depth').grid(row=1, column=0, sticky='w', padx=(0, 12), pady=(6, 0))
        self._fx_percent_row(shared, self.fx_duck, ('shared', 'duck')).grid(row=1, column=1, sticky='ew', pady=(6, 0))
        ttk.Label(frame, text='Pump depth controls the FX dip in Balanced mode. Dry keeps the added FX down during drops; Let FX through removes the dip.',
                  wraplength=650).grid(row=3, column=0, columnspan=2, sticky='w', pady=(6, 4))
        ttk.Checkbutton(frame, text='Allow full FX on bass/sub (otherwise limited to 10%)',
                        variable=self.fx_bass_full_range).grid(row=4, column=0, columnspan=2, sticky='w', pady=3)
        ttk.Label(frame, text='Normal amount is the underlying level between temporary FX boosts (throws). Create a new automated copy to hear changes; these controls are not live audio. Synth knobs stay within their saved preset ranges.',
                  wraplength=650).grid(row=5, column=0, columnspan=2, sticky='w', pady=(4, 0))
        return frame

    def _fx_percent_row(self, parent, variable, key):
        row = ttk.Frame(parent)
        row.columnconfigure(0, weight=1)
        slider_value = tk.DoubleVar(value=float(variable.get()))
        slider = ttk.Scale(row, from_=0, to=100, variable=slider_value,
                           command=lambda value: variable.set(f'{float(value):.0f}'))
        slider.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        number = ttk.Spinbox(row, from_=0, to=100, increment=1, textvariable=variable, width=6)
        number.grid(row=0, column=1)
        ttk.Label(row, text='%').grid(row=0, column=2, padx=(3, 0))

        def reflect_number(*_args):
            try:
                value = float(variable.get())
            except (ValueError, tk.TclError):
                return
            if math.isfinite(value) and 0 <= value <= 100:
                slider_value.set(value)
        variable.trace_add('write', reflect_number)
        self.fx_percent_controls[key] = {'slider': slider, 'number': number, 'value': slider_value}
        return row

    def _fx_controls_snapshot(self):
        try:
            controls = {lane: {
                'amount': float(fields['amount'].get()) / 100,
                'peak': float(fields['peak'].get()) / 100,
                'rise_beats': float(fields['rise_beats'].get()),
                'fade_bars': float(fields['fade_bars'].get()),
                'throws': THROW_TIMINGS[fields['throws'].get()]
            } for lane, fields in self.fx_vars.items()}
            controls['duck'] = float(self.fx_duck.get()) / 100
            controls['bass_full_range'] = bool(self.fx_bass_full_range.get())
        except (ValueError, KeyError, tk.TclError):
            raise ValueError('Check the Delay & Reverb numbers and listed timing choices.') from None
        return validate_fx_controls(controls)

    def _fx_changed(self, *_args):
        if self._fx_updating:
            return
        try:
            document = self._fx_controls_snapshot()
        except ValueError:
            self.fx_preset_label.set('Check values')
            return
        label = next((name for name, preset in FX_PRESETS.items() if document == preset), 'Custom')
        self.fx_preset_label.set(label)

    def apply_fx_preset(self, label):
        document = validate_fx_controls(FX_PRESETS[label])
        self._fx_updating = True
        try:
            for lane, fields in self.fx_vars.items():
                for key in ('amount', 'peak'):
                    fields[key].set(f'{document[lane][key] * 100:g}')
                for key in ('rise_beats', 'fade_bars'):
                    fields[key].set(f'{document[lane][key]:g}')
                fields['throws'].set(next(name for name, value in THROW_TIMINGS.items() if value == document[lane]['throws']))
            self.fx_duck.set(f"{document['duck'] * 100:g}")
            self.fx_bass_full_range.set(document['bass_full_range'])
        finally:
            self._fx_updating = False
        self._fx_changed()

    def _sync_fx_duck_state(self, *_args):
        state = 'normal' if self.drop_fx.get() == 'Balanced' else 'disabled'
        controls = self.fx_percent_controls[('shared', 'duck')]
        controls['slider'].configure(state=state)
        controls['number'].configure(state=state)

    def _scroll_controls(self, event):
        widget = event.widget
        while widget is not None:
            if widget == self.instrument_table:
                return
            if widget in (self._controls, self.canvas):
                self.canvas.yview_scroll(-int(event.delta / 120), 'units')
                return
            widget = getattr(widget, 'master', None)

    def write_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', str(text) + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _movement_changed(self, _event=None):
        self.movement_hint.set(MOVEMENTS.get(self.movement.get(), ('', 'Choose a movement style.'))[1])

    def use_full_song_defaults(self):
        self.intensity.set(160)
        self.intensity_label.set('160%')
        self.movement.set('Full song — straight ramps')
        self.phrase_bars.set('16')
        self.drop_bars.set('')
        self.drop_length.set('8')
        self.drop_fx.set('Balanced')
        self.fx_style.set('Full-range FX (100%)')
        self.division.set('16')
        self.sylenth_movement.set(True)
        self._movement_changed()

    def new_variation(self):
        self.seed.set((self.seed.get() + 1) % 2147483648)
        self.seed_label.set(f'Variation {self.seed.get()}')

    def pick_flp(self):
        path = filedialog.askopenfilename(parent=self, title='Choose your saved song FLP', initialdir=self.source_folder,
                                         filetypes=[('FL Studio projects', '*.flp'), ('All files', '*.*')])
        if path:
            self.source_folder = str(Path(path).parent)
            self.flp_path.set(path)

    def pick_output(self):
        path = filedialog.askdirectory(parent=self, title='Save automated copies in', initialdir=self.out_path.get() or str(DEFAULT_OUT))
        if path:
            self.out_path.set(path)

    def _source_changed(self, *_args):
        self._selection_token += 1
        self._inspect_token += 1
        self._clear_result()
        self.energy_painter.clear_project()
        self.project_info = None
        self._preview_path = self._source_signature = None
        self._instrument_rows.clear()
        self.instrument_table.delete(*self.instrument_table.get_children())
        self.instrument_detail.set('Select an instrument to see its automation coverage.')
        self.preview_notice.set('')
        value = self.flp_path.get().strip()
        if value:
            selected = Path(value).expanduser()
            self.source_summary.set(selected.name + ' · waiting for project check')
            self.source_location.set('Folder: ' + str(selected.parent))
        else:
            self.source_summary.set('No song selected')
            self.source_location.set('Choose the saved FLP you want to automate.')
        self.loading = bool(self.flp_path.get().strip())
        self.preview_status.set('Checking the saved project…' if self.loading else 'Choose a saved song FLP.')
        if not self.running:
            self.status.set('Checking the saved project…' if self.loading else 'Choose your saved FLP')
        if self._inspect_after is not None:
            self.after_cancel(self._inspect_after)
            self._inspect_after = None
        self._update_generate_state()
        if self.loading:
            self._inspect_after = self.after(250, self.inspect_source)

    def inspect_source(self):
        if self._closed:
            return
        self._clear_result()
        if self._inspect_after is not None:
            self.after_cancel(self._inspect_after)
            self._inspect_after = None
        self._inspect_token += 1
        token = self._inspect_token
        self.project_info = None
        self._preview_path = self._source_signature = None
        self.loading = True
        self.energy_painter.begin_project_check()
        self._update_generate_state()
        try:
            value = self.flp_path.get().strip()
            if not value:
                raise ValueError('Choose your saved song FLP.')
            path = Path(value).expanduser().resolve()
            if path.suffix.lower() != '.flp' or not path.is_file():
                raise ValueError('Choose an existing FL Studio .flp project.')
            signature = file_signature(path)
        except (OSError, ValueError) as exc:
            self.loading = False
            self.energy_painter.clear_project()
            self.preview_status.set('Could not check this project.')
            self.preview_notice.set(str(exc))
            self.status.set('Choose a saved FLP')
            self._update_generate_state()
            return
        self.preview_status.set('Reading the arrangement and checking automation coverage…')
        threading.Thread(target=self._inspect_worker, args=(token, path, signature), daemon=True).start()

    def _inspect_worker(self, token, path, signature):
        try:
            from flp_connector import inspect_flp
            report = inspect_flp(path)
            expected = report.get('sha256')
            if file_signature(path) != signature or expected and file_sha256(path) != expected:
                raise ValueError('The FLP changed while it was being checked. Save it, then check the project again.')
            if report.get('supported') is True and not expected:
                raise ValueError('The project check did not provide a source fingerprint. Check the project again.')
            if report.get('supported') is True:
                report = dict(report)
                try:
                    report['energy_overview'] = inspect_flp_timeline(path, expected)
                except Exception as exc:
                    # Painting is optional; a guide failure does not prevent
                    # otherwise supported projects from exporting unpainted.
                    report['energy_error'] = str(exc)
                if file_signature(path) != signature or file_sha256(path) != expected:
                    raise ValueError('The FLP changed while it was being checked. Save it, then check the project again.')
            self._results.put(('inspect', token, path, signature, report, None))
        except Exception as exc:
            self._results.put(('inspect', token, path, signature, None, str(exc)))

    @staticmethod
    def _coverage(row):
        status = str(row.get('automation_status') or 'Coverage not reported').replace('_', ' ')
        lanes = row.get('lanes', [])
        if isinstance(lanes, str):
            names = [lanes]
        elif isinstance(lanes, list):
            names = [str(lane.get('name', lane.get('lane', ''))) if isinstance(lane, dict) else str(lane) for lane in lanes]
        else:
            names = []
        names = [LANE_NAMES.get(name, name) for name in names if name]
        return status + (' · ' + ', '.join(dict.fromkeys(names)) if names else '')

    def _show_preview(self, report):
        self.project_info = copy.deepcopy(report)
        if report.get('energy_overview') and report.get('supported') is True:
            self.energy_painter.set_project(self._preview_path, self._source_signature, report['energy_overview'])
        else:
            self.energy_painter.clear_project('Energy timeline unavailable. You can generate without painting.'
                                             if report.get('energy_error') else 'Load your saved FLP to paint its overall energy.')
        self.instrument_table.delete(*self.instrument_table.get_children())
        self._instrument_rows.clear()
        instruments = report.get('instruments', [])
        for number, row in enumerate(instruments):
            key = str(number)
            self._instrument_rows[key] = row
            insert = row.get('mixer_insert')
            self.instrument_table.insert('', 'end', iid=key, values=(row.get('name') or f'Instrument {number + 1}',
                'Master' if insert == 0 else str(insert) if insert is not None else 'Unknown', self._coverage(row)))
        tempo, bars = report.get('tempo'), report.get('bars')
        source = self._preview_path or Path(self.flp_path.get().strip()).expanduser()
        self.source_summary.set(source.name + (f' · {tempo:g} BPM' if isinstance(tempo, (float, int)) else ''))
        self.source_location.set('Folder: ' + str(source.parent))
        summary = f"{len(instruments)} instrument{'s' if len(instruments) != 1 else ''}"
        if isinstance(tempo, (float, int)):
            summary += f' · {tempo:g} BPM'
        if isinstance(bars, (float, int)):
            summary += f' · {bars:g} bars'
        self.preview_status.set(summary + (' · Ready for an automated copy' if report.get('supported') is True else ' · Cannot export this project yet'))
        notices = _messages(report.get('errors')) + _messages(report.get('warnings'))
        monkeys = [row for row in instruments if row.get('drum_monkey')
                   or 'drum monkey' in str(row.get('plugin', '')).lower()
                   or 'drummonkey' in str(row.get('plugin', '')).lower()
                   or 'drum monkey' in str(row.get('name', '')).lower()]
        if monkeys:
            notices.append('Drum Monkey stays one instrument with its saved kit. Mixer 30 is reserved for the kick without new automation.')
            for row in monkeys:
                routing = row.get('drum_routing') or {}
                if routing.get('status') == 'auto_saved_split' and row.get('lanes'):
                    notices.append(f"{row.get('name', 'Drum Monkey')}: Automatically routes the drums to Mixer 29 and the separate kick to Mixer 30.")
                else:
                    detail = routing.get('reason') or row.get('automation_status') or 'Check this instrument in the coverage list.'
                    notices.append(f"{row.get('name', 'Drum Monkey')}: Automatic drum routing is unavailable. {detail}")
        self.preview_notice.set('\n'.join(dict.fromkeys(notices)))
        if instruments:
            self.instrument_table.selection_set('0')
            self._show_instrument_detail()
        if not self.running:
            self.status.set('Ready to create a copy' if report.get('supported') is True else 'Review the project limitations')
        self._update_generate_state()

    def _show_instrument_detail(self, _event=None):
        selected = self.instrument_table.selection()
        row = self._instrument_rows.get(selected[0]) if selected else None
        if not row:
            return
        text = f"{row.get('name', 'Instrument')} · {row.get('plugin') or 'Saved instrument'}\n{self._coverage(row)}"
        detail = row.get('reason') or row.get('skip_reason')
        if detail:
            text += '\n' + str(detail)
        self.instrument_detail.set(text)

    def _update_generate_state(self):
        ready = bool(not self.loading and not self.running and self.project_info
                     and self.project_info.get('supported') is True and not self.project_info.get('errors'))
        self.generate_button.configure(state='normal' if ready else 'disabled')

    def _generation_options(self):
        if self.running:
            raise ValueError('A project is already being saved.')
        if self.loading or not self.project_info or self._preview_path is None:
            raise ValueError('Wait for the saved project to be checked.')
        if self.project_info.get('supported') is not True or self.project_info.get('errors'):
            raise ValueError('This project cannot be exported yet. Review the limitations shown above.')
        path = Path(self.flp_path.get().strip()).expanduser().resolve()
        if path != self._preview_path or file_signature(path) != self._source_signature or file_sha256(path) != self.project_info.get('sha256'):
            self._source_changed()
            raise ValueError('The FLP changed after the preview. Save it, then check the project again.')
        if self.movement.get() not in MOVEMENTS or self.drop_fx.get() not in DROP_FX or self.fx_style.get() not in FX_STYLES:
            raise ValueError('Choose one of the listed movement and FX styles.')
        try:
            intensity = float(self.intensity.get())
            phrase, drop_length, division, seed = int(self.phrase_bars.get()), int(self.drop_length.get()), int(self.division.get()), int(self.seed.get())
        except (ValueError, TypeError, tk.TclError):
            raise ValueError('Intensity and timing controls must be valid numbers.') from None
        if not math.isfinite(intensity) or not 0 <= intensity <= 200:
            raise ValueError('Set intensity between 0% and 200%.')
        if phrase not in (4, 8, 16) or drop_length not in (4, 8, 16) or division not in (8, 16, 32) or not 0 <= seed <= 2147483647:
            raise ValueError('Choose the listed phrase length, drop length and density values.')
        raw_bars = self.drop_bars.get().strip()
        if raw_bars and not re.fullmatch(r'\d+(?:\s*[,; ]\s*\d+)*', raw_bars):
            raise ValueError('Enter drop bars as whole numbers separated by commas, such as 17,49.')
        drop_bars = sorted({int(value) for value in re.findall(r'\d+', raw_bars)})
        if any(value < 1 for value in drop_bars):
            raise ValueError('Drop bars start at 1.')
        bars = self.project_info.get('bars')
        if isinstance(bars, (int, float)) and bars > 0 and any(value > math.ceil(bars) for value in drop_bars):
            raise ValueError('A drop bar falls after the end of this song.')
        output_text = self.out_path.get().strip()
        if not output_text:
            raise ValueError('Choose where to save the automated copies.')
        output = Path(output_text).expanduser().resolve()
        if output.exists() and not output.is_dir():
            raise ValueError('The output location must be a folder.')
        options = {'expected_source_sha256': self.project_info['sha256'], 'intensity': round(intensity) / 100,
                   'strength': 1.0, 'movement': MOVEMENTS[self.movement.get()][0], 'phrase_bars': phrase,
                   'drop_bars': ','.join(map(str, drop_bars)), 'drop_length': drop_length,
                   'drop_fx': DROP_FX[self.drop_fx.get()], 'fx_style': FX_STYLES[self.fx_style.get()],
                   'division': division, 'seed': seed, 'sylenth_movement': bool(self.sylenth_movement.get()),
                   'max_velocity': 127, 'note': 60, 'invert': False,
                   'energy_curve': self.energy_painter.snapshot_for(path, self.project_info['sha256']),
                   'fx_controls': self._fx_controls_snapshot()}
        return {'source': path, 'base': output, 'options': copy.deepcopy(options), 'selection_token': self._selection_token}

    def _save_preferences(self, request):
        data = {'version': 1, 'defaults_revision': DEFAULTS_REVISION,
                'source_folder': str(request['source'].parent), 'output_folder': str(request['base']),
                'options': {key: value for key, value in request['options'].items()
                            if key not in ('expected_source_sha256', 'drop_bars', 'energy_curve')}}
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        self.preferences = data
        self.source_folder = data['source_folder']

    def _clear_result(self):
        self.last_successful_flp = self.last_successful_output = None
        self.open_flp_button.configure(state='disabled')
        self.open_button.configure(state='disabled')
        self.result_summary.set('No automated copy for the selected song.')

    def _result_matches_selection(self, context):
        try:
            current = Path(self.flp_path.get().strip()).expanduser().resolve()
            return (context['selection_token'] == self._selection_token and current == Path(context['source'])
                    and file_sha256(current) == context['sha256'])
        except (OSError, ValueError, KeyError):
            return False

    def generate(self):
        if self.running:
            return
        self._clear_result()
        try:
            request = self._generation_options()
            folder = new_run_folder(request['base'])
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', request['source'].stem).strip(' .') or 'Song'
            destination = folder / f'{name} - Automated.flp'
            if destination.resolve() == request['source']:
                raise ValueError('The generated copy must use a new file path.')
            try:
                self._save_preferences(request)
            except OSError as exc:
                self.write_log(f'Could not remember these settings: {exc}')
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror('Check the project', str(exc), parent=self)
            return
        self.running = True
        self._update_generate_state()
        self.status.set('Creating the automated copy…')
        self.result_summary.set(f"Creating a copy of {request['source'].name}…")
        self.write_log(f"\nSource FLP: {request['source']}\nOutput FLP: {destination}\nIntensity: {request['options']['intensity'] * 100:g}% · {self.movement.get()}")
        self.write_log('Saving a new FLP with the song’s existing instruments and arrangement.')
        threading.Thread(target=self._export_worker, args=(copy.deepcopy(request), destination), daemon=True).start()

    def _export_worker(self, request, destination):
        context = {'source': str(request['source']), 'selection_token': request['selection_token'],
                   'sha256': request['options']['expected_source_sha256']}
        try:
            from flp_connector import export_flp
            report = export_flp(request['source'], destination, options=request['options'])
            if not destination.is_file():
                raise OSError('The exporter did not create the expected FLP copy.')
            if (not isinstance(report, dict)
                    or Path(report.get('source_flp', '')).expanduser().resolve() != request['source'].resolve()
                    or report.get('source_sha256') != context['sha256']):
                raise ValueError('The export report does not match the selected source FLP. The result was not opened.')
            if (Path(report.get('output_flp', '')).expanduser().resolve() != destination.resolve()
                    or report.get('output_sha256') != file_sha256(destination)):
                raise ValueError('The generated file does not match its export report. The result was not opened.')
            self._results.put(('export', destination, report, context, None))
        except Exception as exc:
            self._results.put(('export', destination, None, context, str(exc)))

    def _poll_results(self):
        if self._closed:
            return
        if self._poll_after is not None:
            self.after_cancel(self._poll_after)
        self._poll_after = None
        try:
            while True:
                result = self._results.get_nowait()
                if result[0] == 'inspect':
                    _, token, path, signature, report, error = result
                    if token != self._inspect_token:
                        continue
                    self.loading = False
                    if error:
                        self.project_info = None
                        self.energy_painter.clear_project()
                        self.preview_status.set('Could not check this project.')
                        self.preview_notice.set(error)
                        if not self.running:
                            self.status.set('Review the project limitations')
                        self.write_log(f'Project check: {error}')
                    else:
                        self._preview_path, self._source_signature = path, signature
                        self._show_preview(report)
                    self._update_generate_state()
                else:
                    _, destination, report, context, error = result
                    self.running = False
                    current = self._result_matches_selection(context)
                    if error:
                        self._clear_result()
                        self.status.set('Could not create the copy' if current else 'Earlier export stopped; the selected song has no new copy')
                        self.write_log(f"Export stopped for: {context['source']}\n{error}\nOutput FLP: {destination}")
                        messagebox.showerror('Could not create the copy', f"Source FLP: {context['source']}\n\n{error}", parent=self)
                    elif not current:
                        self._clear_result()
                        self.status.set('Earlier selection saved; create a copy for the selected song')
                        self.write_log(f"Saved earlier selection: {context['source']}\nOutput FLP: {destination}\nThe selected song has changed. Its Open buttons remain disabled until its own copy is created.")
                    else:
                        self.last_successful_flp, self.last_successful_output = destination, destination.parent
                        self.open_flp_button.configure(state='normal')
                        self.open_button.configure(state='normal')
                        self.status.set('Automated copy ready')
                        self.result_summary.set(f"Copy of {Path(context['source']).name}: {destination}")
                        self.write_log(f"Completed source FLP: {context['source']}")
                        self.write_log(f"Saved {report.get('instrument_count', 'the existing')} instruments and {len(report.get('automation_tracks', []))} automation controls.")
                        plan = report.get('song_movement')
                        if plan and report.get('automation_tracks'):
                            if plan.get('curve_shape') == 'piecewise_linear':
                                painted = ' Painted energy included.' if report.get('options', {}).get('energy_curve') else ''
                                self.write_log(f"Full-song straight ramps and holds across {report.get('bars', 'the full song')} bars for {len(report['automation_tracks'])} controls.{painted}")
                            else:
                                self.write_log(f"Full-song plan: {len(plan.get('builds', []))} builds and {len(plan.get('gestures', []))} featured FX throws across {report.get('bars', 'the full song')} bars.")
                            if plan.get('drops'):
                                self.write_log(f"Drop cues: {plan.get('drop_source', 'song plan')}. Use Drop bars to choose different arrival points.")
                        if report.get('automation_pattern'):
                            count = report['automation_pattern'].get('pattern_count', 1)
                            self.write_log(f'All generated controls are together on one Playlist lane in {count} AUTO All Automations blocks, up to 8 bars each.')
                        for warning in _messages(report.get('warnings')):
                            self.write_log('Note: ' + warning)
                        self.write_log(f'FLP: {destination}')
                        messagebox.showinfo('Automated copy ready', f'Created:\n{destination}\n\nOpen the copy in FL Studio to hear the automation.', parent=self)
                    self._update_generate_state()
        except queue.Empty:
            pass
        self._poll_after = self.after(100, self._poll_results)

    def _open(self, path):
        if path is not None:
            try:
                os.startfile(path)
            except OSError as exc:
                messagebox.showerror('Could not open', str(exc), parent=self)

    def open_flp(self):
        self._open(self.last_successful_flp)

    def open_output(self):
        self._open(self.last_successful_output)


if __name__ == '__main__':
    app = FLPConnectorApp()
    app.mainloop()
