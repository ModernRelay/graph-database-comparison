"""ArcadeDB: load, two-hop query, schema probes, branch and snapshot check.

    python compare/arcadedb/run.py     # needs Docker and curl; starts and removes container cmp-arcadedb

Every statement goes to the server's HTTP API (POST /api/v1/command) through curl on host port 17001.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "arcadedata/arcadedb:26.11.1"
NAME = "cmp-arcadedb"
PORT = 17001
API = f"http://localhost:{PORT}/api/v1"
CURL = ["curl", "-s", "--fail-with-body", "-u", "root:compare-pass", "-X", "POST", "-H", "Content-Type: application/json"]
DB = "compare"


def q(s):
    """A SQL string literal."""
    return json.dumps(s)


def load_script():
    lines = []
    for p in DATASET["people"]:
        lines.append(f"INSERT INTO Person SET slug = {q(p['slug'])}, name = {q(p['name'])}, age = {p['age']};")
    for kind in ("companies", "projects"):
        t = "Company" if kind == "companies" else "Project"
        for c in DATASET[kind]:
            lines.append(f"INSERT INTO {t} SET slug = {q(c['slug'])}, name = {q(c['name'])};")
    for edge, to, key in (("WorksAt", "Company", "works_at"), ("ContributesTo", "Project", "contributes_to")):
        for a, b in DATASET[key]:
            lines.append(f"CREATE EDGE {edge} FROM (SELECT FROM Person WHERE slug = {q(a)}) "
                         f"TO (SELECT FROM {to} WHERE slug = {q(b)});")
    return "\n".join(lines) + "\n"


class Arcade:
    def __init__(self, r):
        self.r = r

    def command(self, language, command, params=None, check=False):
        """POST one command; the JSON body goes on stdin (curl -d @-), the statement itself is noted above it."""
        body = json.dumps({"language": language, "command": command, **({"params": params} if params else {})})
        return self.r.run([*CURL, f"{API}/command/{DB}", "-d", "@-"], input=body, check=check)


def wait_ready():
    for _ in range(90):
        p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{API}/ready"],
                           capture_output=True, text=True)
        if p.stdout == "204":
            return
        time.sleep(2)
    raise SystemExit("arcadedb: server did not become ready")


def main():
    r = Report("arcadedb", f"26.11.1 (docker image {IMAGE})")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    try:
        r.run(["docker", "run", "-d", "--name", NAME, "-p", f"{PORT}:2480",
               "-e", "JAVA_OPTS=-Darcadedb.server.rootPassword=compare-pass", IMAGE], check=True)
        wait_ready()
        _, out = r.run(["curl", "-s", "-u", "root:compare-pass", f"{API}/server?mode=basic"], check=True)
        db = Arcade(r)

        r.step("1. Schema and load")
        r.run([*CURL, f"{API}/server", "-d", json.dumps({"command": f"create database {DB}"})], check=True)
        schema = (HERE / "schema.sql").read_text()
        r.note(schema)
        db.command("sqlscript", schema, check=True)
        script = load_script()
        r.note(script)
        db.command("sqlscript", script, check=True)
        counts = "MATCH (n) RETURN labels(n)[0] AS type, count(*) AS n ORDER BY type"
        r.note(f"# openCypher: {counts}")
        db.command("opencypher", counts, check=True)
        counts = "MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS n ORDER BY type"
        r.note(f"# openCypher: {counts}")
        db.command("opencypher", counts, check=True)

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        query = (HERE / "company_projects.sql").read_text()
        r.note(query)
        r.note('# params: {"company": "acme"}')
        _, out = db.command("sql", query, {"company": "acme"}, check=True)
        got = [row["project"] for row in json.loads(out)["result"]]
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        r.note("# Default setup: the schema from step 1 (every property declared and typed, i.e. schema-full).")
        for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
            stmt = f"INSERT INTO Person CONTENT {json.dumps(DATASET['probes'][probe])}"
            r.note(f"# {label}: {stmt}")
            code, _ = db.command("sql", stmt)
            r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
        r.note("# " + (HERE / "people_count.sql").read_text().strip())
        _, out = db.command("sql", (HERE / "people_count.sql").read_text(), check=True)
        r.result("people_after_probes", json.loads(out)["result"][0]["people"], "12 means neither probe wrote")

        r.note("")
        r.note("# Stricter mode: the docs (concepts/schema) list schema-less, schema-full and hybrid operation and say")
        r.note("# 'undeclared properties are accepted as long as no declared constraint is violated'. Step 1 is already")
        r.note("# schema-full, and no documented type setting rejects undeclared properties, so there is no second run.")
        why = "no stricter mode documented; step 1 already declares every property (schema-full)"
        for key in ("rejects_wrong_type_strict", "rejects_undeclared_property_strict", "people_after_probes_strict"):
            r.result(key, "not available", why)

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the ArcadeDB profile rates branching and time travel below yes, and branches are not emulated.")
        r.result("branch_isolation", "not available", "profile: branching = no (none found in docs)")
        r.result("merge", "not available", "profile: branching = no")
        r.result("historical_read", "not available", "profile: time_travel = partial (online full backups you restore, not query)")
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        r.save()
    print(f"arcadedb: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
