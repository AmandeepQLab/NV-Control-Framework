"""Hardware-free tests for the colormap selectors and their persistence.

The Zero Field selector (gui/widgets/zero_field_widget.py) is the reference;
the main-window live view has an identical one. Both apply to the image
already on screen -- no scan or stream is running in any test here -- and
both persist to the "display" section of setupInfo.json.

Colours are checked on the rendered QImage, not just the selected LUT, so
"the displayed image changed" is what is actually asserted. Only one
ZeroFieldWindow is built in this file (see the module docstring of
test_zero_field_window_save_toggle.py for why that matters).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow

_APP = QApplication.instance() or QApplication(sys.argv)

import pyqtgraph as pg

from config.config_manager import ConfigManager
from gui.display_settings import (
    COLORMAP_NAMES,
    LIVE_VIEW_COLORMAP_KEY,
    ZFE_COLORMAP_KEY,
    load_colormap,
    update_display_text,
)
from gui.main_window import MainWindow
from gui.zero_field_window import ZeroFieldWindow

# A config resembling setupInfo.json's irregular hand formatting, so the
# "nothing else changes" check is meaningful.
_CONFIG_TEXT = (
    '{\n'
    '"dataOutput": {\n'
    '   "zeroFieldDirectory":   "data"\n'
    '                },\n'
    '   "frequencyGenerators":[ {"type": "srs", "port": 5025} ]\n'
    '\n'
    '}'
)

# A horizontal ramp from 0 to 1: the middle pixel sits at level 0.5.
_RAMP = np.tile(np.linspace(0.0, 1.0, 64), (16, 1))


def _make_cfg(directory, display=None):
    path = Path(directory) / "setupInfo.json"
    text = _CONFIG_TEXT
    if display is not None:
        data = json.loads(text)
        data["display"] = display
        text = json.dumps(data, indent=4)
    path.write_text(text, encoding="utf-8")
    cfg = ConfigManager(str(path))
    cfg.load()
    return cfg, path


def _mid_pixel_rgb(image_item):
    """RGB of the middle of the ramp, as currently rendered."""
    image_item.render()
    qimage = image_item.qimage
    color = qimage.pixelColor(qimage.width() // 2, qimage.height() // 2)
    return color.red(), color.green(), color.blue()


def _expected_rgb(name):
    color = pg.colormap.get(name, source="matplotlib").map(0.5, mode="qcolor")
    return color.red(), color.green(), color.blue()


def _assert_rgb_close(test, actual, expected, tolerance=6):
    for a, e in zip(actual, expected):
        test.assertLessEqual(abs(a - e), tolerance, f"{actual} != {expected}")


# GUI objects built here are kept alive for the whole process rather than
# deleteLater()'d: destroying a ZeroFieldWindow alongside another pyqtgraph
# ImageView mid-suite reproducibly segfaulted a later test during garbage
# collection (the same fragility test_zero_field_window_save_toggle.py
# documents). Nothing is shown, so keeping them is harmless.
_KEEP_ALIVE = []


def _make_live_view(cfg):
    """MainWindow without hardware: only the live-view image and selector."""
    main = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(main)
    main.cfg = cfg
    main.colormap_combo = QComboBox()
    main.colormap_combo.addItems(COLORMAP_NAMES)
    main.image_view = pg.ImageView()
    main._init_live_view_colormap()
    _KEEP_ALIVE.append(main)
    return main


class LiveViewColormapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _show_frame(self, main):
        # What update_image() does with a frame, minus the camera.
        main.image_view.setImage(_RAMP.T, autoRange=False, autoLevels=False, levels=(0, 1))

    def test_changing_selector_recolours_displayed_frame_without_stream(self):
        cfg, path = _make_cfg(self._tmp.name)
        main = _make_live_view(cfg)
        self._show_frame(main)
        image_item = main.image_view.getImageItem()

        self.assertEqual(main.colormap_combo.currentText(), "Gray")
        _assert_rgb_close(self, _mid_pixel_rgb(image_item), _expected_rgb("gray"))

        main.colormap_combo.setCurrentText("Viridis")
        _assert_rgb_close(self, _mid_pixel_rgb(image_item), _expected_rgb("viridis"))

        # The next live frame keeps the selection.
        self._show_frame(main)
        _assert_rgb_close(self, _mid_pixel_rgb(image_item), _expected_rgb("viridis"))

        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["display"], {LIVE_VIEW_COLORMAP_KEY: "viridis"})

    def test_saved_colormap_is_applied_on_startup(self):
        cfg, _ = _make_cfg(self._tmp.name, display={LIVE_VIEW_COLORMAP_KEY: "magma"})
        main = _make_live_view(cfg)
        self.assertEqual(main.colormap_combo.currentText(), "Magma")
        self._show_frame(main)
        _assert_rgb_close(
            self, _mid_pixel_rgb(main.image_view.getImageItem()), _expected_rgb("magma")
        )


class ZeroFieldColormapTests(unittest.TestCase):
    """One ZeroFieldWindow for the whole file (see _KEEP_ALIVE)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg, cls.path = _make_cfg(cls._tmp.name, display={ZFE_COLORMAP_KEY: "plasma"})
        cls.window = ZeroFieldWindow(
            hardware_manager=SimpleNamespace(cfg=cls.cfg),
            hardware={"magnet": None},
            camera=None,
            acquisition_state_getter=lambda: None,
        )
        _KEEP_ALIVE.append(cls.window)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_shared_list_matches_zero_field_reference(self):
        combo = self.window.widget.colormap_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], COLORMAP_NAMES)

    def test_loads_saved_value_and_saves_change_and_recolours_current_image(self):
        window = self.window
        combo = window.widget.colormap_combo
        self.assertEqual(combo.currentText(), "Plasma")

        # An image already on screen, no scan running.
        window._current_display_image = _RAMP
        window._render_displayed_image()
        image_item = window.widget.image_view.getImageItem()
        _assert_rgb_close(self, _mid_pixel_rgb(image_item), _expected_rgb("plasma"))

        combo.setCurrentText("Inferno")
        _assert_rgb_close(self, _mid_pixel_rgb(image_item), _expected_rgb("inferno"))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["display"][ZFE_COLORMAP_KEY], "inferno")
        self.assertEqual(load_colormap(self.cfg, ZFE_COLORMAP_KEY), "Inferno")


