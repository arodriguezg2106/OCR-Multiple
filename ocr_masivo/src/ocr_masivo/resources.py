"""Conservative concurrency selection; never reduce OCR resolution or skip pages."""

import ctypes
import os


def available_memory():
    try:
        if os.name == "nt":

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "total",
                        "available",
                        "total_page",
                        "available_page",
                        "total_virtual",
                        "available_virtual",
                        "extended",
                    )
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.available
        else:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        pass
    return None


def page_jobs(requested, workers, free_bytes, cpus):
    # Approximate headroom, not a hard memory limit: large scans can need more.
    if free_bytes is None:
        return 1
    slots = max(1, int((free_bytes / 2**30 - 1) / 0.65))
    return max(1, min(requested, slots // workers, max(1, cpus // workers)))
