"""Observed MBTI axis scores and estimated personality have separate gates."""
import unittest

from backend_contracts import AXES, infer_mbti, mbti_from_totals


class MbtiAxisVisibilityTests(unittest.TestCase):
    def test_one_supported_message_has_visible_scores_but_no_type(self):
        evidence = {axis: {left: .8, right: .1, "insufficient": .1}
                    for axis, (left, right) in AXES.items()}
        result = infer_mbti([{"personalityEvidence": evidence}] +
                            [{"personalityEvidence": None}] * 99, "test")
        self.assertIsNone(result["type"])
        self.assertEqual(result["status"], "partial")
        for axis in result["axes"].values():
            self.assertEqual(axis["evidenceCount"], 1)
            self.assertIsNone(axis["preference"])
            self.assertAlmostEqual(axis["leftShare"], 8 / 9)
            self.assertAlmostEqual(axis["rightShare"], 1 / 9)

    def test_zero_evidence_or_zero_mass_has_no_manufactured_scores(self):
        for totals in ([0.0, 0.0, 0, 100], [1.0, 1.0, 0, 100],
                       [0.0, 0.0, 1, 99]):
            with self.subTest(totals=totals):
                result = mbti_from_totals(100, totals[2],
                    {axis: list(totals) for axis in AXES}, "test")
                self.assertIsNone(result["type"])
                for axis in result["axes"].values():
                    self.assertIsNone(axis["leftShare"])
                    self.assertIsNone(axis["rightShare"])
                    self.assertIsNone(axis["preference"])

    def test_each_axis_displays_only_its_own_evidence(self):
        result = mbti_from_totals(100, 30, {
            "EI": [.8, .1, 1, 99], "SN": [0.0, 0.0, 0, 100],
            "TF": [3.0, 24.0, 30, 70], "JP": [0.0, 0.0, 0, 100],
        }, "test")
        self.assertAlmostEqual(result["axes"]["EI"]["leftShare"], 8 / 9)
        self.assertAlmostEqual(result["axes"]["TF"]["rightShare"], 8 / 9)
        self.assertIsNone(result["axes"]["EI"]["preference"])
        self.assertEqual(result["axes"]["TF"]["preference"], "F")
        for axis in ("SN", "JP"):
            self.assertIsNone(result["axes"][axis]["leftShare"])
            self.assertIsNone(result["axes"][axis]["rightShare"])
            self.assertIsNone(result["axes"][axis]["preference"])
        self.assertIsNone(result["type"])

    def test_type_still_requires_thirty_supported_messages_on_every_axis(self):
        totals = {axis: [24.0, 3.0, 30, 70] for axis in AXES}
        for sparse_axis in AXES:
            with self.subTest(axis=sparse_axis):
                partial = {axis: list(values) for axis, values in totals.items()}
                partial[sparse_axis] = [23.2, 2.9, 29, 71]
                result = mbti_from_totals(100, 100, partial, "test")
                self.assertIsNone(result["type"])
                self.assertIsNotNone(result["axes"][sparse_axis]["leftShare"])
                self.assertIsNone(result["axes"][sparse_axis]["preference"])
        complete = mbti_from_totals(100, 100, totals, "test")
        self.assertEqual(complete["type"], "ESTJ")
        self.assertEqual([axis["preference"] for axis in complete["axes"].values()], list("ESTJ"))

    def test_type_still_requires_one_hundred_eligible_messages(self):
        totals = {axis: [24.0, 3.0, 30, 69] for axis in AXES}
        result = mbti_from_totals(99, 30, totals, "test")
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["type"])
        self.assertTrue(all(axis["leftShare"] is not None for axis in result["axes"].values()))

    def test_balanced_scores_are_visible_without_forcing_a_type(self):
        totals = {axis: [15.0, 15.0, 30, 70] for axis in AXES}
        result = mbti_from_totals(100, 30, totals, "test")
        self.assertIsNone(result["type"])
        self.assertTrue(all(axis["leftShare"] == .5 and axis["rightShare"] == .5
                            for axis in result["axes"].values()))
        self.assertTrue(all(axis["preference"] is None for axis in result["axes"].values()))


if __name__ == "__main__":
    unittest.main()
