"""Interactive MIDI energy sketcher. Tk stays on the UI thread."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from energy_curve import inspect_song, load_curve, make_curve, save_curve, validate_curve


@dataclass
class PainterState:
    overview: dict
    document: dict
    signature: tuple[int, int]
    enabled: bool = False
    undo: list = field(default_factory=list)


class EnergyCanvas(tk.Canvas):
    def __init__(self, master, panel, **kwargs):
        super().__init__(master, background="#101820", highlightthickness=0, cursor="crosshair", **kwargs)
        self.panel = panel
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: panel.end_stroke())
        self.bind("<Motion>", self._hover)
        self.bind("<Control-z>", lambda _e: panel.undo_curve())
        panel._canvases.append(self)

    def bounds(self):
        return 42, 26, max(44, self.winfo_width() - 18), max(28, self.winfo_height() - 30)

    def _position(self, event):
        state = self.panel.state
        if not state:
            return None
        left, top, right, bottom = self.bounds()
        fraction = min(1.0, max(0.0, (event.x - left) / (right - left)))
        value = min(1.0, max(0.0, (bottom - event.y) / (bottom - top)))
        return round(fraction * state.overview["end_tick"]), value

    def _press(self, event):
        point = self._position(event)
        if point and self.panel.can_paint:
            self.focus_set()
            self.panel.begin_stroke(*point)

    def _drag(self, event):
        point = self._position(event)
        if point and self.panel.can_paint:
            self.panel.continue_stroke(*point)

    def _hover(self, event):
        point = self._position(event)
        if point:
            self.panel.show_position(*point)

    def redraw(self):
        self.delete("all")
        left, top, right, bottom = self.bounds()
        state = self.panel.state
        if not state:
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text="Loading song timeline..." if self.panel.loading else "Load a song MIDI to paint its energy",
                             fill="#92a2b5", font=("Segoe UI", 10))
            return
        overview = state.overview
        end = max(1, overview["end_tick"])
        x = lambda tick: left + (right - left) * tick / end
        y = lambda value: bottom - (bottom - top) * value
        for value, label in ((1.0, "High"), (.5, "50%"), (0.0, "Low")):
            self.create_line(left, y(value), right, y(value), fill="#26333f", dash=(2, 4))
            self.create_text(left - 7, y(value), text=label, fill="#9badbf", anchor="e", font=("Segoe UI", 8))
        points = state.document["points"]
        # A bounded number of display points keeps long songs responsive.
        stride = max(1, len(points) // max(1, int(right - left)))
        visible_points = points[::stride]
        if visible_points[-1] != points[-1]:
            visible_points.append(points[-1])
        line = [coordinate for tick, value in visible_points for coordinate in (x(tick), y(value))]
        self.create_polygon(left, bottom, *line, right, bottom, fill="#193b3c", outline="")
        ticks, activity = overview["ticks"], overview["activity"]
        step = max(1, len(ticks) // max(1, int(right - left)))
        guide = [coordinate for i in range(0, len(ticks), step)
                 for coordinate in (x(ticks[i]), y(activity[i] * .82))]
        if len(guide) >= 4:
            self.create_line(*guide, fill="#657281", width=1)
        bars = max(1, overview["bars"])
        spacing = max(1, math.ceil(bars / 8))
        if spacing > 4:
            spacing = math.ceil(spacing / 4) * 4
        for bar_index in range(0, math.ceil(bars), spacing):
            xpos = x(min(end, bar_index * overview["bar_ticks"]))
            self.create_line(xpos, top, xpos, bottom, fill="#26333f")
            self.create_text(xpos, bottom + 14, text=str(bar_index + 1), fill="#9badbf", font=("Segoe UI", 8))
        self.create_text(right, bottom + 14, text="bars", fill="#9badbf", anchor="e", font=("Segoe UI", 8))
        last_label = -1000
        for marker in overview["markers"]:
            xpos = x(marker["tick"])
            if not left <= xpos <= right:
                continue
            self.create_line(xpos, top - 4, xpos, bottom, fill="#957444", dash=(3, 4))
            if xpos - last_label > 85:
                self.create_text(min(xpos + 3, right - 70), top - 13, text=marker["text"][:20],
                                 fill="#e1b576", anchor="w", font=("Segoe UI", 8))
                last_label = xpos
        if len(line) >= 4:
            self.create_line(*line, fill="#74f1c7" if self.panel.enabled.get() else "#80a69b", width=2)
        if not overview["paintable"]:
            self.create_text((left + right) / 2, (top + bottom) / 2, text="Painting currently supports 4/4 MIDI",
                             fill="#f2d4a1", font=("Segoe UI", 11, "bold"))


class EnergyPainterPanel(ttk.LabelFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, text="Paint song energy", padding=10, **kwargs)
        self.columnconfigure(0, weight=1)
        self.enabled = tk.BooleanVar(master=self, value=False)
        self.summary = tk.StringVar(master=self, value="Load a song MIDI, then drag across the line.")
        self.position = tk.StringVar(master=self, value="50% = current feel. Low energy is restrained, not silent.")
        self.state: PainterState | None = None
        self.loading = False
        self._source_key = ""
        self._cache: dict[str, PainterState] = {}
        self._token = 0
        self._load_after = None
        self._poll_after = None
        self._closed = False
        self._queue: queue.Queue = queue.Queue()
        self._stroke = None
        self._canvases = []
        self._action_buttons = []
        self._enable_buttons = []
        self._expanded = None
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        enabled = ttk.Checkbutton(top, text="Use painted energy", variable=self.enabled, command=self._toggle_enabled)
        enabled.grid(row=0, column=0, sticky="w")
        self._enable_buttons.append(enabled)
        ttk.Button(top, text="Expand", command=self.expand).grid(row=0, column=1)
        self.canvas = EnergyCanvas(self, self, height=156, width=500)
        self.canvas.grid(row=1, column=0, sticky="ew", pady=(6, 5))
        self._build_toolbar(self).grid(row=2, column=0, sticky="ew")
        ttk.Label(self, textvariable=self.summary, wraplength=590).grid(row=3, column=0, sticky="w", pady=(5, 0))
        ttk.Label(self, textvariable=self.position, wraplength=590).grid(row=4, column=0, sticky="w", pady=(2, 0))
        self.bind("<Destroy>", self._destroyed, add="+")
        self._update_controls()
        self._poll_after = self.after(60, self._poll_loads)

    @property
    def can_paint(self):
        return self.state is not None and not self.loading and self.state.overview["paintable"]

    def _build_toolbar(self, master):
        toolbar = ttk.Frame(master)
        for text, command in (("Undo", self.undo_curve), ("Reset", self.reset_curve), ("Smooth", self.smooth_curve),
                              ("Save curve", self._choose_save), ("Load curve", self._choose_load)):
            button = ttk.Button(toolbar, text=text, command=command, width=11)
            button.pack(side="left", padx=(0, 5))
            self._action_buttons.append(button)
        return toolbar

    def _destroyed(self, event):
        if event.widget != self:
            return
        self._closed = True
        for after_id in (self._poll_after, self._load_after):
            if after_id:
                self.after_cancel(after_id)

    @staticmethod
    def _key(path):
        return os.path.normcase(str(Path(path).expanduser().resolve())) if str(path).strip() else ""

    @staticmethod
    def _signature(path):
        stat = Path(path).stat()
        return stat.st_size, stat.st_mtime_ns

    def set_source(self, path: str, immediate: bool = False):
        try:
            key = self._key(path.strip())
            signature = self._signature(key) if key and Path(key).is_file() else None
        except (OSError, ValueError):
            key, signature = "", None
        if key == self._source_key and self.state and signature == self.state.signature:
            return
        if key == self._source_key and self.loading and not immediate:
            return
        self.end_stroke()
        if self.state and self._source_key:
            self.state.enabled = self.enabled.get()
            self._cache[self._source_key] = self.state
        self._source_key = key
        self._token += 1
        self.state = None
        self.enabled.set(False)
        self.loading = bool(signature)
        if self._load_after:
            self.after_cancel(self._load_after)
            self._load_after = None
        self.summary.set("Loading MIDI timeline..." if self.loading else "Choose a valid song MIDI to paint its energy.")
        self.position.set("Gray = MIDI note activity; this is not an audio preview.")
        self._update_controls()
        self.redraw()
        if not signature:
            return
        token = self._token
        # Cached paintings are restored only after the source is inspected again.
        # This also catches a file replaced at the same path.
        if immediate:
            self._start_load(key, token, signature)
        else:
            self._load_after = self.after(300, lambda: self._start_load(key, token, signature))

    def _start_load(self, key, token, signature):
        self._load_after = None
        if token != self._token or self._closed:
            return
        def worker():
            try:
                overview = inspect_song(Path(key))
                if self._signature(key) != signature:
                    raise ValueError("The MIDI changed while loading. Choose the file again.")
                self._queue.put((token, key, signature, overview, None))
            except Exception as exc:
                self._queue.put((token, key, signature, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_loads(self):
        if self._closed:
            return
        while True:
            try:
                token, key, signature, overview, error = self._queue.get_nowait()
            except queue.Empty:
                break
            if token != self._token or key != self._source_key:
                continue
            self.loading = False
            if error:
                self.summary.set(f"Could not load energy timeline: {error}")
                self.position.set("You can still generate without a painted curve.")
            else:
                cached = self._cache.get(key)
                if cached and cached.overview["source_sha256"] == overview["source_sha256"]:
                    self.state = cached
                    self.state.signature = signature
                    self.state.overview = overview
                    self.enabled.set(cached.enabled)
                else:
                    document = (make_curve(overview) if overview["paintable"] else
                                {"points": [[tick, .5] for tick in overview["ticks"]]})
                    self.state = PainterState(overview, document, signature)
                    self.enabled.set(False)
                self._cache[key] = self.state
                seconds = round(overview["duration_seconds"])
                self.summary.set(f"{overview['title']}  |  {overview['bars']:g} bars  |  {seconds // 60}:{seconds % 60:02d}")
                self.position.set("Drag to paint. 50% = current feel; low energy is restrained, not silent.")
                if not overview["paintable"]:
                    self.enabled.set(False)
                    self.state.enabled = False
                    self.position.set("Painting supports 4/4 MIDI. You can still use Classic without a curve.")
            self._update_controls()
            self.redraw()
        self._poll_after = self.after(60, self._poll_loads)

    def _update_controls(self):
        state = "normal" if self.can_paint else "disabled"
        for widget in self._action_buttons + self._enable_buttons:
            if widget.winfo_exists():
                widget.configure(state=state)

    def redraw(self):
        self._canvases = [canvas for canvas in self._canvases if canvas.winfo_exists()]
        for canvas in self._canvases:
            canvas.redraw()

    def _toggle_enabled(self):
        if not self.can_paint:
            self.enabled.set(False)
        if self.state:
            self.state.enabled = self.enabled.get()
        self.redraw()

    def show_position(self, tick, value):
        if self.state:
            bar = 1 + tick / self.state.overview["bar_ticks"]
            self.position.set(f"Bar {bar:.2f}  |  Energy {value:.0%}  |  Gray guide = MIDI note activity")

    def _remember(self):
        self.state.undo.append((deepcopy(self.state.document), self.enabled.get()))
        self.state.undo[:] = self.state.undo[-40:]

    def _changed(self):
        self.enabled.set(True)
        self.state.enabled = True
        self.redraw()

    def _densify(self):
        points = self.state.document["points"]
        original_ticks = [point[0] for point in points]
        ticks = sorted(set(original_ticks + self.state.overview["ticks"]))
        if len(ticks) > 8192:
            ticks = self.state.overview["ticks"]
        dense = []
        for tick in ticks:
            at = min(len(points) - 1, max(1, bisect_right(original_ticks, tick)))
            a, b = points[at - 1], points[at]
            amount = (tick - a[0]) / max(1, b[0] - a[0])
            dense.append([tick, a[1] + amount * (b[1] - a[1])])
        self.state.document["points"] = dense

    def begin_stroke(self, tick: int, value: float):
        if not self.can_paint:
            return
        self._remember()
        self._densify()
        self._stroke = None
        self._paint(tick, value)

    def continue_stroke(self, tick: int, value: float):
        if self._stroke is not None and self.can_paint:
            self._paint(tick, value)

    def _paint(self, tick, value):
        points = self.state.document["points"]
        tick = min(self.state.overview["end_tick"], max(0, int(tick)))
        value = min(1., max(0., float(value)))
        ticks = [point[0] for point in points]
        index = min(len(points) - 1, bisect_left(ticks, tick))
        if index and abs(ticks[index - 1] - tick) < abs(ticks[index] - tick):
            index -= 1
        previous = self._stroke
        if previous:
            prev_index, prev_value = previous
            for at in range(min(index, prev_index), max(index, prev_index) + 1):
                fraction = (at - prev_index) / (index - prev_index) if index != prev_index else 1
                points[at][1] = prev_value + fraction * (value - prev_value)
        else:
            points[index][1] = value
        self._stroke = index, value
        self.show_position(tick, value)
        self._changed()

    def end_stroke(self):
        self._stroke = None

    def undo_curve(self):
        if self.can_paint and self.state.undo:
            self.state.document, enabled = self.state.undo.pop()
            self.enabled.set(enabled)
            self.state.enabled = enabled
            self.end_stroke()
            self.redraw()

    def reset_curve(self):
        if self.can_paint:
            self._remember()
            self.state.document = make_curve(self.state.overview)
            self.end_stroke()
            self._changed()
            self.position.set("Reset to 50%: your current feel.")

    def smooth_curve(self):
        if self.can_paint:
            self._remember()
            points = self.state.document["points"]
            values = [point[1] for point in points]
            for i in range(1, len(points) - 1):
                points[i][1] = (values[i - 1] + 2 * values[i] + values[i + 1]) / 4
            self.end_stroke()
            self._changed()

    def snapshot_for(self, midi: Path):
        if not self.enabled.get():
            return None
        if self._key(midi) != self._source_key or not self.can_paint:
            raise ValueError("Wait for this song's energy timeline to load, or turn off Use painted energy.")
        if self._signature(midi) != self.state.signature:
            raise ValueError("The source MIDI changed. Reload its timeline before using painted energy.")
        return deepcopy(validate_curve(self.state.document, self.state.overview))

    def save_document(self, path):
        if not self.can_paint:
            raise ValueError("Load a 4/4 song MIDI before saving a curve.")
        save_curve(Path(path), validate_curve(self.state.document, self.state.overview))

    def load_document(self, path):
        if not self.can_paint:
            raise ValueError("Load a 4/4 song MIDI before opening its curve.")
        document = load_curve(Path(path), self.state.overview)
        self._remember()
        self.state.document = document
        self.end_stroke()
        self._changed()
        self.position.set("Saved energy curve loaded for this song.")

    def _choose_save(self):
        path = filedialog.asksaveasfilename(parent=self, title="Save energy curve", defaultextension=".json",
                                          initialfile="Energy curve.json", filetypes=[("Energy curves", "*.json")])
        if path:
            try:
                self.save_document(path)
                self.position.set("Energy curve saved.")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not save curve", str(exc), parent=self)

    def _choose_load(self):
        path = filedialog.askopenfilename(parent=self, title="Load energy curve", filetypes=[("Energy curves", "*.json")])
        if path:
            try:
                self.load_document(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not load curve", str(exc), parent=self)

    def expand(self):
        if self._expanded and self._expanded.winfo_exists():
            self._expanded.lift()
            return
        window = tk.Toplevel(self)
        self._expanded = window
        window.title("Paint song energy")
        window.geometry("1060x480")
        window.minsize(720, 390)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        ttk.Label(window, textvariable=self.summary, padding=(14, 12, 14, 4)).grid(row=0, column=0, sticky="w")
        enabled = ttk.Checkbutton(window, text="Use painted energy", variable=self.enabled, command=self._toggle_enabled)
        enabled.grid(row=1, column=0, sticky="w", padx=14)
        self._enable_buttons.append(enabled)
        canvas = EnergyCanvas(window, self, height=290)
        canvas.grid(row=2, column=0, sticky="nsew", padx=14, pady=8)
        self._build_toolbar(window).grid(row=3, column=0, sticky="w", padx=14)
        ttk.Label(window, textvariable=self.position, padding=14).grid(row=4, column=0, sticky="w")
        self._update_controls()

