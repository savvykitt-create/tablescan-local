"""Conservative resource budgets. Unknown hardware always means serial/no cache.

Also imported by the standalone Slow worker, including older ML environments
without psutil. Memory figures are available memory, never just installed RAM.
"""
from dataclasses import dataclass, asdict
import os
import sys
import subprocess

GIB = 1024 ** 3


@dataclass(frozen=True)
class Resources:
    total: int = 0
    available: int = 0
    cores: int = 1

    @property
    def reserve(self):
        return max(3 * GIB, self.total // 5)

    def to_dict(self):
        return asdict(self)


def resources():
    try:
        import psutil
        memory = psutil.virtual_memory()
        cores = psutil.cpu_count(logical=False) or 1
        try:
            cores = min(cores, len(psutil.Process().cpu_affinity()))
        except (AttributeError, OSError):
            pass
        return Resources(memory.total, memory.available, cores)
    except (ImportError, OSError):
        pass
    # The optional Slow environment predates psutil; no reinstall is required.
    try:
        import ctypes
        if sys.platform == 'darwin':
            total = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'], timeout=2, stderr=subprocess.DEVNULL))
            text = subprocess.check_output(['/usr/bin/vm_stat'], text=True, timeout=2, stderr=subprocess.DEVNULL)
            import re
            size = int(re.search(r'page size of (\d+) bytes', text)[1])
            pages = {key.strip(): int(value.strip().rstrip('.')) for key, value in
                     (line.split(':', 1) for line in text.splitlines()[1:] if ':' in line)}
            available = sum(pages.get(key, 0) for key in ('Pages free', 'Pages inactive', 'Pages speculative')) * size
        elif sys.platform == 'win32':
            class Status(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
            state = Status(); state.length = ctypes.sizeof(state)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                return Resources()
            total, available = state.total, state.available
        else:
            from pathlib import Path
            values = {line.split(':')[0]: int(line.split()[1]) * 1024
                      for line in Path('/proc/meminfo').read_text().splitlines()}
            total, available = values['MemTotal'], values['MemAvailable']
        return Resources(total, available, max(1, (os.cpu_count() or 2) // 2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return Resources()


def cpu_workers(state, cells, *, accelerated=False):
    # Budget 768 MiB per CPU model copy (measured ~415 MiB on the shipped models),
    # in addition to the
    # main process and OS reserve. GPU sessions are never multiplied blindly.
    if accelerated or cells < 48 or not state.total:
        return 1
    memory_slots = max(0, (state.available - state.reserve) // (768 * 1024**2))
    return max(1, min(6, state.cores - 1, memory_slots, cells // 24))


def can_keep_model(state):
    return bool(state.total and state.available >= max(4 * GIB, state.reserve))


def can_add_model(state, weight_bytes, *, gpu_free=None, gpu_required=0):
    # Loading needs room for weights, conversion/intermediate buffers and image
    # context. A CUDA process must satisfy BOTH host and device budgets.
    needed = max(2 * GIB, int(weight_bytes * 1.5))
    return (can_keep_model(state) and state.available >= state.reserve + needed
            and (gpu_free is None or gpu_free >= gpu_required + 2 * GIB))
