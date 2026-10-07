"""Statement-level read-only guard for legacy-database introspection.

PRD FR-033: "GATHER-db shall reverse-engineer MS SQL metadata and stored-procedure
signatures **without reading production business rows**." NFR-1 adds: metadata
views only, no DDL.

Two things this is NOT:
  * a substitute for a least-privilege database login -- use both
  * a general SQL sanitiser -- it is an allowlist for a fixed, small set of
    introspection queries that this codebase writes itself

Design choice: allowlist the catalogue objects a statement may reference, rather
than blocklisting dangerous keywords. A blocklist fails open on anything the
author did not think of; an allowlist fails closed. A query that references
`dbo.Customers` is rejected because `dbo.Customers` is not a catalogue object,
without anyone having to predict that table's name.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class UnsafeStatementError(RuntimeError):
    """A statement failed the read-only guard and was not executed."""


#: Catalogue namespaces an introspection query may read.
ALLOWED_OBJECT_PREFIXES: tuple[str, ...] = (
    "information_schema.",
    "sys.",
)

#: Table-valued catalogue functions that are safe to call. They analyse metadata
#: and do not execute the object they describe.
ALLOWED_FUNCTIONS: tuple[str, ...] = (
    "sys.dm_exec_describe_first_result_set",
    "sys.dm_exec_describe_first_result_set_for_object",
)

#: Anything that mutates state, escalates, or executes arbitrary code.
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "insert", "update", "delete", "merge", "truncate", "drop", "alter",
    "create", "grant", "revoke", "deny", "backup", "restore", "shutdown",
    "reconfigure", "kill", "openrowset", "openquery", "opendatasource",
    "bulk", "xp_cmdshell", "sp_executesql", "sp_addlogin", "sp_configure",
    "waitfor", "into",
)

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")
#: schema-qualified references, e.g. sys.objects / INFORMATION_SCHEMA.COLUMNS
_QUALIFIED = re.compile(r"\b([A-Za-z_][\w$#]*)\s*\.\s*([A-Za-z_][\w$#]*)")


def _strip_noise(sql: str) -> str:
    """Remove comments and string literals before analysis.

    String literals are removed so a procedure name inside a quoted
    `EXEC dbo.MyProc` argument to a catalogue function is not mistaken for a
    reference to a business object.
    """
    cleaned = _COMMENT_BLOCK.sub(" ", sql)
    cleaned = _COMMENT_LINE.sub(" ", cleaned)
    cleaned = _STRING_LITERAL.sub("''", cleaned)
    return cleaned


def assert_read_only(sql: str) -> None:
    """Raise UnsafeStatementError unless `sql` is a safe catalogue read."""
    if not sql or not sql.strip():
        raise UnsafeStatementError("empty statement")

    cleaned = _strip_noise(sql)
    lowered = cleaned.lower()

    # 1. exactly one statement
    if ";" in cleaned.rstrip().rstrip(";"):
        raise UnsafeStatementError(
            "multiple statements are not permitted in one introspection call"
        )

    # 2. must be a SELECT (or a WITH ... SELECT)
    first = (_WORD.search(cleaned) or _EmptyMatch()).group(0).lower()
    if first not in {"select", "with"}:
        raise UnsafeStatementError(
            f"only SELECT is permitted; statement starts with {first!r}"
        )

    # 3. no mutating or escalating keyword anywhere
    words = {w.lower() for w in _WORD.findall(cleaned)}
    hits = sorted(words & set(FORBIDDEN_KEYWORDS))
    if hits:
        raise UnsafeStatementError(
            f"forbidden keyword(s) present: {', '.join(hits)}"
        )

    # 4. every schema-qualified reference must be a catalogue object
    offenders: list[str] = []
    for schema, obj in _QUALIFIED.findall(cleaned):
        qualified = f"{schema.lower()}.{obj.lower()}"
        if qualified in ALLOWED_FUNCTIONS:
            continue
        if any(qualified.startswith(p) for p in ALLOWED_OBJECT_PREFIXES):
            continue
        # Column references like t.name are qualified too; only flag a reference
        # whose prefix looks like a schema rather than a declared alias.
        if schema.lower() in {"information_schema", "sys"}:
            continue
        if _looks_like_alias(schema, cleaned):
            continue
        offenders.append(f"{schema}.{obj}")

    if offenders:
        raise UnsafeStatementError(
            "statement references non-catalogue object(s): "
            f"{', '.join(sorted(set(offenders)))}. Introspection may only read "
            f"{' / '.join(ALLOWED_OBJECT_PREFIXES)}"
        )

    logger.debug("read-only guard passed for statement: %s", sql[:120])


def _looks_like_alias(candidate: str, sql: str) -> bool:
    """True when `candidate` is bound as a table alias in this statement.

    Catalogue queries alias heavily (`FROM sys.objects o`), and `o.name` must not
    be read as a reference to schema `o`.
    """
    pattern = re.compile(
        r"(?:from|join|apply|,)\s+"
        r"(?:[A-Za-z_][\w$#]*\s*\.\s*[A-Za-z_][\w$#]*|\([^)]*\))"
        r"(?:\s+as)?\s+" + re.escape(candidate) + r"\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(sql))


class _EmptyMatch:
    def group(self, _n: int) -> str:  # pragma: no cover
        return ""


def is_read_only(sql: str) -> bool:
    """Non-raising variant."""
    try:
        assert_read_only(sql)
        return True
    except UnsafeStatementError:
        return False
