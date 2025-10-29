"""
Consilium Agent - Utilities

Logging setup, configuration loading, and helper functions.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import os
import sys
import logging
import hashlib
from pathlib import Path
from datetime import datetime

from rich.console import Console

from .constants import DEFAULT_INIT_PROMPT, DEFAULT_SYSTEM_PROMPT

# TOML library import (Python 3.11+ has tomllib built-in)
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore


# ============================================================================
# LOGGING SETUP
# ============================================================================

LOG_LEVELS = {
    'TRACE': 5,       # Full dialog protocol (prompts, responses, raw events)
    'DEBUG': 10,      # Program state, important moments
    'INFO': 20,       # Rare informational messages
    'WARNING': 30,    # Warnings
    'ERROR': 40,      # Errors
    'CRITICAL': 50,   # Critical failures only
}

_STDERR_TRACE_FLAG = os.environ.get("CONSILIUM_STDERR_TRACE", "").strip().lower()
STDERR_TRACE_ENABLED = _STDERR_TRACE_FLAG not in {"", "0", "false", "no", "off"}

# Add TRACE level to logging
logging.addLevelName(5, 'TRACE')


def setup_logging():
    """Setup logging based on LOGLEVEL environment variable"""
    log_level_name = os.environ.get('LOGLEVEL', 'INFO').upper()
    log_level = LOG_LEVELS.get(log_level_name, 20)

    # Compute workspace hash (same algorithm as SessionManager)
    workspace_path = Path.cwd().absolute()
    workspace_hash = hashlib.sha256(str(workspace_path).encode()).hexdigest()[:16]

    # Log directory in ~/.consilium/workspaces/<hash>/logs/
    log_dir = Path.home() / ".consilium" / "workspaces" / workspace_hash / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file with timestamp (one file per app run)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"chat_{timestamp}.log"

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    # Root logger
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Remove previously managed handlers to avoid duplicates
    for handler in list(logger.handlers):
        if getattr(handler, "_consilium_managed", False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                logger.debug("Failed to close previous log handler cleanly", exc_info=True)

    file_handler._consilium_managed = True
    logger.addHandler(file_handler)

    # Optional stream handler for stderr (surface critical issues to terminal)
    stderr_level_name = os.environ.get('CONSILIUM_STDERR_LEVEL', 'OFF').strip().upper()
    if stderr_level_name not in {'OFF', 'NONE', 'DISABLE'}:
        stderr_level = LOG_LEVELS.get(stderr_level_name)
        if stderr_level is None:
            stderr_level = getattr(logging, stderr_level_name, logging.CRITICAL)

        stream_handler = logging.StreamHandler(stream=sys.stderr)
        stream_handler.setLevel(stderr_level)
        stream_handler.setFormatter(formatter)
        stream_handler._consilium_managed = True
        logger.addHandler(stream_handler)

    # TRACE method
    def trace(self, message, *args, **kwargs):
        if self.isEnabledFor(5):
            self._log(5, message, args, **kwargs)

    if not hasattr(logging.Logger, "trace"):
        logging.Logger.trace = trace  # type: ignore[attr-defined]

    # Silence overly verbose third-party loggers
    quiet_loggers = [
        "markdown_it",
        "markdown_it.rules_block",
        "markdown_it.rules_inline",
    ]
    for quiet_logger in quiet_loggers:
        noisy = logging.getLogger(quiet_logger)
        noisy.setLevel(logging.WARNING)
        noisy.propagate = False

    def excepthook(exc_type, exc_value, exc_traceback):
        """Ensure uncaught exceptions always reach stderr."""
        if logger.isEnabledFor(logging.ERROR):
            logger.error(
                "Uncaught exception",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
        if STDERR_TRACE_ENABLED:
            console = Console(stderr=True)
            console.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = excepthook

    return logger, log_file


def load_prompts_from_config() -> tuple[str, str]:
    """Load prompts from ~/.consilium/prompts.toml with graceful fallbacks."""
    logger = logging.getLogger(__name__)

    defaults = {
        'init': DEFAULT_INIT_PROMPT,
        'system': DEFAULT_SYSTEM_PROMPT,
    }

    config_path = Path.home() / ".consilium" / "prompts.toml"

    if not config_path.exists():
        logger.info(f"Creating default prompts.toml at {config_path}")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            # Create default TOML file with current defaults
            toml_content = f"""# Consilium Agent Prompts Configuration
# Edit these prompts to customize agent behavior

[prompts]
# Initial prompt - shown only on first agent introduction
init = \"\"\"
{defaults['init']}
\"\"\"

# System prompt - used for all subsequent messages
system = \"\"\"
{defaults['system']}
\"\"\"
"""
            config_path.write_text(toml_content, encoding='utf-8')
            logger.info(f"Created default {config_path}")
        except Exception:
            logger.exception("Failed to create default prompts.toml")

        # Return defaults for this session
        return defaults['init'], defaults['system']

    if tomllib is None:
        logger.warning(
            f"{config_path} found but tomllib/tomli is unavailable; using default prompts"
        )
        return defaults['init'], defaults['system']

    try:
        with config_path.open('rb') as config_file:
            data = tomllib.load(config_file)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception(f"Failed to load prompts from {config_path}")
        return defaults['init'], defaults['system']

    prompts_section = data.get('prompts', {})
    init_prompt = prompts_section.get('init', data.get('init', defaults['init']))
    system_prompt = prompts_section.get('system', data.get('system', defaults['system']))

    if not isinstance(init_prompt, str):
        logger.warning("Invalid type for init prompt in prompts.toml; using default")
        init_prompt = defaults['init']

    if not isinstance(system_prompt, str):
        logger.warning("Invalid type for system prompt in prompts.toml; using default")
        system_prompt = defaults['system']

    logger.info(f"Loaded prompts from {config_path}")
    return init_prompt, system_prompt


# Initialize logging and load prompts at module level
logger, log_file_path = setup_logging()
logger.info("=" * 70)
logger.info("Consilium Agent started")
logger.info(f"Log level: {os.environ.get('LOGLEVEL', 'INFO').upper()}")
logger.info("=" * 70)

# Load prompts
INIT_PROMPT, SYSTEM_PROMPT = load_prompts_from_config()

__all__ = ['setup_logging', 'load_prompts_from_config', 'LOG_LEVELS', 'INIT_PROMPT', 'SYSTEM_PROMPT', 'logger', 'log_file_path']
