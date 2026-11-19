"""The User-Agent header the client facade sends on every request."""

from __future__ import annotations

import platform
from importlib import metadata


def _sdk_version() -> str:
    try:
        return metadata.version("vertesia-client")
    except metadata.PackageNotFoundError:
        return "unknown"


USER_AGENT = f"vertesia-client-python/{_sdk_version()} python/{platform.python_version()}"
"""Identifies this SDK, its version and the Python runtime, e.g. ``vertesia-client-python/1.7.0 python/3.12.4``."""
