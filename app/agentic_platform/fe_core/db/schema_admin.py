"""Apply and verify the control-plane schema.

The schema goes into the **`fe` schema of the knowledge base's existing Postgres**, not a second
container. One instance, two schemas: `kb` for the knowledge base, `fe` for
pipeline state. That keeps a single thing to start in development while leaving
the two independently restorable -- the knowledge base's 500-statement raw-DDL migration list
and this schema have nothing to do with each other, and a `DROP SCHEMA fe` must
never be able to take the KB with it.

This module only creates and inspects. It is deliberately separate from
`db/introspector.py`, which reads the *legacy* UW CR database read-only and must
never issue DDL (NFR-1).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_FILE = (Path(__file__).resolve().parents[4]
               / "migrations" / "schema.sql")

#: What a correctly applied schema contains. Checked by name so a partial apply --
#: the file half-executed after a connection drop -- is reported rather than
#: assumed complete.
EXPECTED_TABLES = (
    "fe_project", "fe_workspace", "fe_stage_run", "fe_sdlc_artifact",
    "fe_approval", "fe_outbox_event",
)
EXPECTED_VIEWS = ("project_overview", "artifact_head", "artifact_approved")
EXPECTED_TRIGGERS = ("sdlc_artifact_immutable", "approval_no_update")
#: The indexes that enforce invariants rather than merely speeding queries up.
EXPECTED_UNIQUE_INDEXES = (
    "workspace_one_global_per_project",
    "workspace_one_per_project_epic",
    "sdlc_artifact_single_head",
)


class DatabaseUnavailableError(RuntimeError):
    """Could not connect. Carries the operator action, not just the driver error."""


@dataclass
class SchemaReport:
    schema: str
    tables: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    unique_indexes: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def missing_tables(self) -> list[str]:
        return [t for t in EXPECTED_TABLES if t not in self.tables]

    @property
    def missing_views(self) -> list[str]:
        return [v for v in EXPECTED_VIEWS if v not in self.views]

    @property
    def missing_triggers(self) -> list[str]:
        return [t for t in EXPECTED_TRIGGERS if t not in self.triggers]

    @property
    def missing_guards(self) -> list[str]:
        return [i for i in EXPECTED_UNIQUE_INDEXES if i not in self.unique_indexes]

    @property
    def ok(self) -> bool:
        return not (self.missing_tables or self.missing_views
                    or self.missing_triggers or self.missing_guards)


def _connect(url: str):
    """Open a connection, translating driver errors into an operator action."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise DatabaseUnavailableError(
            "psycopg is not installed. Run: pip install 'psycopg[binary]'"
        ) from exc

    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=8)
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(
            f"{type(exc).__name__}: {exc}\n"
            "        The lmod-postgres container is probably stopped. Start it:\n"
            "          docker start lmod-postgres\n"
            "        and check FE_DB_URL points at it (port 5435 by default)."
        ) from exc


