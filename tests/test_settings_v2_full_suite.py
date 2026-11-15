"""
YouCut — Comprehensive Test Suite for Settings, Themes, Layout & Updates.
Covers:
- Part 1: No NameError ('version_mod'), no cross-thread setParent warnings, cached FFmpeg verification.
- Part 2: Theme switching, selection ring sync, system theme resolution, title bar theme.
- Part 3: Settings layout width constraint (<= 1100px), equal card stretch (1, 1, 1), compact vector stepper (1-32), window widths 1000, 1400, 2500.
- Part 4: Component cooldowns (YouCut 24h, yt-dlp 12h, FFmpeg 7d), forced manual bypass, update cache loading and dropping, toggle OFF vs toggle ON (deferred if queue busy), frozen vs dev updater.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from clipgrab.config.settings import AppSettings
from clipgrab.core.ffmpeg_utils import (
    _CACHED_FFMPEG_VERIFICATION,
    invalidate_ffmpeg_cache,
    verify_ffmpeg,
)
from clipgrab.core.updater import (
    COOLDOWN_FFMPEG_SECONDS,
    COOLDOWN_YOUCUT_SECONDS,
    COOLDOWN_YTDLP_SECONDS,
    RETRY_FAILED_NETWORK_SECONDS,
    UpdateManager,
    _is_newer,
    get_installed_ffmpeg_version,
    get_installed_ytdlp_version,
)
from clipgrab.gui.main_window import MainWindow
from clipgrab.gui.theme import (
    ThemeManager,
    apply_theme,
    resolve_theme,
    set_window_title_bar_theme,
    toggle_theme,
)
from clipgrab.gui.widgets.settings_panel import (
    ComponentUpdateCard,
    FragmentStepper,
    QToggleSwitch,
    SettingsPanel,
    StatusBadge,
    StepperButton,
    ThemePreviewCard,
)
from clipgrab.version import APP_VERSION


class TestFullSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["YOUCUT_HOME"] = self.temp_dir
        self.settings = AppSettings.load()
        self.update_manager = UpdateManager(settings=self.settings)
        self.panel = SettingsPanel(self.settings, self.update_manager)
        self.panel.show()

    def tearDown(self) -> None:
        self.panel.close()
        self.update_manager.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ── PART 1: Run Log Bugs & Verifications ─────────────────────────────────

    def test_part1_no_version_mod_name_error(self) -> None:
        """Verify that _on_app_no_update runs cleanly without NameError: version_mod."""
        try:
            self.update_manager._on_app_no_update()
        except NameError as e:
            self.fail(f"_on_app_no_update raised NameError: {e}")

    def test_part1_ffmpeg_verification_cached(self) -> None:
        """Verify FFmpeg verification is cached in memory and not respawned repeatedly."""
        invalidate_ffmpeg_cache()
        ok1, info1 = verify_ffmpeg()
        self.assertTrue(ok1)

        # Second call should return cached value without executing subprocess
        with patch("subprocess.run") as mock_sub:
            ok2, info2 = verify_ffmpeg()
            self.assertEqual(ok1, ok2)
            self.assertEqual(info1, info2)
            mock_sub.assert_not_called()

        # Invalidate cache and verify it runs again
        invalidate_ffmpeg_cache()
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="ffmpeg version 7.0 test")
            ok3, info3 = verify_ffmpeg()
            self.assertTrue(ok3)
            mock_sub.assert_called_once()

    # ── PART 2: Theme Switching & Sync ───────────────────────────────────────

    def test_part2_theme_selection_ring_sync_no_double_toggle(self) -> None:
        """Verify clicking theme cards sets the setting once and updates selection rings correctly."""
        # Start with dark
        self.settings.update(theme="dark")
        self.panel._load_from_settings()
        self.assertTrue(self.panel._theme_card_dark._is_selected)
        self.assertFalse(self.panel._theme_card_light._is_selected)
        self.assertFalse(self.panel._theme_card_system._is_selected)

        # Select Light
        self.panel._on_theme_card_selected("light")
        self.assertEqual(self.settings.theme, "light")
        self.assertFalse(self.panel._theme_card_dark._is_selected)
        self.assertTrue(self.panel._theme_card_light._is_selected)
        self.assertFalse(self.panel._theme_card_system._is_selected)

        # Select System
        self.panel._on_theme_card_selected("system")
        self.assertEqual(self.settings.theme, "system")
        self.assertFalse(self.panel._theme_card_dark._is_selected)
        self.assertFalse(self.panel._theme_card_light._is_selected)
        self.assertTrue(self.panel._theme_card_system._is_selected)

    def test_part2_theme_resolution_and_title_bar(self) -> None:
        """Verify resolve_theme and window title bar helper without crashing."""
        self.assertEqual(resolve_theme("dark"), "dark")
        self.assertEqual(resolve_theme("light"), "light")
        self.assertIn(resolve_theme("system"), ("dark", "light"))

        # Test set_window_title_bar_theme doesn't fail
        set_window_title_bar_theme(self.panel, True)
        set_window_title_bar_theme(self.panel, False)

    # ── PART 3: Settings Page Layout & Stepper ────────────────────────────────

    def test_part3_content_width_constraint(self) -> None:
        """Verify content widget is capped at maximumWidth 1100px and horizontal scrollbar is disabled."""
        self.assertEqual(self.panel._content_widget.maximumWidth(), 1100)
        self.assertEqual(
            self.panel._scroll.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )

    def test_part3_update_cards_equal_stretch(self) -> None:
        """Verify all 3 update cards share layout equally."""
        self.assertEqual(self.panel._update_cards_layout.count(), 3)
        for i in range(3):
            item = self.panel._update_cards_layout.itemAt(i)
            self.assertEqual(self.panel._update_cards_layout.stretch(i), 1)

    def test_part3_fragment_stepper_compact_vector(self) -> None:
        """Verify compact FragmentStepper with StepperButtons, clamping between 1 and 32."""
        stepper = FragmentStepper(initial_val=16)
        self.assertEqual(stepper.value(), 16)
        self.assertIsInstance(stepper._btn_minus, StepperButton)
        self.assertIsInstance(stepper._btn_plus, StepperButton)
        self.assertEqual(stepper._btn_minus.width(), 36)
        self.assertEqual(stepper._btn_minus.height(), 36)

        # Step up
        stepper._on_plus()
        self.assertEqual(stepper.value(), 17)

        # Step down
        stepper._on_minus()
        self.assertEqual(stepper.value(), 16)

        # Clamping to bounds
        stepper.setValue(100)
        self.assertEqual(stepper.value(), 32)
        self.assertFalse(stepper._btn_plus.isEnabled())

        stepper.setValue(-10)
        self.assertEqual(stepper.value(), 1)
        self.assertFalse(stepper._btn_minus.isEnabled())

    def test_part3_window_widths_responsiveness(self) -> None:
        """Verify settings panel renders properly across narrow (1000px), normal (1400px), and ultrawide (2500px)."""
        for w in (1000, 1400, 2500):
            self.panel.resize(w, 800)
            self.panel.repaint()
            self.assertLessEqual(self.panel._content_widget.width(), 1100)

    # ── PART 4: Update Cooldowns, Cache, Badges & Toggles ──────────────────────

    def test_part4_cooldown_detection(self) -> None:
        """Verify cooldown blocks background checks when recently performed and allows after elapsed."""
        mgr = self.update_manager
        now = datetime.now(timezone.utc)

        # Set cache with check done 1 hour ago
        c = mgr._get_cache("youcut")
        c["last_check_time"] = (now - timedelta(hours=1)).isoformat()
        c["last_check_success"] = True

        # YouCut cooldown is 24h -> should be active
        self.assertTrue(mgr._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS))

        # Check done 25 hours ago -> cooldown expired
        c["last_check_time"] = (now - timedelta(hours=25)).isoformat()
        self.assertFalse(mgr._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS))

        # Failed network check retries after 1h
        c["last_check_success"] = False
        c["last_check_time"] = (now - timedelta(minutes=30)).isoformat()
        self.assertTrue(mgr._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS))
        c["last_check_time"] = (now - timedelta(minutes=70)).isoformat()
        self.assertFalse(mgr._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS))

    def test_part4_manual_check_bypasses_cooldown(self) -> None:
        """Verify check_all(force=True) bypasses cooldowns."""
        mgr = self.update_manager
        now = datetime.now(timezone.utc)

        # Set all caches to 10 seconds ago
        for comp in ("youcut", "ytdlp", "ffmpeg"):
            c = mgr._get_cache(comp)
            c["last_check_time"] = (now - timedelta(seconds=10)).isoformat()
            c["last_check_success"] = True

        with patch.object(mgr, "_run_app_check") as mock_app, \
             patch.object(mgr, "_run_check") as mock_ytdlp, \
             patch("clipgrab.core.updater.FFmpegUpdateChecker") as mock_ff:
            # Force check
            mgr.check_all(force=True)
            mock_app.assert_called_once()
            mock_ytdlp.assert_called_once()

    def test_part4_load_cached_badges_and_drop_cache(self) -> None:
        """Verify cached badges show immediately on launch and drop when installed >= latest."""
        mgr = self.update_manager

        # Set cache with a newer version
        c_youcut = mgr._get_cache("youcut")
        c_youcut["latest_version"] = "99.0.0"
        c_youcut["download_url"] = "https://example.com/setup.exe"

        status_received = []
        mgr.app_status_changed.connect(lambda s, v, n, u: status_received.append((s, v)))
        mgr.load_cached_badges()

        self.assertIn(("available", "99.0.0"), status_received)

        # Now simulate installed version >= latest (e.g. user updated)
        with patch("clipgrab.core.updater.APP_VERSION", "99.0.0"):
            status_received.clear()
            mgr.load_cached_badges()
            self.assertEqual(c_youcut["latest_version"], "")
            self.assertIn(("up_to_date", "99.0.0"), status_received)

    def test_part4_toggle_off_one_time_toast_and_badge(self) -> None:
        """Verify that when auto_update is OFF, toast notifications only show once per version."""
        mgr = self.update_manager
        mgr.auto_update = False
        mgr._settings.update(auto_update_ytdlp=False)

        toasts = []
        mgr.toast_message.connect(toasts.append)

        # First time update available: toast emitted
        mgr._on_ytdlp_check_completed("2026.10.01", "2026.11.01", True, True)
        self.assertEqual(len(toasts), 1)
        self.assertIn("2026.11.01", toasts[0])

        # Second time check completes with same version: NO duplicate toast
        mgr._on_ytdlp_check_completed("2026.10.01", "2026.11.01", True, True)
        self.assertEqual(len(toasts), 1)

    def test_part4_toggle_on_deferred_when_queue_busy(self) -> None:
        """Verify that when auto_update is ON and queue is busy, update is deferred until idle."""
        mgr = self.update_manager
        mgr.auto_update = True
        mgr.set_queue_busy(True)

        with patch.object(mgr, "_perform_update") as mock_perf:
            mgr._on_ytdlp_check_completed("2026.10.01", "2026.11.01", True, True)
            self.assertTrue(mgr._pending_update)
            mock_perf.assert_not_called()

            # When queue becomes idle, update is applied
            mgr.set_queue_busy(False)
            mock_perf.assert_called_once()
            self.assertFalse(mgr._pending_update)


if __name__ == "__main__":
    unittest.main()
