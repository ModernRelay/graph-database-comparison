"""Run Cypher against an embedded LadybugDB file: one statement per `;`-terminated chunk.

    uv run --with ladybug==0.22.1 python lbug.py DB_PATH [--json] < statements.cypher

Prints each result as a table (or JSON rows with --json). Stops at the first error,
prints it and exits 1, like a shell would.
"""

import json
import sys

import ladybug as lb


def main():
    path, as_json = sys.argv[1], "--json" in sys.argv[2:]
    conn = lb.Connection(lb.Database(path))
    stmts = [s.strip() for s in sys.stdin.read().split(";") if s.strip()]
    for stmt in stmts:
        try:
            res = conn.execute(stmt)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        cols, rows = res.get_column_names(), res.get_all()
        if as_json:
            print(json.dumps([dict(zip(cols, row)) for row in rows]))
            continue
        width = [max(len(str(x)) for x in [c] + [row[i] for row in rows]) for i, c in enumerate(cols)]
        print("  ".join(c.ljust(w) for c, w in zip(cols, width)).rstrip())
        for row in rows:
            print("  ".join(str(x).ljust(w) for x, w in zip(row, width)).rstrip())


if __name__ == "__main__":
    main()
