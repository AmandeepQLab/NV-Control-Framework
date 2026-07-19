"""Coordinate exclusive camera use between live view and imaging experiments."""

from contextlib import contextmanager
import threading
import weakref


class _CameraState:
    def __init__(self):
        self.experiment_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.experiment_owns_camera = False
        self.live_view_controller = None


_states_lock = threading.Lock()
_states = weakref.WeakKeyDictionary()


def _get_state(camera):
    """Return the process-wide ownership state for *camera*."""
    with _states_lock:
        state = _states.get(camera)
        if state is None:
            state = _CameraState()
            _states[camera] = state
        return state


def register_live_view_controller(camera, controller):
    """Register the GUI-side controller for a camera's complete live view.

    ``controller`` is intentionally duck-typed: it must provide ``suspend()``,
    which returns whether GUI polling was active, and ``resume(was_running)``.
    This keeps camera ownership reusable and independent of any GUI class.
    """
    state = _get_state(camera)
    with state.state_lock:
        state.live_view_controller = controller


@contextmanager
def exclusive_camera_access(camera):
    """Pause live view and reserve *camera* until the context exits.

    The previous live-stream state is restored even when acquisition raises.
    Imaging experiments should hold this context around their entire run.
    """
    state = _get_state(camera)
    state.experiment_lock.acquire()
    was_streaming = False
    timer_was_running = False
    timer_suspended = False
    ownership_acquired = False

    try:
        with state.state_lock:
            state.experiment_owns_camera = True
            was_streaming = bool(getattr(camera, "streaming", False))
            controller = state.live_view_controller
            if controller is not None:
                timer_was_running = controller.suspend()
                timer_suspended = True
            if was_streaming:
                camera.stop_stream()
            ownership_acquired = True

        yield
    finally:
        with state.state_lock:
            try:
                if ownership_acquired and was_streaming:
                    camera.start_stream()
            finally:
                try:
                    if timer_suspended:
                        state.live_view_controller.resume(timer_was_running)
                finally:
                    state.experiment_owns_camera = False
                    state.experiment_lock.release()


def start_live_stream_if_available(camera):
    """Start live view unless an imaging experiment currently owns *camera*.

    Returns ``True`` when the stream was started and ``False`` when ownership
    prevents a concurrent live acquisition.
    """
    state = _get_state(camera)
    with state.state_lock:
        if state.experiment_owns_camera:
            return False

        camera.start_stream()
        return True
