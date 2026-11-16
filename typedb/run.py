"""TypeDB: load, two-hop query, schema probes. No branches or versioned reads.

    python compare/typedb/run.py

Needs Docker and curl. Runs typedb/typedb:3.14.2 (TypeDB Community Edition) as container
cmp-typedb with its HTTP API on host port 18010, sends TypeQL with curl, and removes the
container at the end. Each request is a one-shot transaction (schema, write or read).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "typedb/typedb:3.14.2"
NAME = "cmp-typedb"
PORT = 18010
URL = f"http://127.0.0.1:{PORT}"
DB = "cmp"


def lit(v):
    return json.dumps(v) if isinstance(v, str) else str(v)


def person(var, d):
    return f"${var} isa person, " + ", ".join(f"has {k} {lit(v)}" for k, v in d.items()) + ";"


def load_query():
    out = ["insert"]
    out += [f"  {person(p['slug'], p)}" for p in DATASET["people"]]
    for kind, key in (("company", "companies"), ("project", "projects")):
        out += [f"  ${d['slug']} isa {kind}, has slug {lit(d['slug'])}, has name {lit(d['name'])};" for d in DATASET[key]]
    out += [f"  works-at (employee: ${a}, employer: ${b});" for a, b in DATASET["works_at"]]
    out += [f"  contributes-to (contributor: ${a}, project: ${b});" for a, b in DATASET["contributes_to"]]
    # Return one counted row instead of every inserted concept.
    out += ["reduce $inserted_rows = count;"]
    return "\n".join(out) + "\n"


def signin():
    body = json.dumps({"username": "admin", "password": "password"}).encode()
    for _ in range(60):
        try:
            req = urllib.request.Request(f"{URL}/v1/signin", body, {"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=5).read())["token"]
        except OSError:
            time.sleep(1)
    raise SystemExit("typedb: server did not come up")


def main():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8000", IMAGE],
                   check=True, capture_output=True)
    work = Path(tempfile.mkdtemp(prefix="typedb-compare-"))
    try:
        os.environ["TOKEN"] = signin()
        req = urllib.request.Request(f"{URL}/v1/version")
        ver = json.loads(urllib.request.urlopen(req, timeout=5).read())
        r = Report("typedb", f"{ver['distribution']} {ver['version']} (image {IMAGE})")
        run(r, work)
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def run(r, work):
    auth = '-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"'
    n = [0]

    def tql(text, tx, check=False):
        """POST one TypeQL query as a one-shot transaction; curl exits 22 on an HTTP error."""
        n[0] += 1
        body = f"q{n[0]:02d}.json"
        (work / body).write_text(json.dumps({"databaseName": DB, "transactionType": tx,
                                    "queryOptions": {"includeQueryStructure": False}, "query": text}))
        # Run from the work dir so the request file path stays relative.
        return r.run(f"curl -sS --fail-with-body -X POST {URL}/v1/query {auth} --data-binary @{body}", cwd=work, check=check)

    def count():
        _, out = tql((HERE / "people_count.tql").read_text(), "read", check=True)
        return json.loads(out)["answers"][0]["data"]["people"]["value"]

    r.note("# Signed in as admin through POST /v1/signin; the token is in $TOKEN.")

    r.step("1. Schema and load")
    r.run(f"curl -sS --fail-with-body -X POST {URL}/v1/databases/{DB} {auth}", check=True)
    schema = (HERE / "schema.tql").read_text()
    r.note(schema)
    tql(schema, "schema", check=True)
    load = load_query()
    lines = load.splitlines()
    r.note(f"# load query, one write transaction ({len(lines) - 2} statements); first person, first edge:")
    r.note("\n".join([lines[0], lines[1], next(s for s in lines if "works-at" in s), "  ...", lines[-1]]))
    tql(load, "write", check=True)
    r.note("# instances per type:")
    tql("match $x isa! $t;\nreduce $n = count($x) groupby $t;\n", "read", check=True)

    r.step("2. Two-hop query: projects Acme's employees contribute to")
    two_hop = (HERE / "two_hop.tql").read_text()
    r.note(two_hop)
    _, out = tql(two_hop, "read", check=True)
    got = [a["data"]["project"]["value"] for a in json.loads(out)["answers"]]
    r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL", f"got {got}, expected {expected_projects()}")

    r.step("3. Schema probes: a wrong value type, then an undeclared property")
    r.note("# TypeDB validates every write against the schema; there is no looser or stricter mode to switch.")
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        q = "insert\n  " + person("p", DATASET["probes"][probe]) + "\n"
        r.note(f"# {label}:\n{q.rstrip()}")
        code, _ = tql(q, "write")
        r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
    r.note((HERE / "people_count.tql").read_text())
    r.result("people_after_probes", count(), "12 means neither probe wrote")

    r.step("4. Branch, merge and a historical read")
    detail = ("profile: branching = no (No branches of the data); time_travel = partial "
              "(No versioned reads; backups are DIY via export and import in CE)")
    r.note("# Not run: TypeDB has no branches or versioned reads. Not emulated.")
    for key in ("branch_isolation", "merge", "historical_read"):
        r.result(key, "not available", detail)

    r.save()
    print(f"typedb: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
