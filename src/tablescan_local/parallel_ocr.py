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
        self.reductions = 0
        self.events = []

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
            if not process.is_alive():
                raise ChildProcessError('CPU worker stopped')
            if time.monotonic() > deadline:
                raise TimeoutError('CPU worker timed out')
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
                except InterruptedError:
                    raise
                except (OSError, RuntimeError, EOFError):
                    self._fallback('initialization_failed')
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
                    busy = {id(item[1]) for item in pending}
                    process, connection = next(worker for worker in self.workers if id(worker[0]) not in busy)
                    pending.append((identifier, process, connection, metadata, args))
                    try:
                        connection.send((identifier, args))
                    except (OSError, EOFError):
                        self._fallback('send_failed')
                        break
                    identifier += 1
                if self.disabled or not pending:
                    continue
                number, process, connection, metadata, args = pending[0]
                try:
                    tag, payload = self._receive(process, connection, heartbeat)
                    if (tag != 'result' or not isinstance(payload, (tuple, list))
                            or len(payload) != 2 or payload[0] != number):
                        raise RuntimeError('CPU worker returned an invalid cell identifier')
                except InterruptedError:
                    raise
                except (OSError, RuntimeError, EOFError) as exc:
                    reason = ('timeout' if isinstance(exc, TimeoutError) else
                              'worker_stopped' if isinstance(exc, (ChildProcessError, EOFError)) else
                              'invalid_result' if isinstance(exc, RuntimeError) else 'receive_failed')
                    self._fallback(reason)
                    continue
                pending.popleft()
                self.completed += 1
                yield metadata, payload[1]
                # Re-check pressure between cells, before admitting more work.
                from .resource_policy import resources
                state = resources()
                if state.total and state.available < state.reserve:
                    # Retire only the worker whose completed cell was delivered.
                    # Other pending cells keep their workers and input order.
                    if len(self.workers) > 1 and state.reserve - state.available < 768 * 1024**2:
                        self._event('reduce', 'memory_pressure', state)
                        self.workers.remove((process, connection))
                        self._close_workers([(process, connection)])
                        self.reductions += 1
                    else:
                        self._fallback('memory_pressure', state)
        except BaseException:
            self.close()
            raise

    def _event(self, action, reason, state=None):
        event = {'action': action, 'reason': reason, 'completed_cells': self.completed,
                 'workers_before': len(self.workers),
                 'workers_after': len(self.workers) - 1 if action == 'reduce' else 0}
        if state is not None:
            event.update(available_bytes=state.available, reserve_bytes=state.reserve)
        self.events.append(event)

    def _fallback(self, reason, state=None):
        self._event('sequential', reason, state)
        self.disabled = True
        self.fallbacks += 1
        log.warning('CPU parallel OCR: %s; using sequential OCR', reason)
        self.close()

    def close(self):
        self._close_workers(self.workers)
        self.workers.clear()

    @staticmethod
    def _close_workers(workers):
        for process, connection in workers:
            if process.is_alive():
                process.terminate()
            connection.close()
        for process, _ in workers:
            process.join(timeout=2)
            if process.is_alive():
                process.kill(); process.join(timeout=2)
            process.close()
