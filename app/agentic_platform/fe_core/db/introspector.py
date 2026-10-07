"""Read-only schema and stored-procedure introspection.

Closes the gap the fit analysis identified as blocking stage 9: nothing in this
estate introspects stored procedures over a live connection. the knowledge base's
`DatabaseSchemaImporter` reads INFORMATION_SCHEMA tables and columns only -- a
grep for `ROUTINE`, `sys.procedures` or `sp_helptext` in it returns nothing, and
SP analysis there comes from parsing `.sql` files and Excel exports instead.

Every statement passes `fe_core.db.safety.assert_read_only` before it reaches a
driver, so a coding mistake cannot turn introspection into a business-data read
or a DDL statement. Combine with a least-privilege login: the guard is defence in
depth, not a substitute for database permissions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from app.agentic_platform.fe_core.db.datasource import Datasource
from app.agentic_platform.fe_core.db.models import (
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    ParameterInfo,
    ProcedureInfo,
    ResultColumnInfo,
    SchemaSnapshot,
    TableInfo,
)
from app.agentic_platform.fe_core.db.safety import assert_read_only

logger = logging.getLogger(__name__)


class DriverUnavailableError(RuntimeError):
    """Neither pyodbc nor pymssql is importable."""


class Cursorish(Protocol):
    description: Any

    def execute(self, sql: str, *params: Any) -> Any: ...
    def fetchall(self) -> list[Any]: ...


# ---------------------------------------------------------------------------
# Catalogue queries. Each is a single SELECT over sys.* / INFORMATION_SCHEMA.*.
# ---------------------------------------------------------------------------

Q_TABLES = """
SELECT s.name AS schema_name, t.name AS table_name, 'table' AS kind
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
UNION ALL
SELECT s.name AS schema_name, v.name AS table_name, 'view' AS kind
FROM sys.views v JOIN sys.schemas s ON s.schema_id = v.schema_id
ORDER BY schema_name, table_name
"""

Q_COLUMNS = """
SELECT s.name AS schema_name, o.name AS table_name, c.name AS column_name,
       c.column_id, ty.name AS data_type, c.max_length, c.precision, c.scale,
       c.is_nullable, c.is_identity, c.is_computed, dc.definition AS default_definition
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc ON dc.parent_object_id = c.object_id
     AND dc.parent_column_id = c.column_id
WHERE o.type IN ('U', 'V')
ORDER BY s.name, o.name, c.column_id
"""

Q_INDEXES = """
SELECT s.name AS schema_name, o.name AS table_name, i.name AS index_name,
       i.is_unique, i.is_primary_key, i.type_desc, c.name AS column_name,
       ic.key_ordinal
FROM sys.indexes i
JOIN sys.objects o ON o.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE o.type = 'U' AND i.name IS NOT NULL AND ic.is_included_column = 0
ORDER BY s.name, o.name, i.name, ic.key_ordinal
"""

Q_FOREIGN_KEYS = """
SELECT fk.name AS fk_name, ps.name AS parent_schema, pt.name AS parent_table,
       pc.name AS parent_column, rs.name AS ref_schema, rt.name AS ref_table,
       rc.name AS ref_column, fkc.constraint_column_id
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.objects pt ON pt.object_id = fk.parent_object_id
JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
     AND pc.column_id = fkc.parent_column_id
JOIN sys.objects rt ON rt.object_id = fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
     AND rc.column_id = fkc.referenced_column_id
ORDER BY fk.name, fkc.constraint_column_id
"""

Q_PROCEDURES = """
SELECT s.name AS schema_name, o.name AS proc_name, o.type AS obj_type,
       o.create_date, o.modify_date
FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('P', 'FN', 'TF', 'IF')
ORDER BY s.name, o.name
"""

Q_PARAMETERS = """
SELECT s.name AS schema_name, o.name AS proc_name, p.name AS param_name,
       p.parameter_id, ty.name AS data_type, p.max_length, p.is_output,
       p.has_default_value
