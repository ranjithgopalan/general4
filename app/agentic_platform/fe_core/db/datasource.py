"""Declarative datasource registry with environment interpolation.

Secrets never appear in `datasources.yaml`; a datasource names the environment
variable holding its password via `password_env`. Non-secret fields support
`${VAR}` and `${VAR:default}`.

This exists because the alternative -- a single connection hardcoded in config --
rots. In the knowledge base, HOST, PORT, DB, USER, ENCRYPT and TRUST_CERT were all declared and
then never read; only the password was used, and the real connection details came
from a database row. Every field declared here is actually applied.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

SUPPORTED_DIALECTS = frozenset({"sqlserver", "sybase", "oracle", "postgresql"})


class DatasourceConfigError(ValueError):
    pass


def _interpolate(value: Any) -> Any:
    """Resolve ${VAR} / ${VAR:default} against the environment."""
    if not isinstance(value, str):
        return value

    def repl(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        resolved = os.getenv(name)
        if resolved is None or resolved == "":
            return default if default is not None else ""
        return resolved

    return _INTERP.sub(repl, value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Datasource:
    name: str
    dialect: str
    host: str | None = None
    port: int = 1433
    database: str | None = None
    username: str | None = None
    password_env: str | None = None
    db_schema: str = "dbo"
    encrypt: bool = True
    trust_server_certificate: bool = True
    readonly: bool = True
    login_timeout_seconds: int = 15
    description: str | None = None
    exclude_patterns: list[str] = field(default_factory=list)

    # -- state ------------------------------------------------------------
    def is_configured(self) -> bool:
        return bool(self.host and self.database)

    def password_available(self) -> bool:
        return bool(self.password_env and os.getenv(self.password_env))

    @property
    def password(self) -> str:
        """Read the secret at use time; never stored on the instance."""
        if not self.password_env:
            return ""
        return os.getenv(self.password_env, "")

    def excluded(self, object_name: str) -> bool:
        for pattern in self.exclude_patterns:
            try:
                if re.search(pattern, object_name, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning(
                    "Invalid exclude_pattern %r on datasource %s", pattern, self.name
                )
        return False

    # -- connection -------------------------------------------------------
    def odbc_connection_string(self, driver: str) -> str:
        """Build an ODBC string honouring every declared field.

        `ApplicationIntent=ReadOnly` is applied when `readonly` is set. That is a
        routing hint, not a guarantee -- the real enforcement is statement-level
        (see `fe_core.db.safety`) plus a least-privilege database login.
        """
        if not self.is_configured():
            raise DatasourceConfigError(
                f"datasource '{self.name}' is missing host and/or database"
            )
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={self.host},{self.port}",
            f"DATABASE={self.database}",
            f"UID={self.username or ''}",
            f"PWD={self.password}",
            f"Encrypt={'yes' if self.encrypt else 'no'}",
            f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'}",
            f"LoginTimeout={self.login_timeout_seconds}",
        ]
        if self.readonly:
            parts.append("ApplicationIntent=ReadOnly")
        return ";".join(parts)

    def redacted(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dialect": self.dialect,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "db_schema": self.db_schema,
            "encrypt": self.encrypt,
            "trust_server_certificate": self.trust_server_certificate,
            "readonly": self.readonly,
            "password_env": self.password_env,
            "password_set": self.password_available(),
        }


def parse_datasource(raw: dict[str, Any]) -> Datasource:
    resolved = {k: _interpolate(v) for k, v in raw.items()}

    name = (resolved.get("name") or "").strip()
    if not name:
        raise DatasourceConfigError("every datasource requires a 'name'")

    dialect = (resolved.get("dialect") or "").strip().lower()
    if dialect not in SUPPORTED_DIALECTS:
        raise DatasourceConfigError(
            f"datasource '{name}': unsupported dialect {dialect!r}; "
            f"expected one of {', '.join(sorted(SUPPORTED_DIALECTS))}"
        )

    readonly = _as_bool(resolved.get("readonly"), default=True)
    if not readonly:
        # Refuse to load a writable legacy datasource. Nothing in this service
        # should ever write to the legacy estate (PRD FR-033, NFR-1).
        raise DatasourceConfigError(
            f"datasource '{name}': readonly=false is not permitted. This service "
            "only ever reads catalogue metadata from legacy systems."
        )

    return Datasource(
        name=name,
        dialect=dialect,
        host=(resolved.get("host") or "").strip() or None,
        port=_as_int(resolved.get("port"), 1433),
        database=(resolved.get("database") or "").strip() or None,
        username=(resolved.get("username") or "").strip() or None,
        password_env=(resolved.get("password_env") or "").strip() or None,
        db_schema=(resolved.get("db_schema") or "dbo").strip() or "dbo",
        encrypt=_as_bool(resolved.get("encrypt"), default=True),
        trust_server_certificate=_as_bool(
            resolved.get("trust_server_certificate"), default=True),
        readonly=True,
        login_timeout_seconds=_as_int(resolved.get("login_timeout_seconds"), 15),
        description=resolved.get("description"),
        exclude_patterns=list(raw.get("exclude_patterns") or []),
    )


def load_datasources(path: Path) -> list[Datasource]:
    if not Path(path).is_file():
        logger.info("No datasources file at %s; none registered", path)
        return []
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise DatasourceConfigError(f"{path}: invalid YAML: {exc}") from exc

    entries = data.get("datasources") or []
    if not isinstance(entries, list):
        raise DatasourceConfigError(f"{path}: 'datasources' must be a list")

    sources = [parse_datasource(e) for e in entries if isinstance(e, dict)]
    names = [s.name for s in sources]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise DatasourceConfigError(f"{path}: duplicate datasource name(s): {dupes}")
    return sources


def get_datasource(name: str, path: Path | None = None) -> Datasource:
    from app.agentic_platform.fe_core.config import get_settings

    path = path or get_settings().fe_datasources_file
    for source in load_datasources(path):
        if source.name == name:
            return source
    raise DatasourceConfigError(f"no datasource named '{name}' in {path}")
