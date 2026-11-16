"""OrientDB Community Edition: load, two-hop query, schema probes, branch and snapshot check.

    python compare/orientdb/run.py     # needs Docker; starts and removes container cmp-orientdb

Statements run through the bundled console (bin/console.sh) inside the container via docker exec, so no
host ports are used. The console takes semicolon-separated commands as its argument, stops at the first
error and exits 1.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "orientdb:3.2.58"
NAME = "cmp-orientdb"
CONNECT = "CONNECT remote:localhost/compare root compare-pass"
CONSOLE = ["docker", "exec", NAME, "/orientdb/bin/console.sh"]


def statements(text):
    """A .osql file as one console argument: statements on one line each, joined by '; '."""
    out = []
    for stmt in text.split(";"):
        stmt = " ".join(stmt.split())
        if stmt:
            out.append(stmt)
    return out


def console(r, stmts, check=False):
    return r.run([*CONSOLE, "; ".join([CONNECT, *stmts])], check=check)


def table_column(out, column):
    """Values of one column from the console's ASCII table output."""
    lines = [ln for ln in out.splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    i = header.index(column)
    return [[c.strip() for c in ln.strip("|").split("|")][i] for ln in lines[1:]]


def load_script():
    q = json.dumps
    lines = [f"CREATE VERTEX Person SET slug = {q(p['slug'])}, name = {q(p['name'])}, age = {p['age']}"
             for p in DATASET["people"]]
    lines += [f"CREATE VERTEX Company SET slug = {q(c['slug'])}, name = {q(c['name'])}" for c in DATASET["companies"]]
    lines += [f"CREATE VERTEX Project SET slug = {q(j['slug'])}, name = {q(j['name'])}" for j in DATASET["projects"]]
    for edge, to, key in (("WorksAt", "Company", "works_at"), ("ContributesTo", "Project", "contributes_to")):
        lines += [f"CREATE EDGE {edge} FROM (SELECT FROM Person WHERE slug = {q(a)}) TO (SELECT FROM {to} WHERE slug = {q(b)})"
                  for a, b in DATASET[key]]
    return lines


def wait_ready():
    for _ in range(90):
        p = subprocess.run([*CONSOLE, "CONNECT remote:localhost root compare-pass; LIST DATABASES"], capture_output=True)
        if p.returncode == 0:
            return
        time.sleep(2)
    raise SystemExit("orientdb: server did not become ready")


def probes(r, suffix):
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        stmt = f"CREATE VERTEX Person CONTENT {json.dumps(DATASET['probes'][probe])}"
        r.note(f"# {label}: {stmt}")
        code, _ = console(r, [stmt])
        r.result(f"rejects_{probe}{suffix}", "rejected" if code != 0 else "accepted")
    _, out = console(r, statements((HERE / "people_count.osql").read_text()), check=True)
    return int(table_column(out, "people")[0])


def main():
    r = Report("orientdb", f"Community Edition 3.2.58 (docker image {IMAGE})")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    try:
        r.run(["docker", "run", "-d", "--name", NAME, "-e", "ORIENTDB_ROOT_PASSWORD=compare-pass", IMAGE], check=True)
        wait_ready()

        r.step("1. Schema and load")
        r.run([*CONSOLE, "CONNECT remote:localhost root compare-pass; CREATE DATABASE remote:localhost/compare root compare-pass plocal"],
              check=True)
        schema = (HERE / "schema.osql").read_text()
        r.note(schema)
        console(r, statements(schema), check=True)
        console(r, load_script(), check=True)
        console(r, ["SELECT @class AS type, count(*) AS n FROM V GROUP BY @class ORDER BY type",
                    "SELECT @class AS type, count(*) AS n FROM E GROUP BY @class ORDER BY type"], check=True)

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        query = (HERE / "company_projects.osql").read_text()
        r.note(query)
        _, out = console(r, statements(query), check=True)
        got = table_column(out, "project")
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        r.note("# Default setup: the schema from step 1 (typed properties, class not strict: OrientDB's hybrid mode).")
        n = probes(r, "")
        r.result("people_after_probes", n, "12 means neither probe wrote")

        r.note("")
        r.note("# Stricter mode: class STRICTMODE (schema-full). Remove whatever the first run wrote, then probe again.")
        strict = (HERE / "strict.osql").read_text()
        r.note(strict)
        console(r, ["DELETE VERTEX Person WHERE slug IN ['mo', 'nia']", *statements(strict)], check=True)
        n = probes(r, "_strict")
        r.result("people_after_probes_strict", n, "12 means neither probe wrote")

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the OrientDB profile rates branching and time travel below yes, and branches are not emulated.")
        r.result("branch_isolation", "not available", "profile: branching = no (no branches of the data)")
        r.result("merge", "not available", "profile: branching = no")
        r.result("historical_read", "not available",
                 "profile: time_travel = partial (full and incremental backups; restore, not query)")
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        r.save()
    print(f"orientdb: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
