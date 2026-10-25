"""Load the documented .env format without evaluating shell code.

Supports KEY=value, optional quotes, comments, and an optional `export` prefix.
Values are literal: no variable interpolation or command substitution. Existing
environment variables take precedence. Error messages never include values.
"""

import os
import re
import shlex
from pathlib import Path


def load_env(path, environ=None):
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.exists():
        return
    parsed = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env assignment on line {number}; use KEY=value")
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid quoting in .env on line {number}") from None
        parsed[key] = " ".join(tokens)
        if "\x00" in parsed[key]:
            raise ValueError(f"Invalid null character in .env on line {number}")
    # Parse the entire file before changing anything, so a malformed file cannot
    # silently leave a half-loaded configuration.
    for key, value in parsed.items():
        environ.setdefault(key, value)
