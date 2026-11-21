"""Server errors to adminPal, fire-and-forget.

capture() never blocks and never raises: it puts the error on a bounded queue drained by one
daemon thread. The same fingerprint is sent at most once per `min_interval` seconds per process,
so a crash loop does not flood adminPal (adminPal groups by fingerprint anyway).
"""
import os
import queue
import re
import threading
import time
import traceback

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_UUID = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
_NUMBER = re.compile(r'\d+')
_LIBRARY_DIRS = (f'{os.sep}site-packages{os.sep}', f'{os.sep}dist-packages{os.sep}')


def scrub(message: str) -> str:
    """First line only, without emails, uuids or numbers (they carry user data and ids), at most 200 chars."""
    line = (message or '').strip().splitlines()[0] if (message or '').strip() else ''
    line = _EMAIL.sub('<email>', line)
    line = _UUID.sub('<id>', line)
    line = _NUMBER.sub('<n>', line)
    return line[:200]


def _in_app(filename: str, root: str) -> bool:
    path = os.path.abspath(filename)
    return path.startswith(root + os.sep) and not any(d in path for d in _LIBRARY_DIRS)


def fingerprint(exc: BaseException, app_root=None) -> str:
    """Exception type + file and function of the innermost app frame.

    No line number (it moves with every deploy), never the message (it carries ids).
    With app_root, the file is relative to it and library code (site-packages) is skipped.
    """
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if app_root:
        root = os.path.abspath(app_root)
        in_app = [f for f in frames if _in_app(f.filename, root)]
        if in_app:
            f = in_app[-1]
            rel = os.path.relpath(os.path.abspath(f.filename), root).replace(os.sep, '/')
            return f'{type(exc).__name__}:{rel}:{f.name}'[:128]
    if frames:
        f = frames[-1]
        return f'{type(exc).__name__}:{os.path.basename(f.filename)}:{f.name}'[:128]
    return f'{type(exc).__name__}:unknown'


class ErrorReporter:
    def __init__(self, client, app_version=None, app_root=None, min_interval=10.0, max_queue=50, idle_seconds=30.0):
        self.client = client
        self.idle_seconds = idle_seconds
        self.app_version = app_version
        self.app_root = app_root
        self.min_interval = min_interval
        self._queue = queue.Queue(maxsize=max_queue)
        self._last_sent = {}
        self._lock = threading.Lock()
        self._thread = None

    def pending(self) -> int:
        return self._queue.qsize()

    def _ensure_thread(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._drain, name='palkit-errors', daemon=True)
                self._thread.start()

    def capture(self, exc: BaseException, route=None) -> None:
        try:
            fp = fingerprint(exc, self.app_root)
            now = time.monotonic()
            with self._lock:
                last = self._last_sent.get(fp)
                if last is not None and now - last < self.min_interval:
                    return
                self._last_sent[fp] = now
                if len(self._last_sent) > 1000:
                    self._last_sent.clear()
            name = type(exc).__name__
            payload = {'source': 'server_error', 'platform': 'api', 'fingerprint': fp,
                       'title': f'{name} in {route}' if route else name,
                       'text': f'{name}: {scrub(str(exc))}'}
            if self.app_version:
                payload['app_version'] = str(self.app_version)[:64]
            if route:
                payload['route'] = str(route)[:300]
            self._queue.put_nowait(payload)
            self._ensure_thread()
        except Exception:  # noqa: BLE001 — reporting must never break the pal
            pass

    def _drain(self):
        while True:
            try:
                payload = self._queue.get(timeout=self.idle_seconds)
            except queue.Empty:
                with self._lock:
                    # Hand over under the lock: a capture() racing this exit either saw the queue
                    # non-empty (we keep draining) or finds _thread None and starts a new one.
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            try:
                self.client.send_server_error(payload)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._queue.task_done()