FROM sys.parameters p
JOIN sys.objects o ON o.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.types ty ON ty.user_type_id = p.user_type_id
WHERE o.type IN ('P', 'FN', 'TF', 'IF')
ORDER BY s.name, o.name, p.parameter_id
"""

#: Result-shape discovery. This ANALYSES the procedure; it does not execute it.
Q_RESULT_SHAPE = """
SELECT name, column_ordinal, system_type_name, is_nullable
FROM sys.dm_exec_describe_first_result_set_for_object(OBJECT_ID(?), NULL)
ORDER BY column_ordinal
"""


class SqlServerIntrospector:
    """MS SQL Server / Azure SQL introspection over pyodbc or pymssql."""

    dialect = "sqlserver"

    def __init__(self, datasource: Datasource, driver: str | None = None):
        if datasource.dialect != self.dialect:
            raise ValueError(
                f"{type(self).__name__} cannot handle dialect {datasource.dialect!r}"
            )
        self.datasource = datasource
        self._driver_override = driver

    # -- connection -------------------------------------------------------
    def _connect(self) -> Any:
        """Open a connection, preferring pyodbc with the newest ODBC driver."""
        last_error: Exception | None = None
        try:
            import pyodbc  # type: ignore

            for driver in self._candidate_drivers(pyodbc):
                try:
                    return pyodbc.connect(
                        self.datasource.odbc_connection_string(driver),
                        timeout=self.datasource.login_timeout_seconds,
                        readonly=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.debug("pyodbc driver %s failed: %s", driver, exc)
        except ImportError:
            logger.info("pyodbc unavailable; trying pymssql")

        try:
            import pymssql  # type: ignore

            return pymssql.connect(
                server=self.datasource.host,
                port=str(self.datasource.port),
                user=self.datasource.username,
                password=self.datasource.password,
                database=self.datasource.database,
                login_timeout=self.datasource.login_timeout_seconds,
            )
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        raise DriverUnavailableError(
            "no working MS SQL driver. Install `pyodbc` plus the Microsoft ODBC "
            "driver, or `pymssql`. Last error: "
            f"{last_error if last_error else 'none attempted'}"
        )

    def _candidate_drivers(self, pyodbc: Any) -> list[str]:
        if self._driver_override:
            return [self._driver_override]
        try:
            installed = set(pyodbc.drivers())
        except Exception:  # noqa: BLE001
            installed = set()
        preferred = [
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "SQL Server Native Client 11.0",
            "SQL Server",
        ]
        ordered = [d for d in preferred if d in installed]
        # Never hardcode a single driver version: the knowledge base pins "ODBC Driver 17" and
        # fails outright on hosts that only ship 18.
        return ordered or preferred

    # -- execution --------------------------------------------------------
    def _query(self, cursor: Cursorish, sql: str, *params: Any) -> list[dict]:
        assert_read_only(sql)
        cursor.execute(sql, *params) if params else cursor.execute(sql)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # -- snapshot ---------------------------------------------------------
    def snapshot(self, include_procedures: bool = True) -> SchemaSnapshot:
        snap = SchemaSnapshot(
            snapshot_id=uuid.uuid4().hex[:12],
            datasource=self.datasource.name,
            dialect=self.dialect,
            catalog=self.datasource.database,
            db_schema=self.datasource.db_schema,
            captured_at=datetime.now(timezone.utc),
        )

        conn = self._connect()
        try:
            cursor = conn.cursor()
            self._load_tables(cursor, snap)
            if include_procedures:
                self._load_procedures(cursor, snap)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        snap.checksum = snap.compute_checksum()
        logger.info("Schema snapshot %s: %s", snap.snapshot_id, snap.summary())
        return snap

    def _load_tables(self, cursor: Cursorish, snap: SchemaSnapshot) -> None:
        tables: dict[tuple[str, str], TableInfo] = {}
        for row in self._query(cursor, Q_TABLES):
            schema, name = row["schema_name"], row["table_name"]
            if self.datasource.excluded(name):
                continue
            tables[(schema, name)] = TableInfo(
                schema_name=schema, name=name, kind=row["kind"]
            )

        for row in self._query(cursor, Q_COLUMNS):
            key = (row["schema_name"], row["table_name"])
            table = tables.get(key)
            if table is None:
                continue
            table.columns.append(ColumnInfo(
                name=row["column_name"],
                ordinal=int(row["column_id"]),
                data_type=row["data_type"],
                max_length=_int_or_none(row.get("max_length")),
                precision=_int_or_none(row.get("precision")),
                scale=_int_or_none(row.get("scale")),
                nullable=bool(row.get("is_nullable")),
                default=row.get("default_definition"),
                is_identity=bool(row.get("is_identity")),
                is_computed=bool(row.get("is_computed")),
            ))

        index_acc: dict[tuple[str, str, str], IndexInfo] = {}
        for row in self._query(cursor, Q_INDEXES):
            key = (row["schema_name"], row["table_name"])
            if key not in tables:
                continue
            ikey = (*key, row["index_name"])
            index = index_acc.get(ikey)
            if index is None:
                index = IndexInfo(
                    name=row["index_name"],
                    columns=[],
                    is_unique=bool(row.get("is_unique")),
                    is_primary_key=bool(row.get("is_primary_key")),
                    type_desc=row.get("type_desc"),
                )
                index_acc[ikey] = index
                tables[key].indexes.append(index)
            index.columns.append(row["column_name"])

        for (schema, name, _iname), index in index_acc.items():
            if index.is_primary_key:
                tables[(schema, name)].primary_key = list(index.columns)

        fk_acc: dict[str, ForeignKeyInfo] = {}
        for row in self._query(cursor, Q_FOREIGN_KEYS):
            key = (row["parent_schema"], row["parent_table"])
            if key not in tables:
                continue
            fk = fk_acc.get(row["fk_name"])
            if fk is None:
                fk = ForeignKeyInfo(
                    name=row["fk_name"], columns=[],
                    referenced_schema=row["ref_schema"],
                    referenced_table=row["ref_table"],
                    referenced_columns=[],
                )
                fk_acc[row["fk_name"]] = fk
                tables[key].foreign_keys.append(fk)
            fk.columns.append(row["parent_column"])
            fk.referenced_columns.append(row["ref_column"])

        snap.tables = [tables[k] for k in sorted(tables)]

    def _load_procedures(self, cursor: Cursorish, snap: SchemaSnapshot) -> None:
        procs: dict[tuple[str, str], ProcedureInfo] = {}
        for row in self._query(cursor, Q_PROCEDURES):
            schema, name = row["schema_name"], row["proc_name"]
            if self.datasource.excluded(name):
                continue
            procs[(schema, name)] = ProcedureInfo(
                schema_name=schema,
                name=name,
                kind="procedure" if str(row["obj_type"]).strip() == "P" else "function",
                created_at=row.get("create_date"),
                modified_at=row.get("modify_date"),
            )

        for row in self._query(cursor, Q_PARAMETERS):
            proc = procs.get((row["schema_name"], row["proc_name"]))
            if proc is None:
                continue
            ordinal = int(row["parameter_id"])
            proc.parameters.append(ParameterInfo(
                name=(row.get("param_name") or "").lstrip("@") or f"p{ordinal}",
                ordinal=ordinal,
                data_type=row["data_type"],
                max_length=_int_or_none(row.get("max_length")),
                direction=(
                    "return" if ordinal == 0
                    else "out" if bool(row.get("is_output")) else "in"
                ),
                has_default=bool(row.get("has_default_value")),
            ))

        for proc in procs.values():
            self._describe_result(cursor, proc, snap)

        snap.procedures = [procs[k] for k in sorted(procs)]

    def _describe_result(
        self, cursor: Cursorish, proc: ProcedureInfo, snap: SchemaSnapshot
    ) -> None:
        """Discover the first result set without executing the procedure.

        Fails for procedures that build their result with dynamic SQL, which is
        common in legacy estates. That is recorded, not fatal -- the DAO
        generator falls back to explicit mapping.
        """
        try:
            rows = self._query(cursor, Q_RESULT_SHAPE, proc.qualified)
        except Exception as exc:  # noqa: BLE001
            proc.result_discoverable = False
            proc.result_error = str(exc)[:200]
            snap.warnings.append(
                f"result shape undiscoverable for {proc.qualified}: {str(exc)[:120]}"
            )
            return
        proc.result_columns = [
            ResultColumnInfo(
                name=r.get("name"),
                ordinal=int(r.get("column_ordinal") or 0),
                data_type=r.get("system_type_name") or "unknown",
                nullable=bool(r.get("is_nullable")),
            )
            for r in rows
        ]
        proc.result_discoverable = True


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_REGISTRY: dict[str, type] = {"sqlserver": SqlServerIntrospector}


def build_introspector(datasource: Datasource, driver: str | None = None):
    """Dialect-pluggable factory.

    Only `sqlserver` is implemented. Sybase, Oracle and PostgreSQL are accepted
    by the datasource schema so a second legacy system can be registered before
    its introspector exists, and the error names the gap precisely.
    """
    impl = _REGISTRY.get(datasource.dialect)
    if impl is None:
        raise NotImplementedError(
            f"no introspector for dialect {datasource.dialect!r}; implemented: "
            f"{', '.join(sorted(_REGISTRY))}"
        )
    return impl(datasource, driver=driver)
