"""共享分析执行门禁：批次之间优先交给手动任务。"""
from contextlib import contextmanager
import threading


class ProcessingQueue:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = False
        self._manual_waiters = 0

    @contextmanager
    def claim(self, priority='manual', cancel=None):
        manual = priority == 'manual'
        acquired = False
        with self._condition:
            self._manual_waiters += int(manual)
            try:
                while self._active or (not manual and self._manual_waiters):
                    if cancel and cancel.is_set():
                        raise InterruptedError('任务已取消')
                    self._condition.wait(.2)
                if cancel and cancel.is_set():
                    raise InterruptedError('任务已取消')
                self._active = acquired = True
            finally:
                self._manual_waiters -= int(manual)
                self._condition.notify_all()
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()