class DisplaySectionTextTests(unittest.TestCase):
    def test_missing_or_unknown_value_defaults_to_gray(self):
        self.assertEqual(load_colormap(None, ZFE_COLORMAP_KEY), "Gray")
        self.assertEqual(load_colormap(SimpleNamespace(config={}), ZFE_COLORMAP_KEY), "Gray")
        cfg = SimpleNamespace(config={"display": {ZFE_COLORMAP_KEY: "jet"}})
        self.assertEqual(load_colormap(cfg, ZFE_COLORMAP_KEY), "Gray")

    def test_adding_and_updating_leaves_rest_of_file_untouched(self):
        added = update_display_text(_CONFIG_TEXT, ZFE_COLORMAP_KEY, "gray")
        body = _CONFIG_TEXT[: _CONFIG_TEXT.rindex("}")].rstrip()
        self.assertTrue(added.startswith(body))
        self.assertEqual(json.loads(added)["display"], {ZFE_COLORMAP_KEY: "gray"})

        second = update_display_text(added, LIVE_VIEW_COLORMAP_KEY, "gray")
        changed = update_display_text(second, ZFE_COLORMAP_KEY, "magma")
        self.assertTrue(changed.startswith(body))
        self.assertEqual(
            json.loads(changed)["display"],
            {ZFE_COLORMAP_KEY: "magma", LIVE_VIEW_COLORMAP_KEY: "gray"},
        )
        # Only the one value differs.
        self.assertEqual(changed.replace('"magma"', '"gray"'), second)

    def test_non_object_display_section_is_replaced_by_full_rewrite(self):
        text = '{"a": 1, "display": "oops"}'
        result = json.loads(update_display_text(text, ZFE_COLORMAP_KEY, "gray"))
        self.assertEqual(result, {"a": 1, "display": {ZFE_COLORMAP_KEY: "gray"}})


if __name__ == "__main__":
    unittest.main()
