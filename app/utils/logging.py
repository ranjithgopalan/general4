"""
Centralized logging for the AIDLC Platform Agents.

Console-first (stdout — CloudWatch/containers capture it); optional rotating file.
A PII-masking log filter is attached when the PII service exists (later milestone);
it fails open (never blocks logging), so this module is import-safe before then.

    from app.utils.logging import log
    log.info("...")

Env-gate: the rich dictConfig (DEBUG level, formatted console, optional rotating file)
is applied only for dev/local/test or LOCAL_RUN. Deployed envs (uat/prod) get a minimal
INFO console config so the platform/container logging agent owns formatting.
"""

import logging
import logging.config
import sys
from pathlib import Path
from typing import Any

# The Windows console defaults to cp1252, which raises UnicodeEncodeError when a log record
# contains Japanese (the KB is bilingual — e.g. 基本情報). Force UTF-8 on the console streams
# BEFORE any StreamHandler binds to them, so console logging never crashes on non-Latin text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - non-reconfigurable stream (older/redirected)
        pass


class ContextVarFilter(logging.Filter):
    """Inject active request context (correlation_id, userid, persona) into every log record.

    These fields are populated from ContextVars so every log line emitted during a request
    automatically carries the trace IDs without explicit f-string threading.  Fails open —
    a missing or unavailable ContextVar never blocks logging.  The attributes are available
    on the LogRecord for custom handlers and future format-string changes.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from app.utils.request_context import (
                get_agent_run_id,
                get_correlation_id,
                get_persona,
                get_userid,
                get_workflow_run_id,
            )
            record.correlation_id = get_correlation_id() or "-"
            record.userid = get_userid() or "-"
            record.persona = get_persona() or "-"
            record.workflow_run_id = get_workflow_run_id() or "-"
            record.agent_run_id = get_agent_run_id() or "-"
        except Exception:
            record.correlation_id = "-"
            record.userid = "-"
            record.persona = "-"
            record.workflow_run_id = "-"
            record.agent_run_id = "-"
        return True


class PIIMaskingFilter(logging.Filter):
    """Mask PII in log messages before they are emitted. Fails open (seam for later)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from app.config import get_settings
            from app.utils.pii_masking import get_pii_masking_service  # type: ignore

            if not get_settings().TELEMETRY_ENABLED:
                return True
            pii = get_pii_masking_service()
            if not pii.is_enabled():
                return True
            if record.msg:
                record.msg = pii.mask_text(str(record.msg))
        except Exception:
            # PII service not available yet or failed — never block logging.
            pass
        return True


def _is_local_env() -> bool:
    """Return True when running locally (dev/local/test) or LOCAL_RUN is set."""
    try:
        s = _settings()
        return s.ENV in ("dev", "local", "test") or s.LOCAL_RUN
    except Exception:
        return True


def _settings():
    from app.config import get_settings

    return get_settings()


class LogConfig:
    """Builds and installs the logging configuration."""

    @staticmethod
    def _minimal_config() -> dict[str, Any]:
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "format": (
                        "%(asctime)s %(levelname)-8s"
                        " [cid=%(correlation_id)s uid=%(userid)s"
                        " wrid=%(workflow_run_id)s arid=%(agent_run_id)s]"
                        " %(name)s: %(message)s"
                    ),
                }
            },
            "handlers": {
                "console": {
                    "level": "INFO",
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "plain",
                }
            },
            "root": {"handlers": ["console"], "level": "INFO"},
        }

    @staticmethod
    def _rich_config() -> dict[str, Any]:
        s = _settings()
        level = logging.DEBUG if s.DEBUG else logging.INFO
        config: dict[str, Any] = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d"
                        " [cid=%(correlation_id)s uid=%(userid)s"
                        " wrid=%(workflow_run_id)s arid=%(agent_run_id)s] - %(message)s"
                    ),
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "level": level,
                    "formatter": "standard",
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                },
                "file": {
                    "level": level,
                    "formatter": "standard",
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": s.LOG_FILE_PATH,
                    "maxBytes": s.LOG_FILE_MAX_BYTES,
                    "backupCount": s.LOG_FILE_BACKUP_COUNT,
                    "encoding": "utf-8",
                },
            },
            "loggers": {"": {"handlers": ["console", "file"], "level": level, "propagate": True}},
        }
        if not s.ENABLE_FILE_LOGGING:
            del config["handlers"]["file"]
            config["loggers"][""]["handlers"] = ["console"]
        return config

    @staticmethod
    def setup_logging() -> logging.Logger:
        s = _settings()
        if _is_local_env():
            if s.ENABLE_FILE_LOGGING:
                Path(s.LOG_FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
            logging.config.dictConfig(LogConfig._rich_config())
        else:
            logging.config.dictConfig(LogConfig._minimal_config())

        logger = logging.getLogger("app")
        # Attach ContextVarFilter to all handlers AFTER dictConfig so it does not reference
        # this module's class path during formatter resolution (which would cause a circular import).
        try:
            ctx_filter = ContextVarFilter()
            for handler in logging.getLogger().handlers:
                handler.addFilter(ctx_filter)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Failed to attach ContextVarFilter: {exc}")
        if _is_local_env():
            try:
                pii_filter = PIIMaskingFilter()
                for handler in logging.getLogger().handlers:
                    handler.addFilter(pii_filter)
            except Exception as exc:  # pragma: no cover
                logger.warning(f"Failed to attach PII masking filter: {exc}")

        for noisy in ("boto3", "botocore", "urllib3", "httpx", "httpcore", "watchfiles", "watchfiles.main"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        logger.info(f"Starting {s.APPLICATION_NAME} v{s.APP_VERSION} (env={s.ENV})")
        return logger


# Ready-to-import application logger.
log = LogConfig.setup_logging()