def redacted_url(url: str) -> str:
    """Safe to log: strips the password only, keeping host, port and database."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url or "")


def _iter_statements(sql: str):
    """Yield individual SQL statements from a multi-statement string.

    psycopg3 cursor.execute() accepts exactly ONE statement at a time.
    This generator splits on semicolons while correctly skipping over:
      - single-quoted strings  ('it''s fine')
      - dollar-quoted blocks   ($$ body $$ and $tag$ body $tag$)
      - single-line comments   (-- ...)
      - block comments         (/* ... */)
    """
    buf: list[str] = []
    i = 0
    n = len(sql)

    while i < n:
        # -- single-line comment: consume to end of line
        if sql[i:i+2] == "--":
            end = sql.find("\n", i)
            chunk = sql[i:end + 1] if end != -1 else sql[i:]
            buf.append(chunk)
            i = end + 1 if end != -1 else n
            continue

        # /* block comment */
        if sql[i:i+2] == "/*":
            end = sql.find("*/", i + 2)
            chunk = sql[i:end + 2] if end != -1 else sql[i:]
            buf.append(chunk)
            i = end + 2 if end != -1 else n
            continue

        # 'single-quoted string' with '' escaping
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2          # escaped quote inside string
                elif sql[j] == "'":
                    j += 1
                    break
                else:
                    j += 1
            buf.append(sql[i:j])
            i = j
            continue

        # $tag$dollar-quoted block$tag$
        if sql[i] == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                tag = sql[i:j + 1]          # e.g. '$$' or '$rag$'
                end = sql.find(tag, j + 1)
                chunk = sql[i:end + len(tag)] if end != -1 else sql[i:]
                buf.append(chunk)
                i = end + len(tag) if end != -1 else n
                continue

        # semicolon = end of statement
        if sql[i] == ";":
            buf.append(";")
            stmt = "".join(buf).strip()
            if stmt and stmt != ";":
                yield stmt
            buf = []
            i += 1
            continue

        buf.append(sql[i])
        i += 1

    # any trailing content without a final semicolon
    remaining = "".join(buf).strip()
    if remaining:
        yield remaining


def apply_schema(url: str, schema_file: Path | None = None,
                 schema: str = "fe") -> SchemaReport:
    """Execute schema.sql statement-by-statement, substituting the target schema name.

    psycopg3 cursor.execute() accepts only one statement at a time, so we
    split the file with _iter_statements() which handles dollar-quoting,
    single-quoted strings and comments.

    Every DDL is IF NOT EXISTS / CREATE OR REPLACE, so re-applying an
    up-to-date database is a no-op.

    When schema != 'fe', two substitutions are made before execution:
      - ``SET search_path TO fe, public``   → ``SET search_path TO <schema>, public``
      - ``SET search_path = fe, pg_temp``   → ``SET search_path = <schema>, pg_temp``
    Table names (fe_project, fe_stage_run …) are unchanged — the fe_ prefix is a
    naming convention, not tied to the schema container.
    """
    path = schema_file or SCHEMA_FILE
    if not path.is_file():
        raise DatabaseUnavailableError(f"schema file not found: {path}")

    sql = path.read_text(encoding="utf-8")

    # Substitute schema name when it differs from the hardcoded default.
    if schema and schema != "fe":
        sql = sql.replace("SET search_path TO fe, public",
                          f"SET search_path TO {schema}, public")
        sql = sql.replace("SET search_path = fe, pg_temp",
                          f"SET search_path = {schema}, pg_temp")

    stmts = list(_iter_statements(sql))
    logger.debug("Parsed %d statements from %s (schema=%s)", len(stmts), path.name, schema)

    with _connect(url) as conn:
        with conn.cursor() as cur:
            # Ensure the schema exists — skip gracefully if the user lacks CREATE
            # SCHEMA privilege but the schema was already created by a DBA.
            try:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            except Exception:  # noqa: BLE001 — InsufficientPrivilege is fine if schema exists
                conn.rollback()
            for stmt in stmts:
                cur.execute(stmt)

    logger.info("Applied %s (%d statements) to %s (schema=%s)",
                path.name, len(stmts), redacted_url(url), schema)
    return inspect_schema(url, schema)


def inspect_schema(url: str, schema: str = "fe") -> SchemaReport:
    """What actually exists, so a report reflects the database not the file."""
    report = SchemaReport(schema=schema)
    with _connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name", (schema,))
            for name, kind in cur.fetchall():
                (report.views if kind == "VIEW" else report.tables).append(name)

            cur.execute(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE trigger_schema = %s", (schema,))
            report.triggers = sorted({row[0] for row in cur.fetchall()})

            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s "
                "AND indexdef ILIKE 'CREATE UNIQUE%%' ORDER BY indexname",
                (schema,))
            report.unique_indexes = [row[0] for row in cur.fetchall()]

            for table in report.tables:
                # Identifiers cannot be parameterised; `table` comes from
                # information_schema, not from user input.
                cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
                report.row_counts[table] = cur.fetchone()[0]
    return report
