"""Validate a small analytical SQL subset and execute it in a disposable process.

This boundary is for a local demo with a trusted DuckDB database file. A spawned
process makes timeouts enforceable, but it is not an OS sandbox: the worker still
runs as the current user. See DuckDB's untrusted SQL guidance before deploying it
to a multi-user service.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
import multiprocessing as mp
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ROWS = 1_000
_MAX_TIMEOUT_S = 60.0
_MAX_SQL_CHARS = 20_000
_MAX_CELL_CHARS = 16_384
_MAX_RESULT_CHARS = 2_000_000
_SAFE_STRUCTURAL_EXPRESSIONS = (exp.And, exp.Or, exp.Not, exp.Case, exp.If)

# An allowlist also rejects newly added DuckDB file, network, secret and query
# functions until each one is reviewed. Names are sqlglot's normalized names.
_SAFE_FUNCTIONS = frozenset(
    {
        "ABS", "APPROX_DISTINCT", "AVG", "BOOL_AND", "BOOL_OR", "CAST",
        "CEIL", "CEILING", "COALESCE", "CONCAT", "COUNT", "COUNT_IF",
        "DATE_ADD", "DATE_DIFF", "DATE_SUB", "DATE_TRUNC", "DAY",
        "DAYOFWEEK", "EPOCH", "EPOCH_MS", "EXTRACT", "FLOOR", "GREATEST",
        "HOUR", "IFNULL", "LEAST", "LENGTH", "LOWER", "MAX", "MEDIAN",
        "MIN", "MINUTE", "MONTH", "NULLIF", "PERCENTILE_CONT",
        "QUANTILE_CONT", "REGEXP_EXTRACT", "REGEXP_MATCHES", "ROUND",
        "SECOND", "STDDEV_SAMP", "STRFTIME", "SUBSTR", "SUBSTRING",
        "SUM", "TIMESTAMP_TRUNC", "TRIM", "TRY_CAST", "UPPER", "YEAR",
    }
)


class SQLPolicyError(ValueError):
    """The SQL is outside the supported, read-only analytical subset."""


class QueryExecutionError(RuntimeError):
    """An approved query could not be executed by DuckDB."""


class QueryTimeoutError(QueryExecutionError):
    """The disposable query worker exceeded its wall-clock deadline."""


def _validate_sql(sql: str, allowed_tables: frozenset[str]) -> str:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > _MAX_SQL_CHARS:
        raise SQLPolicyError("SQL must be a nonempty string of at most 20,000 characters")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except (SqlglotError, RecursionError) as exc:
        raise SQLPolicyError("SQL could not be parsed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SQLPolicyError("Exactly one SELECT statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLPolicyError("Only SELECT and WITH SELECT queries are allowed")

    # sqlglot parses most mutating statements as their own nodes. Reject any
    # that somehow appear within a query tree as well as unknown command nodes.
    forbidden = tuple(
        cls for name in (
            "Command", "Create", "Delete", "Drop", "Insert", "Update", "Merge",
            "Copy", "Attach", "Detach", "Set", "Pragma", "Load", "Install",
            "Alter", "Grant", "Revoke", "Transaction", "Use", "Execute",
        ) if (cls := getattr(exp, name, None)) is not None
    )
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise SQLPolicyError("Mutating or administrative SQL is not allowed")

    # Resolve each scope rather than treating every CTE name as globally valid.
    # A nested CTE name can otherwise mask a private physical table outside its
    # own scope. sqlglot identifies CTE/subquery sources as Scope, not Table.
    accounted_tables: set[int] = set()
    try:
        for scope in traverse_scope(tree):
            for node, source in scope.selected_sources.values():
                if isinstance(node, exp.Table):
                    accounted_tables.add(id(node))
                if not isinstance(source, exp.Table):
                    continue
                if source.db or source.catalog or not isinstance(source.this, exp.Identifier):
                    raise SQLPolicyError("Only unqualified physical tables are allowed")
                if source.name.casefold() not in allowed_tables:
                    raise SQLPolicyError(f"Table is not allowed: {source.name}")
    except SQLPolicyError:
        raise
    except Exception as exc:
        raise SQLPolicyError("SQL table sources could not be resolved") from exc
    if any(id(table) not in accounted_tables for table in tree.find_all(exp.Table)):
        raise SQLPolicyError("SQL contains an unresolved table source")

    for function in tree.find_all(exp.Func):
        # SQLGlot derives some pure SQL syntax from Func. They are expression
        # nodes, not calls to DuckDB functions with side effects.
        if isinstance(function, _SAFE_STRUCTURAL_EXPRESSIONS):
            continue
        name = (
            str(function.this).upper()
            if isinstance(function, exp.Anonymous)
            else function.sql_name().upper()
        )
        if name not in _SAFE_FUNCTIONS:
            raise SQLPolicyError(f"Function is not allowed: {name}")
    return tree.sql(dialect="duckdb")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (dt.date, dt.time, dt.datetime, decimal.Decimal)):
        return str(value)
    if isinstance(value, str):
        if len(value) > _MAX_CELL_CHARS:
            raise ValueError("Query result contains an oversized text cell")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        if len(value) > _MAX_CELL_CHARS:
            raise ValueError("Query result contains an oversized binary cell")
        return bytes(value).hex()
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    rendered = str(value)
    if len(rendered) > _MAX_CELL_CHARS:
        raise ValueError("Query result contains an oversized cell")
    return rendered


def _schema_for(connection: Any, allowed_tables: tuple[str, ...]) -> dict[str, list[dict[str, str]]]:
    rows = connection.execute(
        """SELECT c.table_name, c.column_name, c.data_type, t.table_type
           FROM information_schema.columns AS c
           JOIN information_schema.tables AS t
             ON c.table_catalog = t.table_catalog
            AND c.table_schema = t.table_schema
            AND c.table_name = t.table_name
           WHERE c.table_schema = 'main'
           ORDER BY c.table_name, c.ordinal_position"""
    ).fetchall()
    result: dict[str, list[dict[str, str]]] = {name: [] for name in allowed_tables}
    found_types: dict[str, str] = {}
    for table, column, data_type, table_type in rows:
        key = table.casefold()
        if key in result:
            found_types[key] = table_type
            result[key].append({"name": column, "type": data_type})
    for name in allowed_tables:
        if not result[name]:
            raise ValueError(f"Allowed table is missing: {name}")
        if found_types[name] != "BASE TABLE":
            raise ValueError(f"Allowed relation must be a physical table: {name}")
    return result


def _worker(
    sender: Any,
    db_path: str,
    allowed_tables: tuple[str, ...],
    sql: str | None,
    row_limit: int,
) -> None:
    connection = None
    try:
        import duckdb

        # Parse in the worker too: pathological SQL is covered by the same
        # wall-clock deadline as execution, rather than blocking the API host.
        approved_sql = _validate_sql(sql, frozenset(allowed_tables)) if sql is not None else None
        connection = duckdb.connect(
            db_path,
            read_only=True,
            config={
                "enable_external_access": "false",
                "autoload_known_extensions": "false",
                "autoinstall_known_extensions": "false",
                "allow_community_extensions": "false",
                "allow_unsigned_extensions": "false",
                "threads": "2",
                "memory_limit": "512MB",
                "max_temp_directory_size": "0B",
            },
        )
        schema = _schema_for(connection, allowed_tables)
        if approved_sql is None:
            sender.send(("ok", schema))
            return
        # The wrapper bounds transferred rows even if the model supplies a
        # larger LIMIT. Fetch one extra row so callers can see truncation.
        limited_sql = f"SELECT * FROM ({approved_sql}) AS _agent_result LIMIT {row_limit + 1}"
        cursor = connection.execute(limited_sql)
        columns = [item[0] for item in cursor.description]
        if len(columns) > 100:
            raise ValueError("Query result has more than 100 columns")
        fetched = cursor.fetchmany(row_limit + 1)
        truncated = len(fetched) > row_limit
        rows = [[_json_value(cell) for cell in row] for row in fetched[:row_limit]]
        if sum(len(str(cell)) for row in rows for cell in row) > _MAX_RESULT_CHARS:
            raise ValueError("Query result exceeds the 2 MB output budget")
        sender.send(("ok", {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "sql": approved_sql,
        }))
    except SQLPolicyError as exc:
        sender.send(("policy_error", str(exc)))
    except Exception as exc:  # noqa: BLE001 - worker must serialize all failures
        sender.send(("error", f"{type(exc).__name__}: {str(exc)[:400]}"))
    finally:
        if connection is not None:
            connection.close()
        sender.close()


class QueryService:
    """Run approved analytics against trusted physical tables in a DuckDB file."""

    def __init__(self, db_path: str | Path, allowed_tables: Iterable[str]) -> None:
        self.db_path = Path(db_path).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        if isinstance(allowed_tables, str):
            raise TypeError("allowed_tables must be a collection of table names")
        raw_names = tuple(allowed_tables)
        if any(not isinstance(name, str) for name in raw_names):
            raise ValueError("allowed_tables must contain only strings")
        names = tuple(dict.fromkeys(name.casefold() for name in raw_names))
        if not names or any(not _IDENTIFIER.fullmatch(name) for name in names):
            raise ValueError("allowed_tables must contain simple, nonempty table names")
        self.allowed_tables = names

    def _run(self, sql: str | None, row_limit: int, timeout_s: float) -> Any:
        context = mp.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_worker,
            args=(sender, str(self.db_path), self.allowed_tables, sql, row_limit),
            daemon=True,
        )
        started = False
        try:
            process.start()
            started = True
            sender.close()
            if not receiver.poll(timeout_s):
                raise QueryTimeoutError(f"Query exceeded {timeout_s:g} seconds")
            try:
                status, payload = receiver.recv()
            except EOFError as exc:
                raise QueryExecutionError("Query worker exited without a result") from exc
            if status == "error":
                raise QueryExecutionError(payload)
            if status == "policy_error":
                raise SQLPolicyError(payload)
            return payload
        finally:
            receiver.close()
            if started:
                process.join(timeout=0.1)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)

    def query(
        self,
        sql: str,
        row_limit: int = 100,
        timeout_s: float = 10,
    ) -> dict[str, Any]:
        """Execute one approved SELECT and return a bounded JSON-friendly result."""
        if not isinstance(row_limit, int) or isinstance(row_limit, bool) or not 1 <= row_limit <= _MAX_ROWS:
            raise ValueError(f"row_limit must be between 1 and {_MAX_ROWS}")
        if not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= _MAX_TIMEOUT_S:
            raise ValueError(f"timeout_s must be between 0 and {_MAX_TIMEOUT_S:g}")
        if not isinstance(sql, str) or not sql.strip() or len(sql) > _MAX_SQL_CHARS:
            raise SQLPolicyError("SQL must be a nonempty string of at most 20,000 characters")
        return self._run(sql, row_limit, float(timeout_s))

    def schema(self) -> dict[str, list[dict[str, str]]]:
        """Return columns and DuckDB types for the allowed physical tables."""
        return self._run(None, 0, 10.0)
