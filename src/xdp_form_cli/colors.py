from __future__ import annotations

import os
import sys


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _paint(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{Ansi.RESET}"


def info(message: str) -> None:
    print(_paint(f"[INFO] {message}", Ansi.CYAN))


def step(message: str) -> None:
    print(_paint(f"[STEP] {message}", Ansi.BLUE))


def success(message: str) -> None:
    print(_paint(f"[OK] {message}", Ansi.GREEN))


def warn(message: str) -> None:
    print(_paint(f"[WARN] {message}", Ansi.YELLOW))


def error(message: str) -> None:
    print(_paint(f"[ERROR] {message}", Ansi.RED), file=sys.stderr)
