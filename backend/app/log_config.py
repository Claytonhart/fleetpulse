"""Basic structured logging setup.

Keeps logs greppable (timestamp · level · logger · message). Intentionally
dependency-free for v1; Step 13 can swap in JSON logging if desired.
"""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=_LOG_FORMAT, force=True)
