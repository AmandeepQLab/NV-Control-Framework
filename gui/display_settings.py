"""Persisted image-display preferences (the "display" section of setupInfo.json).

Each image display keeps its colormap choice under its own key. Values are
stored lowercase -- the pyqtgraph/matplotlib colormap name -- and shown in
the combo box with the capitalisation the Zero Field widget uses.

Saving rewrites only the "display" section's text, so the rest of
setupInfo.json (its formatting included) is left byte-for-byte intact. If
that targeted edit can't be made safely, the whole file is re-serialised
instead -- same content, normalised formatting.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

import pyqtgraph as pg

LOGGER = logging.getLogger(__name__)

# Must match ZeroFieldWidget.colormap_combo, the reference implementation
# (tests/test_colormap_selectors.py checks it does).
COLORMAP_NAMES = ["Gray", "Viridis", "Plasma", "Inferno", "Magma"]
DEFAULT_COLORMAP = "Gray"

LIVE_VIEW_COLORMAP_KEY = "live_view_colormap"
ZFE_COLORMAP_KEY = "zfe_colormap"


def colormap_for(name):
    """The pyqtgraph ColorMap for a combo-box entry such as "Viridis"."""
    return pg.colormap.get(name.lower(), source="matplotlib")


def load_colormap(cfg, key):
    """Combo-box text for the saved colormap; "Gray" if absent or unknown.

    cfg is a ConfigManager (or None, e.g. in tests without a config).
    """
    stored = None
    try:
        stored = cfg.config["display"][key]
    except (AttributeError, KeyError, TypeError):
        pass
    if isinstance(stored, str):
        for name in COLORMAP_NAMES:
            if name.lower() == stored.strip().lower():
                return name
        LOGGER.warning("Unknown colormap %r for display.%s; using Gray.", stored, key)
    return DEFAULT_COLORMAP


def select_colormap(combo, name):
    """Show name in combo without emitting currentTextChanged."""
    index = combo.findText(name)
    if index < 0:
        index = combo.findText(DEFAULT_COLORMAP)
    combo.blockSignals(True)
    try:
        combo.setCurrentIndex(index)
    finally:
        combo.blockSignals(False)


def save_colormap(cfg, key, name):
    """Persist one display's colormap. Never raises: a failed save is logged."""
    config_path = getattr(cfg, "config_path", None)
    if config_path is None:
        return
    value = name.lower()
    config = getattr(cfg, "config", None)
    if isinstance(config, dict):
        display = config.setdefault("display", {})
        if isinstance(display, dict):
            display[key] = value
    try:
        path = Path(config_path)
        with open(path, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        _write_atomically(path, update_display_text(text, key, value))
    except (OSError, ValueError) as error:
        LOGGER.warning("Could not save display.%s to %s: %s", key, config_path, error)


def update_display_text(text, key, value):
    """Return text with display[key] = value, changing nothing else if possible."""
    data = json.loads(text)
    expected = json.loads(text)
    if not isinstance(expected.get("display"), dict):
        expected["display"] = {}
    expected["display"][key] = value

    try:
        updated = _edit_display_text(text, data, key, value)
        if json.loads(updated) == expected:
            return updated
    except (ValueError, IndexError):
        pass
    LOGGER.warning("Rewriting the config file in full to store display.%s.", key)
    return json.dumps(expected, indent=4) + "\n"


def _edit_display_text(text, data, key, value):
    entry = f'"{key}": {json.dumps(value)}'
    block = re.search(r'"display"\s*:\s*\{[^{}]*\}', text)
    if isinstance(data.get("display"), dict) and block is not None:
        body = block.group(0)
        key_pattern = re.compile(r'("%s"\s*:\s*)"(?:[^"\\]|\\.)*"' % re.escape(key))
        if key_pattern.search(body):
            new_body = key_pattern.sub(
                lambda match: match.group(1) + json.dumps(value), body, count=1
            )
        else:
            opening = body.index("{") + 1
            inner = body[opening:-1]
            # Appended as the section's last member.
            separator = "," if inner.strip() else ""
            new_body = body[:opening] + inner.rstrip() + f"{separator}\n        {entry}\n    }}"
        return text[: block.start()] + new_body + text[block.end():]

    # No display section yet: append one as the root object's last member.
    closing = text.rindex("}")
    head = text[:closing].rstrip()
    return head + f',\n    "display": {{\n        {entry}\n    }}\n' + text[closing:]


def _write_atomically(path, text):
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
