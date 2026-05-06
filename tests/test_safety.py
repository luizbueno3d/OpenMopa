import unittest
from unittest import mock

from mopa_luiz.cli import MarkConfig
from mopa_luiz.safety import (
    evaluate_emission,
    validate_layer_settings,
)


def fake_cfg() -> MarkConfig:
    return MarkConfig(
        path=mock.MagicMock(),
        values={"FIELDSIZE": "200", "MINPWMFREQ": "1000", "MAXPWMFREQ": "4000000"},
    )


class EvaluateEmissionTests(unittest.TestCase):
    def _ok(self, **overrides):
        defaults = dict(
            cfg=fake_cfg(),
            power_percent=1.0,
            frequency_khz=30.0,
            pulse_width_ns=200.0,
            intends_emission=True,
            arm=True,
            confirm="ARM",
            operation="vector_engrave",
            paths_count=2,
        )
        defaults.update(overrides)
        return evaluate_emission(**defaults)

    def test_happy_path_passes(self):
        result = self._ok()
        self.assertTrue(result.ok)
        self.assertTrue(result.emission_will_occur)

    def test_missing_arm_blocks_emission(self):
        result = self._ok(arm=False)
        self.assertFalse(result.ok)
        self.assertTrue(any("arm=true" in e for e in result.errors))

    def test_wrong_confirm_blocks(self):
        result = self._ok(confirm="arm please")
        self.assertFalse(result.ok)

    def test_power_up_to_100_percent_passes(self):
        result = self._ok(power_percent=100)
        self.assertTrue(result.ok)

    def test_power_above_100_percent_blocks(self):
        result = self._ok(power_percent=101)
        self.assertFalse(result.ok)
        self.assertTrue(any("between 0 and 100" in e for e in result.errors))

    def test_pulse_off_table_blocks(self):
        result = self._ok(pulse_width_ns=120)  # not on the table
        self.assertFalse(result.ok)

    def test_frequency_outside_markcfg_blocks(self):
        result = self._ok(frequency_khz=0.1)
        self.assertFalse(result.ok)

    def test_raster_now_passes_safety_gate(self):
        # Raster engrave is implemented (mopa_luiz.raster) so the safety
        # gate must let it through; layer-level validation handles raster
        # parameter sanity (pitch > 0, angle finite).
        result = self._ok(operation="raster_engrave")
        self.assertTrue(result.ok)

    def test_empty_job_blocks(self):
        result = self._ok(paths_count=0)
        self.assertFalse(result.ok)


class LayerValidationTests(unittest.TestCase):
    def test_disabled_layer_skips_checks(self):
        errs = validate_layer_settings(
            operation="disabled",
            power_percent=999, frequency_khz=999, pulse_width_ns=1,
            speed_mm_s=0, cfg=fake_cfg(),
        )
        self.assertEqual(errs, [])

    def test_emitting_layer_up_to_100_percent_passes(self):
        errs = validate_layer_settings(
            operation="vector_cut",
            power_percent=100, frequency_khz=30, pulse_width_ns=200,
            speed_mm_s=300, cfg=fake_cfg(),
        )
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
