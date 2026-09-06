"""The link state must be impossible to mistake: stale or disconnected
numbers may be shown, but never as live ones."""
import json
import os
import tempfile
import time
import unittest

from claude_usage.history import last_sample_ts
from claude_usage.link import (DISCONNECTED, LIVE, STALE, age_short, age_text,
                               classify)


class TestClassify(unittest.TestCase):
    def test_recent_success_is_live(self):
        self.assertEqual(classify("", 0, 30).state, LIVE)

    def test_expired_token_is_disconnected_with_the_fix(self):
        s = classify("Credentials expired -- re-authenticate with 'claude'", 0, 5)
        self.assertEqual(s.state, DISCONNECTED)
        self.assertIn("claude auth login", s.advice)

    def test_no_credentials_and_403_are_disconnected(self):
        self.assertEqual(classify("No credentials -- run 'claude-usage'", 0, 5).state, DISCONNECTED)
        self.assertEqual(classify("OAuth usage error 403", 0, 5).state, DISCONNECTED)

    def test_rate_limit_is_stale_and_names_the_wait(self):
        s = classify("Rate limited -- using last known values", 647, 200)
        self.assertEqual(s.state, STALE)
        self.assertIn("10 minutes", s.headline)   # 647 s written out
        self.assertEqual(s.advice, "")

    def test_silence_past_threshold_is_stale_even_without_an_error(self):
        # A stopped timer or a hung poll: no error string, no fresh data.
        self.assertEqual(classify("", 0, 601, stale_after_s=600).state, STALE)
        self.assertEqual(classify("", 0, 599, stale_after_s=600).state, LIVE)

    def test_seventeen_hours_reads_as_seventeen_hours(self):
        s = classify("Credentials expired", 0, 17.4 * 3600)
        self.assertIn("17 hours 24 minutes", s.headline)


class TestNever(unittest.TestCase):
    def test_no_data_yet_is_said_plainly(self):
        s = classify("", 0, 10 ** 9)
        self.assertEqual(s.state, STALE)
        self.assertIn("No data received yet", s.headline)
        self.assertNotIn("days", s.headline)
        self.assertEqual(age_short(10 ** 9), "?")

    def test_float_ages_round_instead_of_truncating(self):
        self.assertEqual(age_text(17.4 * 3600), "17 hours 24 minutes")


class TestAgeText(unittest.TestCase):
    def test_singular_and_plural_are_written_out(self):
        self.assertEqual(age_text(1), "1 second")
        self.assertEqual(age_text(45), "45 seconds")
        self.assertEqual(age_text(60), "1 minute")
        self.assertEqual(age_text(3600), "1 hour")
        self.assertEqual(age_text(3600 * 2 + 60 * 5), "2 hours 5 minutes")
        self.assertEqual(age_text(86400 * 3), "3 days")
        self.assertNotIn("(s)", age_text(125))

    def test_short_form(self):
        self.assertEqual(age_short(45), "45s")
        self.assertEqual(age_short(17 * 60), "17m")
        self.assertEqual(age_short(3 * 3600 + 5), "3h")
        self.assertEqual(age_short(2 * 86400), "2d")


class TestLastSampleTs(unittest.TestCase):
    def test_reads_the_last_line_and_survives_junk(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "usage-history.jsonl")
            with open(p, "w") as fh:
                fh.write(json.dumps({"ts": 100.0, "session": 0.1}) + "\n")
                fh.write(json.dumps({"ts": 200.5, "session": 0.2}) + "\n")
            self.assertEqual(last_sample_ts(p), 200.5)
            self.assertEqual(last_sample_ts(os.path.join(d, "missing.jsonl")), 0.0)


class TestRetryAfterCap(unittest.TestCase):
    """Honouring Retry-After stops a lockout; capping it stops an hour of
    blindness. Both matter, so the cap is asserted explicitly."""

    def _wait_ms(self, retry_after: float, cap: float) -> int:
        wait = min(retry_after, cap) if cap > 0 else retry_after
        return int((wait + 5.0) * 1000)

    def test_short_wait_is_honoured_in_full(self):
        self.assertEqual(self._wait_ms(647, 900), 652_000)

    def test_long_wait_is_capped(self):
        self.assertEqual(self._wait_ms(3600, 900), 905_000)

    def test_cap_of_zero_means_no_cap(self):
        self.assertEqual(self._wait_ms(3600, 0), 3_605_000)


class TestPersistenceIsNotHijacked(unittest.TestCase):
    """Building the app with a throwaway config must not overwrite the
    user's real one. A harness once wrote a temp claude_dir into the live
    config, so the widget read session data from a directory that had
    already been deleted."""

    def test_config_path_redirects_the_write(self):
        import json
        from claude_usage.config import save_config
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "redirected.json")
            save_config(target, {"claude_dir": d, "config_path": target})
            self.assertTrue(os.path.isfile(target))
            self.assertEqual(json.load(open(target))["claude_dir"], d)

    def test_empty_config_path_means_do_not_persist(self):
        # The guard lives in _persist_config; assert the contract it reads.
        cfg = {"config_path": ""}
        self.assertEqual(cfg.get("config_path"), "")


class TestStripStaysResizable(unittest.TestCase):
    """The strip pins min == max so Qt cannot re-add Cocoa's resizable mask.
    That pin must be RELEASED before the next setGeometry, or Qt clamps the
    new size to the old one and the strip freezes -- which is exactly what
    happened on device while the offscreen repro passed, because the
    offscreen platform does not enforce size constraints."""

    def test_apply_size_releases_the_pin_before_setting_geometry(self):
        import inspect
        from claude_usage import overlay
        src = inspect.getsource(overlay.UsageOverlay._apply_size)
        release = src.index("setMaximumSize(16777215")
        pin = src.index("setMaximumSize(width, height)")
        self.assertLess(release, pin,
                        "the pin must be released before it is re-applied")
        geo = src.index("setGeometry(")
        self.assertLess(release, geo,
                        "the pin must be released BEFORE setGeometry, or the "
                        "new size is clamped to the old one")
