"""Synthetic musical fixtures; no recorded music or user project data."""
from bisect import bisect_right
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))
from velocity_automation_midi import NoteEvent
PPQ = 96
BAR = PPQ * 4
FLOOR = 1 / 128

def normalized_notes(rows):
    return [NoteEvent(n["tick"], n["duration"], n["note"], n["velocity"], 0) for n in rows]


def voice(cid, name, spans):
    """spans: (first bar, last bar exclusive, interval beats, held beats, velocity)."""
    notes = []
    for begin, stop, interval, held, velocity in spans:
        for tick in range(round(begin * BAR), round(stop * BAR), max(1, round(interval * PPQ))):
            notes.append({"tick": tick, "duration": min(round(held * PPQ), round(stop * BAR) - tick),
                          "note": 36 if name in {"SUB", "BASS"} else 60 + cid,
                          "velocity": velocity})
    return {"channel_id": cid, "id": cid, "name": name, "plugin": "Sylenth1",
            "notes": notes, "note_count": len(notes)}


def synthetic_song(markers=True):
    """Dense drop, sparse break and two different build lengths; no audio claims."""
    voices = [
        voice(1, "SUB", [(0, 24, 1, .8, 100), (40, 48, 1, .8, 100)]),
        voice(2, "LEAD", [(4, 12, 2, 1.5, 70), (12, 24, .5, .4, 110),
                           (32, 40, 2, 1.5, 80), (40, 48, .5, .4, 110)]),
        voice(3, "PAD", [(0, 48, 4, 3.5, 70)]),
        voice(4, "ARP", [(8, 24, .5, .3, 90), (32, 48, .5, .3, 90)]),
        voice(5, "Drums", [(0, 24, 1, .25, 100), (32, 48, 1, .25, 100)]),
    ]
    names = [(0, "Intro"), (4, "Build"), (16, "Drop"), (24, "Break"),
             (32, "Build"), (40, "Drop"), (47, "Outro")]
    return {"ppq": PPQ, "end_tick": 48 * BAR, "tempo": 128, "time_signature": [4, 4],
            "voices": voices, "markers": [{"tick": b * BAR, "name": n, "kind": 0}
                                                for b, n in names] if markers else []}
def interpolate(anchors, tick):
    """Independent straight-line equation, not a generator utility."""
    index = max(0, bisect_right([t for t, _ in anchors], tick) - 1)
    if index + 1 == len(anchors):
        return anchors[index][1]
    a, x = anchors[index]
    b, y = anchors[index + 1]
    return x + (y - x) * (tick - a) / (b - a)


def curve_document(points, end=48 * BAR, source_hash="a" * 64):
    return {"format": "velocity-energy-curve", "version": 1, "source_sha256": source_hash,
            "ticks_per_beat": PPQ, "end_tick": end, "points": points}
def plan(end_bars=32, drops=()):
    return {"ppq": PPQ, "end_tick": end_bars * BAR,
            "drops": [{"start": a * BAR, "end": b * BAR} for a, b in drops],
            "voice_roles": {"2": "lead"}}


def held(first_bar=1, bars=4, velocity=110):
    return NoteEvent(first_bar * BAR, bars * BAR, 64, velocity, 0)


def control(lane):
    return {"parameter_id": 25 if lane == "delay" else 143, "channel_id": 2,
            "name": lane.title() + " wet", "role": "lead", "character": "effect",
            "minimum": .05, "baseline": .2, "maximum": .8}
