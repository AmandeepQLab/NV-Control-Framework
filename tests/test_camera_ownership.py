"""Unit tests for exclusive imaging-camera ownership."""

import unittest

from framework.camera_ownership import (
    exclusive_camera_access,
    register_live_view_controller,
    start_live_stream_if_available,
)


class FakeCamera:
    def __init__(self, streaming=False):
        self.streaming = streaming
        self.start_calls = 0
        self.stop_calls = 0

    def start_stream(self):
        self.start_calls += 1
        self.streaming = True

    def stop_stream(self):
        self.stop_calls += 1
        self.streaming = False


class FakeLiveViewController:
    def __init__(self, timer_running=False):
        self.timer_running = timer_running
        self.suspend_calls = 0
        self.resume_arguments = []

    def suspend(self):
        self.suspend_calls += 1
        was_running = self.timer_running
        self.timer_running = False
        return was_running

    def resume(self, was_running):
        self.resume_arguments.append(was_running)
        if was_running:
            self.timer_running = True


class CameraOwnershipTests(unittest.TestCase):
    def test_pauses_and_restores_an_existing_live_stream(self):
        camera = FakeCamera(streaming=True)

        with exclusive_camera_access(camera):
            self.assertFalse(camera.streaming)
            self.assertFalse(start_live_stream_if_available(camera))

        self.assertTrue(camera.streaming)
        self.assertEqual(camera.stop_calls, 1)
        self.assertEqual(camera.start_calls, 1)

    def test_restores_live_stream_when_acquisition_raises(self):
        camera = FakeCamera(streaming=True)

        with self.assertRaisesRegex(RuntimeError, "acquisition failed"):
            with exclusive_camera_access(camera):
                raise RuntimeError("acquisition failed")

        self.assertTrue(camera.streaming)

    def test_leaves_an_inactive_live_stream_stopped(self):
        camera = FakeCamera(streaming=False)

        with exclusive_camera_access(camera):
            self.assertFalse(camera.streaming)

        self.assertFalse(camera.streaming)
        self.assertEqual(camera.start_calls, 0)

    def test_restores_timer_only_when_live_view_was_active(self):
        camera = FakeCamera(streaming=True)
        controller = FakeLiveViewController(timer_running=True)
        register_live_view_controller(camera, controller)

        with exclusive_camera_access(camera):
            self.assertFalse(camera.streaming)
            self.assertFalse(controller.timer_running)

        self.assertTrue(camera.streaming)
        self.assertTrue(controller.timer_running)
        self.assertEqual(controller.resume_arguments, [True])

    def test_keeps_timer_off_when_live_view_was_inactive(self):
        camera = FakeCamera(streaming=False)
        controller = FakeLiveViewController(timer_running=False)
        register_live_view_controller(camera, controller)

        with exclusive_camera_access(camera):
            self.assertFalse(controller.timer_running)

        self.assertFalse(camera.streaming)
        self.assertFalse(controller.timer_running)
        self.assertEqual(controller.resume_arguments, [False])


if __name__ == "__main__":
    unittest.main()
