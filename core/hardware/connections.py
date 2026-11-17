"""Release runner-owned serial connections without hiding the original error."""

from contextlib import contextmanager
from functools import wraps


def disconnect_devices(owner):
    """Try every owned device, retaining failed handles so closure can be retried."""
    owner._closed = True
    owner._mb_spm_contents = None
    errors = []
    for name in ("spm", "cell_valve"):
        device = getattr(owner, name, None)
        if device is None:
            continue
        native = getattr(device, "pump", None) or getattr(device, "valve", None)
        try:
            result = device.disconnect()
            if result is False or getattr(native, "connected", False) is True:
                raise RuntimeError(f"{name} still reports an open connection")
        except Exception as exc:
            errors.append((name, exc))
        else:
            setattr(owner, name, None)
    if errors:
        detail = "; ".join(f"{name}: {exc}" for name, exc in errors)
        raise RuntimeError(f"Could not release hardware connections: {detail}") from errors[0][1]


def close_runner(runner):
    """Also handle runner instances retained from an earlier application version."""
    if runner is None:
        return
    close = getattr(runner, "close", None)
    if callable(close):
        close()
    else:
        disconnect_devices(runner)


def _close_after_error(runner):
    try:
        close_runner(runner)
    except Exception as exc:
        # The experiment/initialization failure remains the primary exception.
        try:
            print(f"Hardware disconnection also failed: {exc}")
        except Exception:
            pass


def disconnect_on_error(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            _close_after_error(self)
            raise
    return guarded


@contextmanager
def campaign_connections(runner):
    """Keep connections between experiments, then release them on every exit."""
    try:
        yield runner
    except BaseException:
        _close_after_error(runner)
        raise
    else:
        close_runner(runner)
