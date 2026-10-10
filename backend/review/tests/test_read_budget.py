import threading
import time

from django.test import SimpleTestCase

from review.services.read_budget import ReadBudget, ReadBudgetExhausted


class ReadBudgetTests(SimpleTestCase):
    def test_wall_clock_limits_a_read_that_keeps_progressing(self):
        release = threading.Event()
        finished = threading.Event()

        def slow_read():
            try:
                release.wait(1)
                return 'late complete page'
            finally:
                finished.set()

        budget = ReadBudget(1, 0.15, 0.1)
        budget.next_timeout()
        started = time.monotonic()
        try:
            with self.assertRaises(ReadBudgetExhausted):
                budget.run(slow_read)
            self.assertLess(time.monotonic() - started, 0.35)
        finally:
            release.set()
            finished.wait(1)

    def test_completed_read_does_not_succeed_after_total_deadline(self):
        budget = ReadBudget(1, 0.03, 1)
        time.sleep(0.04)
        with self.assertRaises(ReadBudgetExhausted):
            budget.check_deadline()
