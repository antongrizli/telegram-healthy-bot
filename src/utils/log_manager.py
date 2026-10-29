import os
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from typing import Optional

# Timestamp pattern matching standard Python asctime format: '2026-10-29 09:19:15,808'
LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?")

DEFAULT_LOG_PATH = os.environ.get("LOG_FILE_PATH", "logs/healthy_bot.log")


class HealthCheckAccessFilter(logging.Filter):
    """
    Suppresses noisy 200 OK access logs for the internal /health endpoint
    while preserving warnings, errors, or non-200 responses.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "GET /health HTTP" in msg and " 200 " in msg:
            return False
        return True


def setup_logging(log_file_path: Optional[str] = None) -> Optional[RotatingFileHandler]:
    """
    Configures root logging with a console handler and a rotating file handler.
    Installs HealthCheckAccessFilter to suppress healthcheck access spam.
    """
    path = log_file_path or DEFAULT_LOG_PATH

    # 1. Silence noisy healthcheck logs in aiohttp.access
    health_filter = HealthCheckAccessFilter()
    logging.getLogger("aiohttp.access").addFilter(health_filter)

    # 2. Setup RotatingFileHandler
    handler = None
    try:
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        handler = RotatingFileHandler(
            filename=path,
            maxBytes=15 * 1024 * 1024,  # 15 MB per file
            backupCount=5,
            encoding="utf-8"
        )
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(formatter)
        handler.addFilter(health_filter)

        root_logger = logging.getLogger()
        # Avoid adding multiple duplicate file handlers
        existing = any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == handler.baseFilename for h in root_logger.handlers)
        if not existing:
            root_logger.addHandler(handler)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not initialize file logging at {path}: {e}")
        # Try fallback to /tmp if primary path failed
        if not path.startswith("/tmp/"):
            try:
                fallback_path = "/tmp/healthy_bot.log"
                handler = RotatingFileHandler(fallback_path, maxBytes=15 * 1024 * 1024, backupCount=3, encoding="utf-8")
                formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
                handler.setFormatter(formatter)
                handler.addFilter(health_filter)
                logging.getLogger().addHandler(handler)
            except Exception as fb_err:
                logging.getLogger(__name__).warning(f"Fallback logging to /tmp also failed: {fb_err}")

    return handler


def get_logs_for_last_24h(log_file_path: Optional[str] = None, hours: int = 24, now: Optional[datetime] = None) -> bytes:
    """
    Reads log file and rotated backups, filters lines from the last N hours,
    and returns UTF-8 encoded bytes.
    """
    path = log_file_path or DEFAULT_LOG_PATH
    if not os.path.exists(path) and os.path.exists("/tmp/healthy_bot.log"):
        path = "/tmp/healthy_bot.log"

    if not os.path.exists(path):
        return f"# No log file found at {path}\n".encode("utf-8")

    current_time = now or datetime.now()
    cutoff = current_time - timedelta(hours=hours)

    # Collect files in chronological order (oldest rotated backup first, active last)
    log_files = []
    base_dir = os.path.dirname(path) or "."
    base_name = os.path.basename(path)

    # Check for rotated files: e.g. healthy_bot.log.5, .4, .3, .2, .1
    for i in range(10, 0, -1):
        rotated = f"{path}.{i}"
        if os.path.exists(rotated):
            log_files.append(rotated)
    log_files.append(path)

    matching_lines = []
    include_current_block = False

    for file_path in log_files:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = LOG_TIMESTAMP_RE.match(line)
                    if match:
                        ts_str = match.group(1)
                        try:
                            line_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                            if line_time >= cutoff:
                                include_current_block = True
                                matching_lines.append(line)
                            else:
                                include_current_block = False
                        except ValueError:
                            # Malformed timestamp, preserve if in current block
                            if include_current_block:
                                matching_lines.append(line)
                    else:
                        # Continuation line (tracebacks, multi-line logs)
                        if include_current_block:
                            matching_lines.append(line)
        except Exception as e:
            matching_lines.append(f"# Error reading {file_path}: {e}\n")

    if not matching_lines:
        return f"# No log records found in the last {hours} hours (since {cutoff.strftime('%Y-%m-%d %H:%M:%S')}).\n".encode("utf-8")

    header = f"# Logs for the last {hours} hours (since {cutoff.strftime('%Y-%m-%d %H:%M:%S')})\n"
    header += f"# Total lines: {len(matching_lines)}\n"
    header += "# =====================================================================\n\n"

    result = header + "".join(matching_lines)
    return result.encode("utf-8")
