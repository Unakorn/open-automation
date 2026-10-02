"""Use the existing energy painter with an exact saved-FLP identity.

Only this connector adapter changes drawing to straight press-to-release
segments. The MIDI writer's original painter remains untouched.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import uuid4
from tkinter import ttk

from energy_curve import inspect_song, make_curve, validate_curve
from energy_painter import EnergyPainterPanel, PainterState


def inspect_flp_timeline(path, expected_sha256):
    """Build the note guide from this FLP, retaining its exact byte identity."""
    from flp_song_analysis import analyze_flp, build_analysis_midi
    path = Path(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('The FLP changed before its energy timeline was built.')
    model = analyze_flp(path, max_instruments=20, data_override=data, allow_single_pattern=True)
    folder = Path(__file__).resolve().parents[1] / 'outputs' / '.energy-timeline'
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (uuid4().hex + '.mid')
    try:
        build_analysis_midi(model).save(temporary)
        overview = inspect_song(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != data:
        raise ValueError('The FLP changed while its energy timeline was built.')
    overview.update(source_sha256=expected_sha256, title=path.stem)
    return overview


class FLPEnergyPainterPanel(EnergyPainterPanel):
    def __init__(self, master, **kwargs):
        self._line_anchor = None
        self._line_original = None
        super().__init__(master, **kwargs)
        self.canvas.bind('<Configure>', lambda _event: self.redraw())
        self.canvas.bind('<ButtonRelease-1>', lambda event: self._release(self.canvas, event))
        self.clear_project()

    def redraw(self):
        super().redraw()
        if self.state is None:
            for canvas in self._canvases:
                canvas.delete('all')
                left, top, right, bottom = canvas.bounds()
                canvas.create_text((left + right) / 2, (top + bottom) / 2,
                                   text='Checking the FLP timeline…' if self.loading else 'Load your saved FLP to paint energy',
                                   fill='#92a2b5', font=('Segoe UI', 10))

    def expand(self):
        super().expand()
        for canvas in self._canvases:
            canvas.bind('<Configure>', lambda _event: self.redraw())
            canvas.bind('<ButtonRelease-1>', lambda event, target=canvas: self._release(target, event))

    def _release(self, canvas, event):
        point = canvas._position(event)
        if point is not None:
            self.continue_stroke(*point)
        self.end_stroke()

    def _build_toolbar(self, master):
        toolbar = ttk.Frame(master)
        for text, command in (('Undo', self.undo_curve), ('Reset', self.reset_curve),
                              ('Save curve', self._choose_save), ('Load curve', self._choose_load)):
            button = ttk.Button(toolbar, text=text, command=command, width=11)
            button.pack(side='left', padx=(0, 5))
            self._action_buttons.append(button)
        return toolbar

    def clear_project(self, message='Load your saved FLP to paint its overall energy.'):
        self.end_stroke()
        self._token += 1
        if self._load_after:
            self.after_cancel(self._load_after)
            self._load_after = None
        self._source_key = ''
        self._cache.clear()
        self.state = None
        self.loading = False
        self.enabled.set(False)
        self.summary.set(message)
        self.position.set('Drag a straight line. 50% keeps the generated shape; lower is restrained, not silent.')
        self._update_controls()
        self.redraw()

    def begin_project_check(self):
        self.end_stroke()
        self.loading = True
        self._update_controls()

    def set_project(self, path, signature, overview):
        key = self._key(path)
        identity = ('source_sha256', 'ticks_per_beat', 'end_tick')
        previous = self.state if key == self._source_key else None
        if previous and any(previous.overview[k] != overview[k] for k in identity):
            previous = None
        self.end_stroke()
        self._token += 1
        self._cache.clear()
        self._source_key = key
        self.loading = False
        if previous is not None:
            self.state = previous
            self.state.overview = deepcopy(overview)
            self.state.signature = signature
        else:
            self.state = PainterState(deepcopy(overview), make_curve(overview), signature)
            self.enabled.set(False)
        self.summary.set(f"{overview['title']} | {overview['bars']:g} bars | Timeline from this FLP")
        self.position.set('Drag from one point to another for a straight ramp. Gray shows note activity.')
        self._update_controls()
        self.redraw()

    def begin_stroke(self, tick, value):
        if not self.can_paint:
            return
        self._remember()
        self._line_original = deepcopy(self.state.document['points'])
        self._line_anchor = self._bounded_point(tick, value)
        self._draw_line_to(*self._line_anchor)

    def _bounded_point(self, tick, value):
        return (min(self.state.overview['end_tick'], max(0, int(tick))),
                min(1.0, max(0.0, float(value))))

    def continue_stroke(self, tick, value):
        if self._line_anchor is not None and self.can_paint:
            self._draw_line_to(*self._bounded_point(tick, value))

    def _draw_line_to(self, tick, value):
        anchor_tick, anchor_value = self._line_anchor
        low, high = sorted((anchor_tick, tick))
        # Rebuild from the press snapshot on every mouse event. Intermediate
        # mouse jitter never becomes a saved point or a smoothing operation.
        points = {t: v for t, v in self._line_original if not low <= t <= high}
        points[anchor_tick], points[tick] = anchor_value, value
        document = dict(self.state.document, points=[[t, v] for t, v in sorted(points.items())])
        try:
            self.state.document = validate_curve(document, self.state.overview)
        except ValueError as exc:
            self.position.set(str(exc))
            return
        self.show_position(tick, value)
        self._changed()

    def end_stroke(self):
        self._line_anchor = self._line_original = None
        self._stroke = None

    def snapshot_for(self, path, expected_sha256=None):
        if not self.enabled.get():
            return None
        if self._key(path) != self._source_key or not self.can_paint:
            raise ValueError('Wait for this FLP energy timeline, or turn off Use painted energy.')
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if (self._signature(path) != self.state.signature
                or actual != self.state.overview['source_sha256']
                or expected_sha256 is not None and actual != expected_sha256):
            raise ValueError('The FLP changed. Check it again before using this painting.')
        return deepcopy(validate_curve(self.state.document, self.state.overview))
