"""Limits for interactive, read-only integration scans."""
import time
import threading

# Slow upstream reads cannot create an unbounded number of abandoned workers.
# A late read may finish in the background, but it never publishes or updates DB.
_read_slots = threading.BoundedSemaphore(8)


class ReadBudgetExhausted(ValueError):
    pass


class ReadBudget:
    def __init__(self, max_requests, max_seconds, request_timeout):
        self.max_requests = max(0, min(int(max_requests), 200))
        self.deadline = time.monotonic() + max(0, min(float(max_seconds), 120))
        self.request_timeout = max(0.01, min(float(request_timeout), 5))
        self.requests = 0

    def next_timeout(self):
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.max_requests or remaining <= 0.02:
            raise ReadBudgetExhausted('读取达到请求次数或时间上限，请稍后刷新')
        self.requests += 1
        # Requests applies a scalar timeout to both connection and reading.
        # Reserve time for both phases within the remaining scan budget.
        return min(self.request_timeout, remaining / 2)

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise ReadBudgetExhausted('读取达到时间上限，请稍后刷新')

    def run(self, operation):
        """Bound the caller's wall clock, beyond requests' idle-read timeout.

        Use only for HTTP reads without database work or external writes. Python
        cannot safely kill a thread; global slots keep late reads bounded.
        """
        self.check_deadline()
        if not _read_slots.acquire(blocking=False):
            raise ReadBudgetExhausted('GitLab 读取繁忙，请稍后刷新')
        done = threading.Event()
        outcome = []

        def read():
            try:
                outcome.append((True, operation()))
            except BaseException as exc:
                outcome.append((False, exc))
            finally:
                _read_slots.release()
                done.set()

        worker = threading.Thread(target=read, daemon=True, name='gitlab-budget-read')
        try:
            worker.start()
        except BaseException:
            _read_slots.release()
            raise
        wait_for = min(self.request_timeout, max(0, self.deadline - time.monotonic()))
        if not done.wait(wait_for):
            raise ReadBudgetExhausted('GitLab 读取超过时间上限，请稍后刷新')
        self.check_deadline()
        ok, result = outcome[0]
        if not ok:
            raise result
        return result
