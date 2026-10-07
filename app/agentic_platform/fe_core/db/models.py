"""Schema-snapshot records (PRD 9.2 "Schema Snapshot": metadata only)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    ordinal: int
    data_type: str
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    default: str | None = None
    is_identity: bool = False
    is_computed: bool = False


class ForeignKeyInfo(BaseModel):
    name: str
    columns: list[str]
    referenced_schema: str
    referenced_table: str
    referenced_columns: list[str]


class IndexInfo(BaseModel):
    name: str
    columns: list[str]
    is_unique: bool = False
    is_primary_key: bool = False
    type_desc: str | None = None


class TableInfo(BaseModel):
    schema_name: str
    name: str
    kind: Literal["table", "view"] = "table"
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.name}"


class ParameterInfo(BaseModel):
    name: str
    ordinal: int
    data_type: str
    max_length: int | None = None
    direction: Literal["in", "out", "inout", "return"] = "in"
    has_default: bool = False


class ResultColumnInfo(BaseModel):
    name: str | None
    ordinal: int
    data_type: str
    nullable: bool = True


class ProcedureInfo(BaseModel):
    """A stored procedure signature.

    `result_columns` may be empty and `result_discoverable` False: SQL Server
    cannot describe the first result set of a procedure that builds it with
    dynamic SQL. That is expected for legacy code, and the DAO generator must
    handle it by falling back to explicit mapping rather than assuming failure.
    """

    schema_name: str
    name: str
    kind: Literal["procedure", "function"] = "procedure"
    parameters: list[ParameterInfo] = Field(default_factory=list)
    result_columns: list[ResultColumnInfo] = Field(default_factory=list)
    result_discoverable: bool = True
    result_error: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.name}"


class SchemaSnapshot(BaseModel):
    snapshot_id: str
    datasource: str
    dialect: str
    catalog: str | None = None
    db_schema: str | None = None
    tables: list[TableInfo] = Field(default_factory=list)
    procedures: list[ProcedureInfo] = Field(default_factory=list)
    captured_at: datetime | None = None
    checksum: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def compute_checksum(self) -> str:
        """Stable digest so an unchanged schema does not create a new version."""
        payload = {
            "tables": [
                {
                    "q": t.qualified,
                    "cols": [(c.name, c.data_type, c.nullable) for c in t.columns],
                    "pk": t.primary_key,
                    "fks": [(f.name, f.columns, f.referenced_table) for f in t.foreign_keys],
                }
                for t in sorted(self.tables, key=lambda x: x.qualified)
            ],
            "procs": [
                {
                    "q": p.qualified,
                    "params": [(x.name, x.data_type, x.direction) for x in p.parameters],
                    "result": [(r.name, r.data_type) for r in p.result_columns],
                }
                for p in sorted(self.procedures, key=lambda x: x.qualified)
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def summary(self) -> dict:
        undiscoverable = [
            p.qualified for p in self.procedures if not p.result_discoverable
        ]
        return {
            "datasource": self.datasource,
            "tables": len(self.tables),
            "views": sum(1 for t in self.tables if t.kind == "view"),
            "procedures": len(self.procedures),
            "procedures_without_result_shape": len(undiscoverable),
            "checksum": self.checksum,
            "warnings": self.warnings,
        }

    def to_pom_schema(self) -> dict:
        """Emit a POM-shaped policy input for GATHER-opa.

        `prerequisite-validator` hard-blocks without `pom-schema.json` containing
        a `fields` array, so publishing this from the snapshot unblocks stage 14
        (PRD R-8).
        """
        fields = []
        for table in sorted(self.tables, key=lambda t: t.qualified):
            for column in table.columns:
                fields.append({
                    "name": f"{table.name}.{column.name}",
                    "type": _pom_type(column.data_type),
                    "source": table.qualified,
                    "nullable": column.nullable,
                })
        return {
            "schemaVersion": "1.0",
            "generatedFrom": {
                "datasource": self.datasource,
                "snapshot_id": self.snapshot_id,
                "checksum": self.checksum,
            },
            "fields": fields,
        }


_NUMERIC = {"int", "bigint", "smallint", "tinyint", "decimal", "numeric",
            "money", "smallmoney", "float", "real"}
_BOOLEAN = {"bit"}
_TEMPORAL = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time"}


def _pom_type(sql_type: str) -> str:
    base = (sql_type or "").split("(")[0].strip().lower()
    if base in _NUMERIC:
        return "number"
    if base in _BOOLEAN:
        return "boolean"
    if base in _TEMPORAL:
        return "string"
    return "string"
