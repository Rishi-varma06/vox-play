"""
Command-line entry points.

The import of ``.main`` is deferred. Eagerly importing it here means that
``python -m voxplay.cli.main`` loads the module once as a side effect of
importing the package, then again as ``__main__``, and runpy prints a
RuntimeWarning about it before any of our own output appears.
"""

from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from .main import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
