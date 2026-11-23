"""Queue panel for the Zero Field window: entry list and queue controls.

Display and controls only. ZeroFieldWindow owns the entries and runs them;
see gui/zero_field_queue.py for the entry model.
"""

from pathlib import Path

from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.zero_field_queue import ON_FAILURE_CHOICES, RUNNING


_RUNNING_BRUSH = QBrush(QColor(255, 236, 150))


class ZeroFieldQueueWidget(QWidget):
    """Table of queued runs plus the buttons that edit and run the queue."""

    COLUMNS = ("#", "Settings", "Status", "Output file")

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 4)

        side = QVBoxLayout()
        layout.addLayout(side, 1)

        edit_grid = QGridLayout()
        side.addLayout(edit_grid)
        self.add_button = QPushButton("Add to queue")
        self.add_button.setToolTip(
            "Store the current settings, output directory and save options as "
            "a new entry. Changing the form afterwards does not change it."
        )
        self.remove_button = QPushButton("Remove")
        self.move_up_button = QPushButton("Move up")
        self.move_down_button = QPushButton("Move down")
        self.duplicate_button = QPushButton("Duplicate")
        self.clear_button = QPushButton("Clear pending")
        self.clear_button.setToolTip("Remove every pending entry.")
        self.load_into_form_button = QPushButton("Load entry into form")
        self.load_into_form_button.setToolTip(
            "Copy the selected entry's settings back into the form, to edit "
            "and add again. The entry itself is not changed."
        )
        self.save_queue_button = QPushButton("Save queue…")
        self.load_queue_button = QPushButton("Load queue…")
        buttons = (
            self.add_button, self.duplicate_button,
            self.remove_button, self.clear_button,
            self.move_up_button, self.move_down_button,
            self.load_into_form_button, self.save_queue_button,
            self.load_queue_button,
        )
        for position, button in enumerate(buttons):
            edit_grid.addWidget(button, position // 2, position % 2)

        options = QGridLayout()
        side.addLayout(options)
        options.addWidget(QLabel("Pause between runs (s):"), 0, 0)
        self.pause_spin = QDoubleSpinBox()
        self.pause_spin.setRange(0.0, 86400.0)
        self.pause_spin.setDecimals(1)
        self.pause_spin.setValue(0.0)
        options.addWidget(self.pause_spin, 0, 1)
        options.addWidget(QLabel("On failure:"), 1, 0)
        self.on_failure_combo = QComboBox()
        self.on_failure_combo.addItems(ON_FAILURE_CHOICES)
        self.on_failure_combo.setToolTip(
            "A run fails if it raises an error or is refused before starting "
            "(field out of range, unusable output directory). A stopped run "
            "is not a failure."
        )
        options.addWidget(self.on_failure_combo, 1, 1)

        run_row = QHBoxLayout()
        side.addLayout(run_row)
        self.run_queue_button = QPushButton("Run queue")
        self.run_queue_button.setStyleSheet(
            "background-color: green; color: white; font-weight: bold;"
        )
        self.stop_current_button = QPushButton("Stop current run")
        self.stop_current_button.setToolTip(
            "Stop the running entry (marked stopped) and continue with the next."
        )
        self.stop_queue_button = QPushButton("Stop queue")
        self.stop_queue_button.setToolTip(
            "Stop the running entry and do not start any more. Entries not "
            "yet run stay pending."
        )
        run_row.addWidget(self.run_queue_button)
        run_row.addWidget(self.stop_current_button)
        run_row.addWidget(self.stop_queue_button)

        self.queue_status_label = QLabel("Queue: idle")
        font = self.queue_status_label.font()
        font.setBold(True)
        self.queue_status_label.setFont(font)
        side.addWidget(self.queue_status_label)
        side.addStretch()

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
