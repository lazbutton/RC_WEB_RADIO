"""
Beets 2.5 calls ioctl(TIOCGWINSZ) with a 4-byte buffer.
Python 3.14 rejects that (kernel writes 8 bytes) → SystemError at import of beets.ui.
"""

from __future__ import annotations


def patch_beets_term_ioctl() -> None:
    import fcntl

    if getattr(fcntl.ioctl, "_compat_padded", False):
        return

    real_ioctl = fcntl.ioctl

    def ioctl(fd, request, arg=0, *args, **kwargs):
        try:
            return real_ioctl(fd, request, arg, *args, **kwargs)
        except SystemError:
            if isinstance(arg, str) and len(arg) < 8:
                padded = arg + "\0" * (8 - len(arg))
                out = real_ioctl(fd, request, padded, *args, **kwargs)
                return out[: len(arg)]
            if isinstance(arg, (bytes, bytearray)) and len(arg) < 8:
                pad = bytes(arg) + b"\0" * (8 - len(arg))
                out = real_ioctl(fd, request, pad, *args, **kwargs)
                return out[: len(arg)]
            raise

    ioctl._compat_padded = True  # type: ignore[attr-defined]
    fcntl.ioctl = ioctl  # type: ignore[method-assign]
