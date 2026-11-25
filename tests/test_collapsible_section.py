"""gui/widgets/collapsible_section.py and gui/style.py. GUI only, no hardware."""

import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QScrollArea, QWidget

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from gui import style  # noqa: E402
from gui.widgets import collapsible_section as cs  # noqa: E402
from gui.widgets.collapsible_section import CollapsibleSection  # noqa: E402


def _section(**kwargs):
    section = CollapsibleSection("Delays", **kwargs)
    spin = style.double_spin(0.0, 10.0, 4, 0.02, "s", tooltip="t")
    combo = QComboBox()
    combo.addItems(["a", "b"])
    section.body_layout().addRow("Trigger delay:", spin)
    section.body_layout().addRow("Mode:", combo)
    return section, spin, combo


class SectionBehaviourTests(unittest.TestCase):
    def setUp(self):
        cs.disable_state_persistence()

    def test_collapsed_by_default_shows_summary_only(self):
        section, _spin, _combo = _section()
        section.set_summary("trigger 0.02 s")
        self.assertFalse(section.is_expanded())
        self.assertTrue(section.body.isHidden())
        self.assertFalse(section.summary_label.isHidden())
        self.assertEqual(section.summary_label.text(), "trigger 0.02 s")

    def test_expanding_shows_body_hides_summary_and_signals(self):
        section, _spin, _combo = _section()
        section.set_summary("x")
        seen = []
        section.expanded_changed.connect(seen.append)
        section.toggle_button.click()
        self.assertTrue(section.is_expanded())
        self.assertFalse(section.body.isHidden())
        self.assertTrue(section.summary_label.isHidden())
        section.set_expanded(False)
        self.assertEqual(seen, [True, False])

    def test_collapsing_never_changes_values_or_enabled_state(self):
        section, spin, combo = _section(expanded=True)
        spin.setValue(1.2345)
        combo.setCurrentIndex(1)
        spin.setEnabled(False)
        for expanded in (False, True, False):
            section.set_expanded(expanded)
            self.assertEqual(spin.value(), 1.2345)
            self.assertEqual(combo.currentIndex(), 1)
            self.assertFalse(spin.isEnabled())

    def test_header_checkbox_expands_but_never_collapses_or_unchecks(self):
        check = QCheckBox("On")
        section, _spin, _combo = _section(checkbox=check)
        check.setChecked(True)
        self.assertTrue(section.is_expanded())
        check.setChecked(False)
        self.assertTrue(section.is_expanded(), "unticking must not fold it")
        check.setChecked(True)
        section.set_expanded(False)
        self.assertTrue(check.isChecked(), "folding must not change the setting")

    def test_summary_provider_follows_body_and_checkbox(self):
        check = QCheckBox("On")
        section, spin, combo = _section(checkbox=check)
        section.set_summary_provider(
            lambda: f"{'on' if check.isChecked() else 'off'}, {spin.value():g} s, "
                    f"{combo.currentText()}")
        self.assertEqual(section.summary(), "off, 0.02 s, a")
        spin.setValue(0.5)
        combo.setCurrentIndex(1)
        self.assertEqual(section.summary(), "off, 0.5 s, b")
        check.setChecked(True)
        self.assertEqual(section.summary(), "on, 0.5 s, b")

    def test_width_is_the_same_collapsed_and_expanded(self):
        section, spin, _combo = _section()
        collapsed = section.sizeHint().width()
        self.assertGreaterEqual(collapsed, spin.sizeHint().width())
        section.set_expanded(True)
        self.assertEqual(section.sizeHint().width(), collapsed)

    def test_long_summary_does_not_widen_the_section(self):
        section, _spin, _combo = _section()
        width = section.sizeHint().width()
        section.set_summary("Field: 4 G along [0.707, 0.707, 0] = (2.8284, 2.8284, 0) G " * 3)
        self.assertEqual(section.sizeHint().width(), width)

    def test_title_and_tooltip(self):
        section = CollapsibleSection("Plot & fit")
        self.assertEqual(section.title(), "Plot & fit")
        self.assertEqual(section.toggle_button.text(), "Plot && fit")  # no mnemonic
        self.assertIn("Only hides", section.toggle_button.toolTip())


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "ui_state.ini")
        self.addCleanup(cs.disable_state_persistence)

    def _settings(self):
        return QSettings(self.path, QSettings.Format.IniFormat)

    def test_off_by_default_nothing_read_or_written(self):
        cs.disable_state_persistence()
        self.assertFalse(cs.state_persistence_enabled())
        settings = self._settings()
        settings.setValue("sections/delays/expanded", True)
        settings.sync()
        section, _spin, _combo = _section(settings_key="delays")
        self.assertFalse(section.is_expanded(), "stored state must be ignored")
        section.set_expanded(True)
        section.set_expanded(False)
        self.assertTrue(self._settings().value("sections/delays/expanded") in (True, "true"))

    def test_remembered_when_enabled(self):
        cs.enable_state_persistence(self._settings())
        section, _spin, _combo = _section(settings_key="delays")
        self.assertFalse(section.is_expanded())
        section.set_expanded(True)
        again, _spin, _combo = _section(settings_key="delays")
        self.assertTrue(again.is_expanded())
        again.set_expanded(False)
        third, _spin, _combo = _section(settings_key="delays")
        self.assertFalse(third.is_expanded())
        # Without a key nothing is stored.
        plain, _spin, _combo = _section(expanded=True)
        self.assertTrue(plain.is_expanded())

    def test_env_variable_selects_an_ini_file(self):
        import os
        old = os.environ.get(cs.SETTINGS_FILE_ENV)
        os.environ[cs.SETTINGS_FILE_ENV] = self.path
        self.addCleanup(lambda: os.environ.pop(cs.SETTINGS_FILE_ENV, None)
                        if old is None else os.environ.__setitem__(cs.SETTINGS_FILE_ENV, old))
        settings = cs.enable_state_persistence()
        self.assertEqual(Path(settings.fileName()), Path(self.path))


class StyleTests(unittest.TestCase):
    def test_card_and_spin_helpers(self):
        box, form = style.card("Scan")
        self.assertIs(box.property(style.CARD_PROPERTY), True)
        self.assertEqual(box.title(), "Scan")
        spin = style.double_spin(0, 10, 6, 2.77, "GHz", tooltip="tip")
        self.assertEqual(spin.suffix(), " GHz")
        self.assertEqual(spin.decimals(), 6)
        self.assertEqual(spin.value(), 2.77)
        self.assertEqual(spin.toolTip(), "tip")

    def test_stylesheet_only_matches_marked_widgets(self):
        sheet = style.APP_STYLESHEET
        for selector in ("QGroupBox[card=\"true\"]", "QFrame#collapsibleSection",
                         "QToolButton#sectionToggle"):
            self.assertIn(selector, sheet)
        # No bare type selectors that would restyle pyqtgraph's widgets.
        for line in sheet.splitlines():
            stripped = line.strip()
            if stripped.endswith("{"):
                self.assertTrue(
                    "[card=" in stripped or "#" in stripped, stripped)

    def test_fit_column_width_includes_collapsed_section_bodies(self):
        content = QWidget()
        layout = style.form_layout(content)
        section, _spin, _combo = _section()
        layout.addRow(section)
        scroll = QScrollArea()
        scroll.setWidget(content)
        width = style.fit_column_width(scroll, content)
        self.assertGreaterEqual(width, section.body.sizeHint().width())


if __name__ == "__main__":
    unittest.main()
