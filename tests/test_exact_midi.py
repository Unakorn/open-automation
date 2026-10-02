"""Portable interpolation and curve checks with synthetic music."""
import copy
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))
from helpers import *
class ExactCurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import linear_control_midi as engine
        from full_song_movement import build_plan
        cls.engine = engine
        cls.model = synthetic_song()
        cls.options = {"movement": "song", "intensity": 1.6, "strength": 1,
                       "seed": 1, "drop_fx": "reduced", "fx_style": "full_range",
                       "phrase_bars": 16, "drop_length": 8, "division": 16, "energy_curve": None}
        cls.plan = build_plan(cls.model, cls.options)
        cls.notes = normalized_notes(cls.model["voices"][1]["notes"])

    def assert_lines(self, anchors, points, baseline, low=0, high=1):
        end = self.model["end_tick"]
        self.assertEqual(anchors[0], (0, baseline))
        self.assertEqual(anchors[-1], (end - 1, baseline))
        self.assertEqual([a[0] for a in anchors], sorted({a[0] for a in anchors}))
        self.assertEqual(points[0], anchors[0])
        self.assertEqual(points[-1], anchors[-1])
        self.assertTrue(set(t for t, _ in anchors) <= set(t for t, _ in points))
        for tick, value in points:
            self.assertTrue(low <= value <= high)
            self.assertAlmostEqual(value, interpolate(anchors, tick), places=12,
                                   msg=f"Nonlinear sample at tick {tick}")

    def test_sampler_uses_exact_unequal_straight_segments_and_holds(self):
        anchors = [(0, .25), (137, .25), (661, .9), (919, .9), (1535, .1)]
        points = self.engine.sample_linear(anchors, PPQ, 1536, division=16)
        self.assertEqual(points[0], anchors[0])
        self.assertEqual(points[-1], anchors[-1])
        self.assertTrue(set(anchors) <= set(points))
        self.assertGreater(len(points), len(anchors))
        for tick, value in points:
            self.assertAlmostEqual(value, interpolate(anchors, tick), places=12)
        self.assertEqual([t for t, _ in points], sorted({t for t, _ in points}))

    def test_every_fx_sample_matches_its_piecewise_linear_anchors(self):
        for lane in ("cutoff", "delay", "reverb"):
            anchors = self.engine.fx_anchors(self.plan, self.notes, "lead", lane, self.options, voice_ids=[2])
            points = self.engine.generate_fx_points(self.plan, self.notes, "lead", lane, self.options, voice_ids=[2])
            self.assert_lines(anchors, points, 1 if lane == "cutoff" else 1 / 128)

    def test_synth_samples_are_linear_and_stay_inside_saved_patch_bounds(self):
        for character, parameter in (("brightness", 42), ("drive", 43), ("width", 15),
                                     ("modulation", 99), ("decay", 23), ("release", 24), ("effect", 25)):
            control = {"parameter_id": parameter, "channel_id": 2, "name": "Delay wet" if parameter == 25 else "Test",
                       "role": "lead", "character": character, "minimum": .2, "baseline": .45, "maximum": .8}
            anchors = self.engine.synth_anchors(self.plan, control, self.notes, self.options)
            points = self.engine.generate_synth_points(self.plan, control, self.notes, self.options)
            self.assert_lines(anchors, points, .45, .2, .8)

    def test_own_velocity_changes_the_curve_with_the_same_song_plan(self):
        quiet = [NoteEvent(n.tick, n.duration, n.note, 25, n.channel) for n in self.notes]
        loud = [NoteEvent(n.tick, n.duration, n.note, 120, n.channel) for n in self.notes]
        for lane in ("cutoff", "delay", "reverb"):
            a = self.engine.generate_fx_points(self.plan, quiet, "lead", lane, self.options, voice_ids=[2])
            b = self.engine.generate_fx_points(self.plan, loud, "lead", lane, self.options, voice_ids=[2])
            common = set(t for t, _ in a) & set(t for t, _ in b)
            av, bv = dict(a), dict(b)
            self.assertGreater(max(abs(av[t] - bv[t]) for t in common), .005, lane)
            self.assertTrue(all(bv[t] >= av[t] - 1e-12 for t in common),
                            lane + " moved against louder played dynamics")

    def test_own_held_lengths_change_response_without_changing_song_plan(self):
        short = [NoteEvent(t, PPQ // 8, 60, 90, 0) for t in range(4 * BAR, 12 * BAR, PPQ)]
        held = [NoteEvent(n.tick, PPQ, n.note, n.velocity, n.channel) for n in short]
        a = self.engine.generate_fx_points(self.plan, short, "lead", "cutoff", self.options, voice_ids=[2])
        b = self.engine.generate_fx_points(self.plan, held, "lead", "cutoff", self.options, voice_ids=[2])
        common = set(t for t, _ in a) & set(t for t, _ in b)
        av, bv = dict(a), dict(b)
        self.assertGreater(max(abs(av[t] - bv[t]) for t in common), .005)
        # Same attacks/velocities, both parts currently playing: actual longer
        # sustain increases the performance target, not a random variance score.
        self.assertGreater(interpolate(b, 9 * BAR), interpolate(a, 9 * BAR) + .01)

    def test_generation_has_no_random_jitter_and_does_not_mutate_input(self):
        original = copy.deepcopy((self.plan, self.notes, self.options))
        a = self.engine.generate_fx_points(self.plan, self.notes, "lead", "cutoff", self.options, voice_ids=[2])
        b = self.engine.generate_fx_points(self.plan, list(reversed(self.notes)), "lead", "cutoff",
                                           dict(self.options, seed=999), voice_ids=[2])
        self.assertEqual(a, b)
        self.assertEqual((self.plan, self.notes, self.options), original)

    def test_painting_controls_energy_but_samples_remain_linear(self):
        end = self.model["end_tick"]
        low = dict(self.options, energy_curve=curve_document([[0, .2], [end, .2]]))
        high = dict(self.options, energy_curve=curve_document([[0, .8], [end, .8]]))
        a = self.engine.generate_fx_points(self.plan, self.notes, "lead", "cutoff", low, voice_ids=[2])
        b = self.engine.generate_fx_points(self.plan, self.notes, "lead", "cutoff", high, voice_ids=[2])
        common = set(t for t, _ in a) & set(t for t, _ in b)
        av, bv = dict(a), dict(b)
        self.assertGreater(max(abs(av[t] - bv[t]) for t in common), .02)
        drawn = dict(self.options, energy_curve=curve_document([[0, .2], [701, .8], [3907, .35], [end, .7]]))
        anchors = self.engine.fx_anchors(self.plan, self.notes, "lead", "cutoff", drawn, voice_ids=[2])
        points = self.engine.generate_fx_points(self.plan, self.notes, "lead", "cutoff", drawn, voice_ids=[2])
        self.assertTrue({701, 3907} <= {t for t, _ in anchors})
        self.assert_lines(anchors, points, 1)

    def test_dry_policy_holds_the_entire_drop_and_keep_does_not_clear_it(self):
        dry_options, keep_options = dict(self.options, drop_fx="dry"), dict(self.options, drop_fx="keep")
        for lane in ("delay", "reverb"):
            dry = self.engine.fx_anchors(self.plan, self.notes, "lead", lane, dry_options, voice_ids=[2])
            keep = self.engine.fx_anchors(self.plan, self.notes, "lead", lane, keep_options, voice_ids=[2])
            for drop in self.plan["drops"]:
                a, b = drop["start"], drop["end"]
                for tick in (a, (a + b) // 2, b - 1):
                    self.assertAlmostEqual(interpolate(dry, tick), 1 / 128)
                self.assertGreater(interpolate(keep, (a + b) // 2), 1 / 128)
        control = {"parameter_id": 25, "channel_id": 2, "name": "Delay wet", "role": "lead",
                   "character": "effect", "minimum": .2, "baseline": .45, "maximum": .8}
        dry = self.engine.synth_anchors(self.plan, control, self.notes, dry_options)
        for drop in self.plan["drops"]:
            for tick in (drop["start"], (drop["start"] + drop["end"]) // 2, drop["end"] - 1):
                self.assertAlmostEqual(interpolate(dry, tick), .2)

if __name__ == "__main__": unittest.main()
