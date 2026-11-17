"""Dgraph: load, two-hop query, schema probes (default and strict mutation mode), branch and snapshot.

    python compare/dgraph/run.py

Needs Docker. Runs dgraph/dgraph:v25.5.1 as one container (cmp-dgraph) with a Zero and an Alpha
inside, and talks to Alpha's HTTP API with the curl that ships in the image (docker exec), so no
host ports are opened. The probes run twice: once with the default `--limit mutations=allow`, then
in a fresh container started with `--limit mutations=strict`, Dgraph's documented strict mode.
"""

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "dgraph/dgraph:v25.5.1"
NAME = "cmp-dgraph"
ALPHA = "http://localhost:8080"


def start(r, mode):
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    script = ("dgraph zero --my=localhost:5080 & "
              f"dgraph alpha --my=localhost:7080 --zero=localhost:5080 "
              f"--security whitelist=0.0.0.0/0 --limit mutations={mode}")
    r.run(shlex.join(["docker", "run", "-d", "--name", NAME, IMAGE, "sh", "-c", script]), check=True)
    for _ in range(120):
        p = subprocess.run(["docker", "exec", NAME, "curl", "-s", f"{ALPHA}/health"], capture_output=True, text=True)
        if '"status":"healthy"' in p.stdout:
            # Alpha reports healthy before its group has a tablet leader; probe with a cheap query.
            q = subprocess.run(["docker", "exec", NAME, "curl", "-s", "-H", "Content-Type: application/dql",
                                "--data-binary", "{ q(func: has(dgraph.type)) { count(uid) } }", f"{ALPHA}/query"],
                               capture_output=True, text=True)
            if '"data"' in q.stdout:
                break
        time.sleep(1)
    else:
        r.save()
        raise SystemExit("dgraph: Alpha never became ready")
    r.run(["docker", "exec", NAME, "curl", "-s", f"{ALPHA}/health"], check=True)


def curl(r, path, body, ctype):
    """POST body to Alpha; returns the parsed JSON reply."""
    # Shell-quoted string so the logged command can be pasted as is.
    _, out = r.run(shlex.join(["docker", "exec", "-i", NAME, "curl", "-sS", "-H", f"Content-Type: {ctype}",
                               "--data-binary", "@-", f"{ALPHA}{path}"]), input=body, check=True)
    return json.loads(out)


def mutate(r, rows):
    return curl(r, "/mutate?commitNow=true", json.dumps({"set": rows}), "application/json")


def query(r, file):
    return curl(r, "/query", (HERE / file).read_text(), "application/dql")


def schema_and_load(r):
    r.note((HERE / "schema.dql").read_text())
    reply = curl(r, "/alter", (HERE / "schema.dql").read_text(), "application/dql")
    if "errors" in reply:
        r.save()
        raise SystemExit(f"dgraph: schema rejected: {reply}")
    node = lambda kind, d: {"uid": f"_:{d['slug']}", "dgraph.type": kind, **d}  # noqa: E731
    rows = ([node("Person", p) for p in DATASET["people"]]
            + [node("Company", c) for c in DATASET["companies"]]
            + [node("Project", j) for j in DATASET["projects"]]
            + [{"uid": f"_:{p}", "works_at": {"uid": f"_:{c}"}} for p, c in DATASET["works_at"]]
            + [{"uid": f"_:{p}", "contributes_to": [{"uid": f"_:{j}"}]} for p, j in DATASET["contributes_to"]])
    r.note(f"# one JSON mutation: {len(rows)} objects, blank nodes _:<slug> link the edges")
    reply = mutate(r, rows)
    if "errors" in reply:
        r.save()
        raise SystemExit(f"dgraph: load failed: {reply}")


def people(r):
    return query(r, "people_count.dql")["data"]["people_count"][0]["people"]


def probes(r, suffix):
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        row = {"uid": "_:probe", "dgraph.type": "Person", **DATASET["probes"][probe]}
        r.note(f"# {label}: {json.dumps(row)}")
        reply = mutate(r, [row])
        r.result(f"rejects_{probe}{suffix}", "rejected" if "errors" in reply else "accepted",
                 reply["errors"][0]["message"] if "errors" in reply else "")
    n = people(r)
    r.result(f"people_after_probes{suffix}", n, "12 means neither probe wrote")


def main():
    r = Report("dgraph", f"v25.5.1 ({IMAGE})")
    try:
        r.step("1. Schema and load")
        start(r, "allow")
        schema_and_load(r)

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        r.note((HERE / "queries.dql").read_text())
        reply = query(r, "queries.dql")
        got = [row["project"] for row in reply["data"]["company_projects"]]
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        r.note("# Default mode (--limit mutations=allow): keys without a suffix")
        probes(r, "")
        r.note("")
        r.note("# Strict mode (--limit mutations=strict), fresh container, same schema and data: keys end in _strict")
        start(r, "strict")
        schema_and_load(r)
        probes(r, "_strict")

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the Dgraph profile rates branching 'no' and time_travel 'partial'"
               " (binary backups restored, not queried).")
        r.result("branch_isolation", "not available", "profile: branching = no (None found)")
        r.result("merge", "not available", "profile: branching = no (None found)")
        r.result("historical_read", "not available",
                 "profile: time_travel = partial (binary backups restored, not queried)")
    finally:
        r.run(["docker", "rm", "-f", NAME])
        r.save()
    print(f"dgraph: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
