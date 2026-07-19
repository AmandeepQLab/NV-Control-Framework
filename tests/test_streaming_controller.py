"""Unit tests for bounded, exclusive live-stream shutdown."""

import threading
import time
import unittest

from hardware.camera.streaming import StreamController


class StreamingControllerTests(unittest.TestCase):
    def test_stop_prevents_a_second_acquisition(self):
        controller = StreamController()
        first_acquisition_started = threading.Event()
        release_first_acquisition = threading.Event()
        acquisitions = []

        def stream_loop():
            while not controller.wait(0):
                def acquire():
                    acquisitions.append("frame")
                    first_acquisition_started.set()
                    release_first_acquisition.wait()

                if not controller.acquire_once(acquire):
                    break

        controller.start(stream_loop)
        self.assertTrue(first_acquisition_started.wait(1))

        stop_thread = threading.Thread(target=lambda: controller.stop(1))
        stop_thread.start()
        time.sleep(0.02)
        release_first_acquisition.set()
        stop_thread.join(1)

        self.assertFalse(stop_thread.is_alive())
        self.assertEqual(acquisitions, ["frame"])
        self.assertFalse(controller.is_streaming)

    def test_duplicate_start_does_not_create_another_thread(self):
        controller = StreamController()
        release = threading.Event()

        def stream_loop():
            controller.acquire_once(release.wait)

        self.assertTrue(controller.start(stream_loop))
        self.assertFalse(controller.start(stream_loop))
        release.set()
        controller.stop(1)

    def test_stop_raises_when_acquisition_exceeds_timeout(self):
        controller = StreamController()
        acquisition_started = threading.Event()
        release = threading.Event()

        def stream_loop():
            controller.acquire_once(lambda: (acquisition_started.set(), release.wait()))

        controller.start(stream_loop)
        self.assertTrue(acquisition_started.wait(1))

        with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
            controller.stop(0.01)

        release.set()
        controller.thread.join(1)


if __name__ == "__main__":
    unittest.main()
