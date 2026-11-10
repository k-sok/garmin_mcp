"""Report Garmin's HTTP status for a failed request in a file (``GARMIN_STATUS_FILE``).

A host that runs this server for several people needs to know when Garmin
refuses a call: 401 (the login is no longer accepted), 429 (too many requests)
or a block in front of Garmin. The tool result is no source for that: it
carries Garmin's own text and the library's exception text, which Garmin data
can imitate (a shared workout named "API Error 429"). So the status is taken
from the HTTP response object of the final request, and nothing else.

When ``GARMIN_STATUS_FILE`` is an absolute path, a failed request writes
``{"status": <int>, "blocked": <bool>}`` there, atomically, mode 0600, without
following a link. ``blocked`` is true for a 403 whose body is not JSON (a
firewall page, not Garmin's API). The file keeps the most severe status since
it was last taken away by the host: 429 or blocked, then 401, then the rest.
Failures to write are ignored: the tool error stays as it is.

How: a response hook on the API session records the status and content type of
every response of one ``Client._run_request``. The last one is the final
response: when the library refreshed the tokens and its retry succeeded, that
is a success and nothing is written.
"""

import json
import os
import threading
import uuid

ENV = "GARMIN_STATUS_FILE"
_MAX_BYTES = 256
_lock = threading.Lock()
_local = threading.local()
_MARK = "_garmin_mcp_status_installed"


def _severity(status, blocked):
    if blocked or status == 429:
        return 3
    if status == 401:
        return 2
    return 1


def _is_json(content_type, body):
    if "json" in (content_type or "").lower():
        return True
    try:
        json.loads(body)
    except (ValueError, TypeError):
        return False
    return True


def _read_existing(path):
    """The status file's content, or None when it is absent, linked or not ours."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        data = os.read(fd, _MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > _MAX_BYTES:
        return None
    try:
        parsed = json.loads(data)
        status, blocked = parsed["status"], parsed["blocked"]
    except (ValueError, KeyError, TypeError):
        return None
    if type(status) is not int or type(blocked) is not bool:
        return None
    return status, blocked


def report_status(status, blocked=False):
    """Write ``status`` to ``$GARMIN_STATUS_FILE`` unless a more severe one waits there."""
    path = os.environ.get(ENV, "")
    if not path or not os.path.isabs(path):
        return
    with _lock:
        try:
            existing = _read_existing(path)
            if existing is not None and _severity(*existing) > _severity(status, blocked):
                return
            tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps({"status": status, "blocked": blocked}))
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return


def _record_response(response, *args, **kwargs):
    calls = getattr(_local, "responses", None)
    if calls is None:
        return
    try:
        status = response.status_code
        # The body matters only to tell a 403 that is not JSON (a block) from Garmin's own.
        body = response.content[:65536] if status == 403 else b""
        calls.append((status, response.headers.get("Content-Type"), body))
    except Exception:  # noqa: BLE001 - recording must never break a request
        calls.append(None)


def _final_status(responses):
    """``(status, blocked)`` of the final failed response, or None."""
    if not responses or responses[-1] is None:
        return None
    status, content_type, body = responses[-1]
    if not isinstance(status, int) or status < 400:
        return None
    return status, status == 403 and not _is_json(content_type, body)


def install():
    """Wrap ``garminconnect``'s ``Client._run_request`` once; harmless without the switch."""
    from garminconnect import client as client_module

    cls = client_module.Client
    original = cls._run_request
    if getattr(original, _MARK, False):
        return

    def _run_request(self, method, path, **kwargs):
        if not os.environ.get(ENV):
            return original(self, method, path, **kwargs)
        hooks = self._api_session.hooks.setdefault("response", [])
        if _record_response not in hooks:
            hooks.append(_record_response)
        outer = getattr(_local, "responses", None)
        _local.responses = responses = []
        try:
            return original(self, method, path, **kwargs)
        except BaseException:
            final = _final_status(responses)
            if final is not None:
                report_status(*final)
            raise
        finally:
            _local.responses = outer

    setattr(_run_request, _MARK, True)
    _run_request.__wrapped__ = original
    cls._run_request = _run_request
