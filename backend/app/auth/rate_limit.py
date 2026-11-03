import threading
import time
from collections import defaultdict, deque


class AttemptLimiter:
    """In-memory sliding-window counter of failed attempts per key.

    State lives in the backend process, so it resets on restart and is
    not shared between multiple worker processes.
    """

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        attempts = self.attempts[key]
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        if not attempts:
            self.attempts.pop(key, None)
        return attempts

    def retry_after(self, key: str) -> int:
        """Seconds until the key may try again, or 0 if not blocked."""
        now = time.monotonic()
        with self.lock:
            attempts = self._prune(key, now)
            if len(attempts) < self.max_attempts:
                return 0
            return max(1, int(attempts[0] + self.window_seconds - now))

    def record_failure(self, key: str) -> None:
        with self.lock:
            self.attempts[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self.lock:
            self.attempts.pop(key, None)
