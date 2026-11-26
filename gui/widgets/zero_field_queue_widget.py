"""Queue panel for the Zero Field window: entry list and queue controls.

Display and controls only. ZeroFieldWindow owns the entries and runs them;
see gui/zero_field_queue.py for the entry model.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from gui import style
from gui.widgets import run_button
from gui.zero_field_queue import ON_FAILURE_CHOICES, RUNNING


_RUNNING_BRUSH = QBrush(QColor(255, 236, 150))


def _tipped(text, field):
    label = QLabel(text)
    label.setToolTip(field.toolTip())
    return label


class ZeroFieldQueueWidget(QWidget):
    """Table of queued runs plus the buttons that edit and run the queue."""

    COLUMNS = ("#", "Settings", "Status", "Output file")

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip(
            "Queued runs in list order: settings summary, status, and the "
            "file each run wrote. Select a row to edit or reorder it.")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 3)

        # Two cards beside the table, each a few rows high, so the dock
        # stays low (about 180 px) and the plots keep their height.
        side = QHBoxLayout()
        side.setSpacing(style.COLUMN_SPACING)
        layout.addLayout(side, 2)

        # ---- Edit the list, and queue files --------------------------------
        edit_box, edit_layout = style.card("Entries", form=False)
        side.addWidget(edit_box, 1, Qt.AlignmentFlag.AlignTop)
        edit_grid = QGridLayout()
        edit_grid.setSpacing(4)
        edit_layout.addLayout(edit_grid)
        self.add_button = QPushButton("Add to queue")
        self.add_button.setToolTip(
            "Store the current settings, output directory and save options as "
            "a new entry. Changing the form afterwards does not change it."
        )
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip(
            "Remove the selected entry (not the one running).")
        self.move_up_button = QPushButton("Move up")
        self.move_up_button.setToolTip(
            "Move the selected entry one place earlier. Pending entries run in "
            "list order, picked when each run starts.")
        self.move_down_button = QPushButton("Move down")
        self.move_down_button.setToolTip("Move the selected entry one place later.")
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setToolTip(
            "Add a pending copy of the selected entry (same settings and "
            "output folder).")
        self.clear_button = QPushButton("Clear pending")
        self.clear_button.setToolTip("Remove every pending entry.")
        self.load_into_form_button = QPushButton("Load entry into form")
        self.load_into_form_button.setToolTip(
            "Copy the selected entry's settings back into the form, to edit "
            "and add again. The entry itself is not changed."
        )
        self.save_queue_button = QPushButton("Save queue…")
        self.save_queue_button.setToolTip(
            "Save every entry's settings, output folder and stability check "
            "to a JSON queue file (run status is not saved).")
        self.load_queue_button = QPushButton("Load queue…")
        self.load_queue_button.setToolTip(
            "Replace the list with the entries of a saved queue file, all "
            "pending. A file with any invalid or unreachable entry is refused "
            "as a whole.")
        buttons = (
            (self.add_button, self.duplicate_button, self.remove_button),
            (self.move_up_button, self.move_down_button, self.clear_button),
            (self.load_into_form_button, self.save_queue_button,
             self.load_queue_button),
        )
        for row, row_buttons in enumerate(buttons):
            for column, button in enumerate(row_buttons):
                edit_grid.addWidget(button, row, column)

        # ---- Run ------------------------------------------------------------
        run_box, options = style.card("Run")
        options.setVerticalSpacing(4)
        side.addWidget(run_box, 1, Qt.AlignmentFlag.AlignTop)

        self.run_queue_button = QPushButton("Run queue")
        self.run_queue_button.setStyleSheet(
            run_button.run_button_stylesheet(run_button.IDLE))
        self.run_queue_button.setToolTip(
            "Run the pending entries one after another, in list order. Each "
            "entry's field range is checked again just before it starts.")
        self.stop_current_button = QPushButton("Stop current run")
        self.stop_current_button.setToolTip(
            "Stop the running entry (marked stopped) and continue with the next."
        )
        self.stop_queue_button = QPushButton("Stop queue")
        self.stop_queue_button.setToolTip(
            "Stop the running entry and do not start any more. Entries not "
            "yet run stay pending."
        )
        options.addRow(style.button_row(self.run_queue_button,
                                        self.stop_current_button,
                                        self.stop_queue_button))

        self.pause_spin = style.double_spin(
            0.0, 86400.0, 1, 0.0, "s",
            "Wait between one run's end and the next run's start, in seconds "
            "(e.g. to let the setup settle).")
        options.addRow(_tipped("Pause between runs:", self.pause_spin), self.pause_spin)
        self.on_failure_combo = QComboBox()
        self.on_failure_combo.addItems(ON_FAILURE_CHOICES)
        self.on_failure_combo.setToolTip(
            "A run fails if it raises an error or is refused before starting "
            "(field out of range, unusable output directory). A stopped run "
            "is not a failure."
        )
        options.addRow(_tipped("On failure:", self.on_failure_combo),
                       self.on_failure_combo)

        self.queue_status_label = QLabel("Queue: idle")
        self.queue_status_label.setToolTip(
            "Queue progress: run k of N, pausing, or the finished summary.")
        self.queue_status_label.setWordWrap(True)
        font = self.queue_status_label.font()
        font.setBold(True)
        self.queue_status_label.setFont(font)
        options.addRow(self.queue_status_label)

    def selected_row(self):
        """Index of the selected entry, or None."""
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def select_row(self, row):
        if row is None or not 0 <= row < self.table.rowCount():
            self.table.clearSelection()
            return
        self.table.selectRow(row)

    def set_entries(self, entries):
        """Redraw the list; the running entry is highlighted in bold."""
        selected = self.selected_row()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                str(row + 1),
                entry.summary(),
                entry.status + (f": {entry.message}" if entry.message else ""),
                Path(entry.output_path).name if entry.output_path else "",
            )
            running = entry.status == RUNNING
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 2):
                    item.setToolTip(value)
                if column == 3 and entry.output_path:
                    item.setToolTip(entry.output_path)
                if running:
                    item.setBackground(_RUNNING_BRUSH)
                    bold = QFont(item.font())
                    bold.setBold(True)
                    item.setFont(bold)
                self.table.setItem(row, column, item)
        self.select_row(selected if selected is not None and selected < len(entries) else None)
