"""Guardrail policy constants."""

from __future__ import annotations

# Statement types that are never allowed
DENIED_STATEMENT_TYPES = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "COPY",
        "CALL",
        "EXECUTE",
        "MERGE",
        "REPLACE",
        "ATTACH",
        "DETACH",
        "VACUUM",
        "ANALYZE",
        "REFRESH",
        "SET",
        "SHOW",
        "USE",
        "LOAD",
        "UNLOAD",
        "EXPORT",
        "IMPORT",
    }
)

ALLOWED_STATEMENT_TYPES = frozenset({"SELECT", "WITH"})

# Dangerous function names (lowercased) to block when present in AST
DANGEROUS_FUNCTIONS = frozenset(
    {
        "pg_read_file",
        "pg_write_file",
        "pg_ls_dir",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "load_file",
        "intooutfile",
        "sleep",
        "benchmark",
        "sys_exec",
        "sys_eval",
        "xp_cmdshell",
    }
)
