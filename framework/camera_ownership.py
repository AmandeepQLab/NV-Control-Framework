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
        self.state_restorer = None


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


def register_camera_state_restorer(camera, restorer):
    """Register the Main Window callback that restores user camera settings.

    The callback is invoked while an experiment lease still owns an idle
    camera, immediately before live view is resumed.
    """
    state = _get_state(camera)
    with state.state_lock:
        state.state_restorer = restorer


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
                if ownership_acquired and state.state_restorer is not None:
                    state.state_restorer()
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


def apply_live_acquisition_state(camera, apply_state):
    """Apply Main-Window settings without racing live view or experiments.

    Returns ``False`` if an experiment currently owns the camera.  When live
    view was active it is suspended, the camera is made idle, settings are
    applied, and the exact prior live-view state is resumed.
    """
    state = _get_state(camera)
    with state.experiment_lock:
        with state.state_lock:
            if state.experiment_owns_camera:
                return False
            controller = state.live_view_controller
            timer_was_running = controller.suspend() if controller else False
            was_streaming = bool(getattr(camera, "streaming", False))
            try:
                if was_streaming:
                    camera.stop_stream()
                apply_state()
                if was_streaming:
                    camera.start_stream()
            finally:
                if controller is not None:
                    controller.resume(timer_was_running)
    return True
