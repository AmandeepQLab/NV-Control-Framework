"""Thread-safe lifecycle control for camera live-stream threads."""

import threading
import time


class StreamController:
    """Start, stop, and serialize one camera streaming thread.

    ``acquire_once`` holds an acquisition gate across the decision to acquire
    and the acquisition itself.  ``stop`` obtains that same gate before
    committing the shutdown event, so no additional acquisition can begin
    after a successful shutdown request.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._acquisition_lock = threading.Lock()
        self._thread = None

    @property
    def thread(self):
        return self._thread

    @property
    def is_streaming(self):
        thread = self._thread
        return (
            thread is not None
            and thread.is_alive()
            and not self._stop_event.is_set()
        )

    def start(self, target):
        """Start *target* unless an existing stream thread is still alive."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(target=target, daemon=True)
            self._thread.start()
            return True

    def acquire_once(self, acquire):
        """Run one stream acquisition unless shutdown has been committed."""
        with self._acquisition_lock:
            if self._stop_event.is_set():
                return False

            acquire()
            return True

    def wait(self, timeout_s):
        """Wait for the stream interval, returning early on shutdown."""
        return self._stop_event.wait(timeout_s)

    def stop(self, timeout_s):
        """Stop the thread within *timeout_s* or raise ``RuntimeError``."""
        if timeout_s <= 0:
            raise ValueError("stream shutdown timeout must be positive.")

        with self._lifecycle_lock:
            thread = self._thread

        if thread is None or not thread.is_alive():
            self._stop_event.set()
            return

        deadline = time.monotonic() + timeout_s
        remaining_s = max(0.0, deadline - time.monotonic())
        acquired = self._acquisition_lock.acquire(timeout=remaining_s)

        if not acquired:
            # Tell the loop to exit after its in-flight acquisition finishes.
            # The caller still receives an error because clean shutdown was not
            # confirmed within the requested bound.
            self._stop_event.set()
            raise RuntimeError("Camera stream shutdown failed: acquisition did not stop in time.")

        try:
            self._stop_event.set()
        finally:
            self._acquisition_lock.release()

        remaining_s = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining_s)
        if thread.is_alive():
            raise RuntimeError("Camera stream shutdown failed: stream thread did not terminate in time.")
