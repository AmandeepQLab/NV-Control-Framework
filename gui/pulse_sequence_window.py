from PyQt6.QtWidgets import QDialog, QVBoxLayout
import pyqtgraph as pg


class PulseSequenceWindow(QDialog):

    def __init__(self, config, parent=None):
        super().__init__(parent)

        self.setWindowTitle("ODMR Pulse Sequence")
        self.resize(1100, 500)

        self.config = config

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.plot = pg.PlotWidget()
        layout.addWidget(self.plot)

        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.35)
        self.plot.setLabel("bottom", "Time", units="ms")
        self.plot.setLabel("left", "Channels")

        self.draw_sequence()

    def draw_pulse(self, start_ms, duration_ms, y, height, color, label=None):

        x = [
            start_ms,
            start_ms,
            start_ms + duration_ms,
            start_ms + duration_ms
        ]

        yvals = [
            y,
            y + height,
            y + height,
            y
        ]

        self.plot.plot(
            x,
            yvals,
            pen=pg.mkPen(color, width=3)
        )

        if label is not None:
            text = pg.TextItem(label, color=color, anchor=(0.5, 1.2))
            text.setPos(start_ms + duration_ms / 2, y + height)
            self.plot.addItem(text)

    def draw_sequence(self):

        exposure_s = self.config.get("exposure_s", 0.02)
        trigger_delay_s = self.config.get("trigger_delay_s", 0.05)
        frame_gap_s = self.config.get("frame_gap_s", 0.05)
        repeats = self.config.get("repeats", 1)
        pulse_lead_s = self.config.get("pulse_lead_s", 0.002)
        pulse_tail_s = self.config.get("pulse_tail_s", 0.002)

        pulse_lead_ms = pulse_lead_s * 1000
        pulse_tail_ms = pulse_tail_s * 1000

        exposure_ms = exposure_s * 1000
        trigger_delay_ms = trigger_delay_s * 1000
        frame_gap_ms = frame_gap_s * 1000

        y_laser = 3
        y_mw = 2
        y_cam = 1
        h = 0.6

        self.plot.clear()

        self.plot.setYRange(0.5, 4.2)
        self.plot.getAxis("left").setTicks([
            [
                (y_cam + h / 2, "Camera trigger"),
                (y_mw + h / 2, "MW switch"),
                (y_laser + h / 2, "Green laser"),
            ]
        ])

        t = trigger_delay_ms

        # Trigger delay marker
        delay_text = pg.TextItem(
            f"Trigger delay = {trigger_delay_ms:.1f} ms",
            color=(80, 80, 80),
            anchor=(0, 1)
        )
        delay_text.setPos(0, 4.0)
        self.plot.addItem(delay_text)

        self.plot.addLine(
            x=trigger_delay_ms,
            pen=pg.mkPen((120, 120, 120), width=2, style=pg.QtCore.Qt.PenStyle.DashLine)
        )

        for r in range(repeats):

            # =========================
            # OFF FRAME
            # =========================
            self.draw_pulse(
                t,
                exposure_ms,
                y_laser,
                h,
                (0, 180, 0),
                label=f"Laser OFF-frame R{r+1}"
            )

            self.draw_pulse(
                t,
                exposure_ms,
                y_cam,
                h,
                (30, 80, 220),
                label="Camera"
            )

            off_label = pg.TextItem("MW OFF", color=(0, 0, 0), anchor=(0.5, -0.2))
            off_label.setPos(t + exposure_ms / 2, 0.75)
            self.plot.addItem(off_label)

            t += exposure_ms

            # Frame gap
            gap_text = pg.TextItem(
                f"gap {frame_gap_ms:.1f} ms",
                color=(120, 120, 120),
                anchor=(0.5, 0.5)
            )
            gap_text.setPos(t + frame_gap_ms / 2, 3.9)
            self.plot.addItem(gap_text)

            t += frame_gap_ms

            # =========================
            # ON FRAME
            # =========================
            self.draw_pulse(
                t,
                exposure_ms,
                y_laser,
                h,
                (0, 180, 0),
                label=f"Laser ON-frame R{r+1}"
            )

            self.draw_pulse(
                t,
                exposure_ms,
                y_mw,
                h,
                (220, 80, 30),
                label="MW ON"
            )

            self.draw_pulse(
                t,
                exposure_ms,
                y_cam,
                h,
                (30, 80, 220),
                label="Camera"
            )

            on_label = pg.TextItem("MW ON", color=(0, 0, 0), anchor=(0.5, -0.2))
            on_label.setPos(t + exposure_ms / 2, 0.75)
            self.plot.addItem(on_label)

            t += exposure_ms

            # Frame gap after ON frame
            if r < repeats - 1:
                gap_text = pg.TextItem(
                    f"gap {frame_gap_ms:.1f} ms",
                    color=(120, 120, 120),
                    anchor=(0.5, 0.5)
                )
                gap_text.setPos(t + frame_gap_ms / 2, 3.9)
                self.plot.addItem(gap_text)

            t += frame_gap_ms

        total_ms = t

        self.plot.setXRange(0, total_ms * 1.05)

        title = pg.TextItem(
            f"ODMR repeated OFF/ON sequence | repeats = {repeats} | exposure = {exposure_ms:.1f} ms",
            color=(0, 0, 0),
            anchor=(0, 0)
        )
        title.setPos(0, 4.15)
        self.plot.addItem(title)
