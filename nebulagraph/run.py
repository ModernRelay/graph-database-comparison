"""NebulaGraph: load, two-hop query, schema probes, branch and snapshot.

    python compare/nebulagraph/run.py

Needs Docker with Compose 2.24+ (for `!reset`). Starts NebulaGraph Community 3.8.0 (metad, storaged,
graphd, all vesoft/nebula-*:v3.8.0) from the official docker-compose-lite.yaml of
vesoft-inc/nebula-docker-compose at tag v3.8.0, kept verbatim here, plus compose.override.yaml,
which publishes no host ports and adds a vesoft/nebula-console:v3.8.0 container. Every statement
runs through that console with docker exec. The compose project is cmp-nebulagraph; its bind-mounted
data and logs live in a temp directory, and `docker compose down -v` removes the stack at the end.

nebula-console -e exits 0 even when a statement fails, so the runner reads "[ERROR" in its output.
It also runs one statement per line, so the .ngql files are joined into one line before sending.
"""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT = "cmp-nebulagraph"
CONSOLE = f"{PROJECT}-console-1"
NGQL = ["docker", "exec", CONSOLE, "nebula-console", "-addr", "graphd", "-port", "9669", "-u", "root", "-p", "nebula"]


def one_line(text):
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def ngql(r, stmt, check=False):
    """Run statements through nebula-console; returns (error line or None, stdout)."""
    _, out = r.run(shlex.join([*NGQL, "-e", stmt]))
    err = next((line for line in out.splitlines() if line.startswith("[ERROR")), None)
    if check and err:
        r.save()
        raise SystemExit(f"nebulagraph: {err}")
    return err, out


def quiet(stmt):
    """Run a statement without logging it (used only for readiness polls)."""
    p = subprocess.run([*NGQL, "-e", stmt], capture_output=True, text=True)
    return p.returncode == 0 and "[ERROR" not in p.stdout, p.stdout


def wait_for(stmt, ok, what, tries=90):
    """Poll until ok(output); returns the whole seconds it took."""
    start = time.monotonic()
    for _ in range(tries):
        good, out = quiet(stmt)
        if good and ok(out):
            return round(time.monotonic() - start)
        time.sleep(1)
    raise SystemExit(f"nebulagraph: timed out waiting for {what}")


def rows(out):
    """Cells of the console's result table, header dropped, quotes stripped."""
    table = [line for line in out.splitlines() if line.startswith("|")]
    return [[c.strip().strip('"') for c in line.strip("|").split("|")] for line in table[1:]]


def leaders_ready(out):
    """SHOW HOSTS: the space's partition count matches its leader count on storaged0."""
    for row in rows(out):
        if row[0] == "storaged0" and row[4].startswith("cmp:") and row[5].startswith("cmp:"):
            return row[4] == row[5]
    return False


def lit(v):
    return json.dumps(v)  # nGQL string and int literals match JSON's


def load_statements():
    vertex = lambda tag, cols, items: (  # noqa: E731
        f"INSERT VERTEX {tag}({', '.join(cols)}) VALUES "
        + ", ".join(f"{lit(d['slug'])}:({', '.join(lit(d[c]) for c in cols)})" for d in items) + ";")
    edge = lambda kind, pairs: (  # noqa: E731
        f"INSERT EDGE {kind}() VALUES " + ", ".join(f"{lit(a)}->{lit(b)}:()" for a, b in pairs) + ";")
    return " ".join([
        "USE cmp;",
        vertex("Person", ["name", "age"], DATASET["people"]),
        vertex("Company", ["name"], DATASET["companies"]),
        vertex("Project", ["name"], DATASET["projects"]),
        edge("WorksAt", DATASET["works_at"]),
        edge("ContributesTo", DATASET["contributes_to"]),
    ])


def people(r):
    _, out = ngql(r, one_line((HERE / "people_count.ngql").read_text()), check=True)
    return int(rows(out)[0][0])


def main():
    work = Path(tempfile.mkdtemp(prefix="nebulagraph-compare-"))
    compose = ["docker", "compose", "-p", PROJECT, "--project-directory", str(work),
               "-f", str(HERE / "docker-compose-lite.yaml"), "-f", str(HERE / "compose.override.yaml")]
    # The official file reads ${TZ}; set it so Compose doesn't warn about an unset variable.
    up = "TZ=UTC " + shlex.join(compose)
    r = Report("nebulagraph", "Community 3.8.0 (vesoft/nebula-graphd, -metad, -storaged, -console:v3.8.0)")
    try:
        r.step("1. Schema and load")
        r.run(up + " up -d --quiet-pull", check=True)
        r.run(["docker", "exec", f"{PROJECT}-graphd-1", "/usr/local/nebula/bin/nebula-graphd", "--version"], check=True)
        wait_for("SHOW HOSTS", lambda out: True, "graphd to accept connections")
        # Since 3.0 storaged must be registered with metad before it serves data.
        ngql(r, 'ADD HOSTS "storaged0":9779', check=True)
        wait_for("SHOW HOSTS", lambda out: '"ONLINE"' in out, "storaged0 to come online")
        ngql(r, "SHOW HOSTS", check=True)

        schema = [line for line in (HERE / "schema.ngql").read_text().splitlines() if line.strip()]
        r.note("\n".join(schema))
        ngql(r, schema[0], check=True)  # CREATE SPACE
        # The docs say to wait two heartbeat cycles (20 s by default) before using a new space or schema.
        waited = wait_for("USE cmp", lambda out: True, "the new space")
        r.note(f"# waited {waited} s for USE cmp to succeed")
        ngql(r, " ".join(schema[1:]), check=True)
        # Writes fail with "RPC failure" until every partition of the space has a Raft leader.
        waited = wait_for("SHOW HOSTS", leaders_ready, "partition leaders", tries=120)
        time.sleep(max(0, 20 - waited))
        r.note(f"# waited {max(waited, 20)} s (about): until every partition had a leader, and at least two heartbeats"
               " (20 s) for the tags, edge types and index to reach storaged")
        ngql(r, "SHOW HOSTS", check=True)
        ngql(r, load_statements(), check=True)
        n = people(r)
        if n != len(DATASET["people"]):
            r.save()
            raise SystemExit(f"nebulagraph: loaded {n} people, expected {len(DATASET['people'])}")

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        r.note((HERE / "company_projects.ngql").read_text())
        _, out = ngql(r, one_line((HERE / "company_projects.ngql").read_text()), check=True)
        got = [row[0] for row in rows(out)]
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
            d = DATASET["probes"][probe]
            cols = [k for k in d if k != "slug"]
            stmt = f"USE cmp; INSERT VERTEX Person({', '.join(cols)}) VALUES {lit(d['slug'])}:({', '.join(lit(d[c]) for c in cols)});"
            r.note(f"# {label}")
            err, _ = ngql(r, stmt)
            r.result(f"rejects_{probe}", "rejected" if err else "accepted", err or "")
        r.note((HERE / "people_count.ngql").read_text())
        r.result("people_after_probes", people(r), "12 means neither probe wrote")

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the NebulaGraph profile rates branching 'no' and time_travel 'partial'"
               " (snapshots and BR backups restored, not queried).")
        r.result("branch_isolation", "not available", "profile: branching = no (None found)")
        r.result("merge", "not available", "profile: branching = no (None found)")
        r.result("historical_read", "not available",
                 "profile: time_travel = partial (snapshots and BR backups restored, not queried)")
    finally:
        r.run(up + " down -v")
        r.save()
        shutil.rmtree(work, ignore_errors=True)
    print(f"nebulagraph: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
