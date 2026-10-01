"""Bounded, ordered CPU cell workers with explicit ownership and cancellation."""
import logging
import multiprocessing
import os
import time
from collections import deque

log = logging.getLogger(__name__)


def _worker(connection, threads):
    # Spawn, never fork a live Qt/ONNX process. Imports occur after thread limits.
    os.environ['TABLESCAN_OCR_DEVICE'] = 'cpu'
    # Preserve the original ONNX reduction/thread configuration: forcing one
    # thread changes floating-point scores on some models. Disable busy-waiting
    # instead, so idle model thread pools do not compete with other workers.
    os.environ['TABLESCAN_ORT_NO_SPIN'] = '1'
    if threads:
        os.environ['TABLESCAN_ORT_THREADS'] = str(threads)
    try:
        import cv2
        cv2.setNumThreads(1)
        from .ocr import LocalOcrEngine
        engine = LocalOcrEngine(high_accuracy=True)
        connection.send(('ready', None))
        while True:
            payload = connection.recv()
            if payload is None:
                return
            identifier, args = payload
            try:
                result = engine.recognize_cell(**args)
                connection.send(('result', (identifier, result)))
            except Exception as exc:
                connection.send(('error', str(exc)))
                return
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            connection.send(('error', str(exc)))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class CellWorkers:
    def __init__(self, count, *, threads=0, target=_worker):
        self.count = count
        self.threads = threads
        self.target = target
        self.workers = []
        self.disabled = False
        self.fallbacks = 0
        self.completed = 0

    def _start(self, heartbeat):
        if self.workers:
            return
        context = multiprocessing.get_context('spawn')
        for _ in range(self.count):
            parent, child = context.Pipe()
            process = context.Process(target=self.target, args=(child, self.threads), daemon=True)
            try:
                process.start()
            except BaseException:
                parent.close(); child.close()
                raise
            child.close()
            self.workers.append((process, parent))
        for process, connection in self.workers:
            tag, _ = self._receive(process, connection, heartbeat, 60)
            if tag != 'ready':
                raise RuntimeError('CPU worker could not initialize')

    @staticmethod
    def _receive(process, connection, heartbeat, timeout=120):
        deadline = time.monotonic() + timeout
        while not connection.poll(.1):
            heartbeat()
            if not process.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('CPU worker stopped or timed out')
        return connection.recv()

    def map(self, tasks, engine, heartbeat=lambda: None):
        """Yield (metadata, recognition) in input order; keep at most N crops live.

        On a worker failure replay only unfinished tasks through the original
        engine. Cancellation propagates and all child processes are reaped.
        """
        tasks = iter(tasks)
        pending = deque()
        try:
            if not self.disabled:
                try:
                    self._start(heartbeat)
                except (OSError, RuntimeError, EOFError):
                    self._fallback()
            identifier = 0
            exhausted = False
            while pending or not exhausted:
                if self.disabled:
                    while pending:
                        _, _, _, metadata, args = pending.popleft()
                        heartbeat()
                        yield metadata, engine.recognize_cell(**args)
                    for metadata, args in tasks:
                        heartbeat()
                        yield metadata, engine.recognize_cell(**args)
                    return
                while len(pending) < len(self.workers) and not exhausted:
                    try:
                        metadata, args = next(tasks)
                    except StopIteration:
                        exhausted = True
                        break
                    slot = identifier % len(self.workers)
                    process, connection = self.workers[slot]
                    pending.append((identifier, process, connection, metadata, args))
                    try:
                        connection.send((identifier, args))
                    except (OSError, EOFError):
                        self._fallback()
                        break
                    identifier += 1
                if self.disabled or not pending:
                    continue
                number, process, connection, metadata, args = pending[0]
                try:
                    tag, payload = self._receive(process, connection, heartbeat)
                    if tag != 'result' or payload[0] != number:
                        raise RuntimeError('CPU worker returned an invalid cell identifier')
                except (OSError, RuntimeError, EOFError):
                    self._fallback()
                    continue
                pending.popleft()
                self.completed += 1
                yield metadata, payload[1]
                # Re-check pressure between cells, before admitting more work.
                from .resource_policy import resources
                state = resources()
                if state.total and state.available < state.reserve:
                    self._fallback()
        except BaseException:
            self.close()
            raise

    def _fallback(self):
        self.disabled = True
        self.fallbacks += 1
        log.warning('CPU parallel OCR unavailable or memory pressure; using sequential OCR')
        self.close()

    def close(self):
        for process, connection in self.workers:
            if process.is_alive():
                process.terminate()
            connection.close()
        for process, _ in self.workers:
            process.join(timeout=2)
            if process.is_alive():
                process.kill(); process.join(timeout=2)
            process.close()
        self.workers.clear()
