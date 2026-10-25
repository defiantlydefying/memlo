"""Create a local virtual environment and configuration without exposing tokens."""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def bootstrap(root):
    if sys.version_info < (3, 11):
        raise RuntimeError("Memlo needs Python 3.11 or newer. Install it and run this script again.")
    root = Path(root).resolve()
    if not (root / ".env.example").is_file() or not (root / "requirements.txt").is_file():
        raise RuntimeError("Missing setup files. Run this from a complete Memlo checkout.")
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not environment.exists():
        print("Creating .venv…")
        venv.EnvBuilder(with_pip=True).create(environment)
    if not python.is_file():
        raise RuntimeError("The existing .venv is incomplete. Rename it, then run setup again.")
    print("Installing Memlo’s dependencies…")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
    config = root / ".env"
    try:
        # Exclusive creation: even concurrent setup runs must never overwrite keys.
        with config.open("x", encoding="utf-8") as destination:
            config.chmod(0o600)
            with (root / ".env.example").open(encoding="utf-8") as template:
                shutil.copyfileobj(template, destination)
        print("Created .env. Add your Discord bot token there; the AI key is optional.")
    except FileExistsError:
        print("Kept your existing .env unchanged.")
    print("Next: edit .env, then run:")
    print(f'  "{python}" bot.py --check')
    print(f'  "{python}" bot.py')
    return python


def main():
    try:
        bootstrap(Path(__file__).parent)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
