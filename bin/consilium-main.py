#!/usr/bin/env python3
"""
Consilium Agent - Main Entry Point

Follows the bin/lib structure and can be invoked with an optional log level.
Example:
    ./consilium-main.py TRACE
    ./consilium-main.py --log-level debug

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path
from typing import Iterable

VALID_LEVELS = {
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
}

ALIASES = {
    "WARN": "WARNING",
}


def _normalize_level(value: str) -> str:
    """Normalize arbitrary user input into a supported logging level."""
    normalized = value.strip().replace("-", "").replace("_", "")
    if not normalized:
        raise ValueError("Empty log level")
    upper = normalized.upper()
    upper = ALIASES.get(upper, upper)
    if upper not in VALID_LEVELS:
        raise ValueError(f"Unsupported log level '{value}'")
    return upper


def _parse_args(argv: Iterable[str]) -> tuple[str, bool, bool]:
    parser = argparse.ArgumentParser(
        description="Launch Consilium Agent TUI (log level support).",
    )
    parser.add_argument(
        "log_level",
        nargs="?",
        help="TRACE | DEBUG | INFO | WARNING | ERROR (case insensitive)",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level_kw",
        help="TRACE | DEBUG | INFO | WARNING | ERROR (case insensitive)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install default roles to ~/.consilium/roles/ and exit",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )
    args = parser.parse_args(list(argv))

    chosen = args.log_level_kw or args.log_level
    if chosen is None:
        level = os.environ.get("LOGLEVEL", "INFO").upper()
    else:
        level = _normalize_level(chosen)

    return level, args.install, args.version


def _print_banner(level: str, logger: logging.Logger) -> None:
    """Show startup banner only when TTY is available; otherwise log."""
    lines = [
        "========================================",
        "  Consilium Agent TUI",
        "========================================",
        f"Log level: {level}",
        "",
        "TRACE   - Full dialog protocol (prompts, events)",
        "DEBUG   - Program state, tool calls",
        "INFO    - Main events (start, connections)",
        "WARNING - Warnings",
        "ERROR   - Errors only",
        "",
        "Logs saved in ~/.consilium/workspaces/<workspace_hash>/logs/",
        "========================================",
        "",
    ]

    if sys.stdout.isatty():
        for line in lines:
            print(line)
    else:
        banner = "\n".join(line for line in lines if line)
        logger.info("Startup banner suppressed (no TTY).\n%s", banner)


def main(argv: Iterable[str] | None = None) -> None:
    """Main entry point."""
    level, install_mode, version_mode = _parse_args(argv or sys.argv[1:])

    # Handle --version mode (before any imports or logging setup)
    if version_mode:
        # Quick path resolution to get version without full setup
        script_path = Path(__file__).resolve()
        bin_dir = script_path.parent
        lib_dir = bin_dir.parent / "lib"
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        try:
            from consilium import __version__  # type: ignore  # noqa: E402
            print(f"Consilium Agent v{__version__}")
        except ImportError:
            print("Consilium Agent (version unknown)")
        return

    os.environ["LOGLEVEL"] = level

    # Import utilities after LOGLEVEL is set so logging follows CLI choice
    launcher_logger = logging.getLogger("ConsiliumLauncher")

    def _ensure_consilium_on_path() -> None:
        """Ensure the local consilium package is importable even from scripts outside repo."""
        candidates: list[Path] = []

        env_hint = os.environ.get("CONSILIUM_APP_PATH")
        if env_hint:
            candidates.append(Path(env_hint))

        script_path = Path(__file__).resolve()
        bin_dir = script_path.parent
        candidates.extend(
            [
                bin_dir.parent,  # repo root when running from ./bin
                bin_dir.parent / "lib",
                Path.cwd(),
                Path.cwd() / "lib",
            ]
        )

        for parent in list(Path.cwd().parents)[:6]:
            candidates.append(parent)
            candidates.append(parent / "lib")

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate:
                continue
            try:
                candidate = candidate.resolve()
            except OSError:
                continue

            options = [candidate]
            if candidate.name != "lib":
                options.append(candidate / "lib")

            for option in options:
                if not option.exists():
                    continue
                marker = option / "consilium" / "__init__.py"
                if marker.exists():
                    path_str = str(option)
                    if path_str not in sys.path and path_str not in seen:
                        sys.path.insert(0, path_str)
                        seen.add(path_str)
                        launcher_logger.debug("Consilium library path added: %s", path_str)
                    return

    _ensure_consilium_on_path()

    from consilium.utils import log_file_path  # type: ignore  # noqa: E402

    # Handle --install mode
    if install_mode:
        from consilium.roles import RoleManager  # type: ignore  # noqa: E402
        launcher_logger.info("Running in --install mode: bootstrapping default roles")
        print("🚀 Installing default roles to ~/.consilium/roles/")
        role_manager = RoleManager()
        role_manager.bootstrap_defaults()
        role_manager.reload()
        installed_roles = role_manager.list_roles()
        print(f"✅ Installed {len(installed_roles)} default role(s):")
        for role in installed_roles:
            print(f"   - {role.name} ({role.role_id[:8]}...)")
        print("")
        launcher_logger.info("Installation complete, exiting")
        return

    _print_banner(level, launcher_logger)

    def _reattach_tty() -> None:
        if os.name != "posix":
            launcher_logger.debug("Skipping TTY reattach on non-posix platform")
            return
        try:
            fd_in = os.open("/dev/tty", os.O_RDONLY | os.O_CLOEXEC)
            fd_out = os.open("/dev/tty", os.O_WRONLY | os.O_CLOEXEC)
            fd_err = os.open("/dev/tty", os.O_WRONLY | os.O_CLOEXEC)
        except OSError as exc:
            launcher_logger.debug("TTY reattach unavailable: %s", exc)
            return

        try:
            os.dup2(fd_in, 0)
            os.dup2(fd_out, 1)
            os.dup2(fd_err, 2)
        except OSError as exc:
            launcher_logger.warning("Failed to dup TTY descriptors: %s", exc, exc_info=True)
            return
        finally:
            for fd in (fd_in, fd_out, fd_err):
                try:
                    os.close(fd)
                except OSError:
                    pass

        try:
            stdin_buffer = os.fdopen(0, "rb", closefd=False)
            stdout_buffer = os.fdopen(1, "wb", closefd=False)
            stderr_buffer = os.fdopen(2, "wb", closefd=False)
        except OSError as exc:
            launcher_logger.warning("Failed to open TTY file descriptors: %s", exc, exc_info=True)
            return

        sys.stdin = sys.__stdin__ = io.TextIOWrapper(stdin_buffer, encoding="utf-8", line_buffering=True)
        sys.stdout = sys.__stdout__ = io.TextIOWrapper(stdout_buffer, encoding="utf-8", line_buffering=True)
        sys.stderr = sys.__stderr__ = io.TextIOWrapper(stderr_buffer, encoding="utf-8", line_buffering=True)

        launcher_logger.debug(
            "TTY reattached: stdin=%s stdout=%s stderr=%s",
            sys.stdin.isatty(),
            sys.stdout.isatty(),
            sys.stderr.isatty(),
        )

    launcher_logger.debug(
        "TTY before reattach: stdin=%s stdout=%s stderr=%s",
        sys.stdin.isatty(),
        sys.stdout.isatty(),
        sys.stderr.isatty(),
    )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _reattach_tty()
    launcher_logger.debug(
        "TTY after reattach (if any): stdin=%s stdout=%s stderr=%s",
        sys.stdin.isatty(),
        sys.stdout.isatty(),
        sys.stderr.isatty(),
    )

    # Lazy imports so LOGLEVEL is visible during module init
    BIN_DIR = Path(__file__).parent.absolute()
    LIB_DIR = BIN_DIR.parent / "lib"
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))

    from consilium.app import ConsiliumAgentTUI  # type: ignore  # noqa: E402

    app = ConsiliumAgentTUI()
    try:
        app.run()
    finally:
        message = f"Log saved: {log_file_path}"
        if sys.stdout.isatty():
            print(f"\n{message}")
        launcher_logger.info(message)


if __name__ == "__main__":
    main()
