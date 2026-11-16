"""HelixDB: load, two-hop query, schema probes, branch and snapshot.

    python compare/helixdb/run.py

Needs Docker and curl. Runs the standalone server image ghcr.io/helixdb/helixdb:v0.0.15 (the newest
published image on 2026-11-11; the profile rated v0.0.12) in memory mode as container cmp-helixdb, published on
127.0.0.1:19069 because the image has no shell or curl to docker exec into. All requests go to
the native operation-tree endpoint POST /v2/query as raw JSON, the body the v3 SDKs build.

HelixDB v3 has no schema file or declared property types: labels and properties come into being
on write. So step 1 only loads data, and the probes go through the same add_n write.
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
IMAGE = "ghcr.io/helixdb/helixdb:v0.0.15"
NAME = "cmp-helixdb"
URL = "http://127.0.0.1:19069"


def value(v):
    """A literal property value with its type tag, as the docs' JSON tab writes it."""
    if isinstance(v, bool):
        return {"value": {"bool": v}}
    if isinstance(v, int):
        return {"value": {"i64": v}}
    return {"value": {"string": v}}


def add_n(var, kind, data):
    props = [[k, value(v)] for k, v in data.items()]
    return {"query": {"name": var, "root": {"add_n": {"label": kind, "properties": props}}}}


def add_e(var, kind, src, dst):
    return {"query": {"name": var, "root": {"add_e": {
        "input": {"nodes": {"reference": {"var": src}}}, "label": kind, "to": {"var": dst}, "properties": []}}}}


def write(name, entries, returns):
    return {"request_type": "write", "query_name": name,
            "query": {"write": {"entries": entries, "returns": returns}}}


def post(r, body, path="/v2/query"):
    """POST a JSON body; returns (http_status, parsed reply). curl prints the status on the last line."""
    cmd = shlex.join(["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json",
                      "--data-binary", "@-", "-w", r"\n%{http_code}", f"{URL}{path}"])
    _, out = r.run(cmd, input=json.dumps(body), check=True)
    text, status = out.rstrip().rsplit("\n", 1)
    return int(status), json.loads(text)


def query(r, file):
    status, reply = post(r, json.loads((HERE / file).read_text()))
    if status != 200:
        r.save()
        raise SystemExit(f"helixdb: query {file} failed: {status} {reply}")
    return reply


def main():
    r = Report("helixdb", f"server image {IMAGE}")
    try:
        r.step("1. Schema and load")
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        r.run(["docker", "run", "-d", "--name", NAME, "-p", "127.0.0.1:19069:8080", IMAGE], check=True)
        for _ in range(60):
            p = subprocess.run(["curl", "-fsS", f"{URL}/readyz"], capture_output=True, text=True)
            if '"ready":true' in p.stdout:
                break
            time.sleep(1)
        else:
            r.save()
            raise SystemExit("helixdb: server never became ready")
        r.run(["curl", "-fsS", f"{URL}/readyz"], check=True)
        r.note("# No schema step: HelixDB v3 has no schema file or declared types (profile: schema_on_write = partial,"
               " 'No declared types; any label or property accepted').")
        entries = ([add_n(p["slug"], "Person", p) for p in DATASET["people"]]
                   + [add_n(c["slug"], "Company", c) for c in DATASET["companies"]]
                   + [add_n(j["slug"], "Project", j) for j in DATASET["projects"]]
                   + [add_e(f"w{i}", "WORKS_AT", p, c) for i, (p, c) in enumerate(DATASET["works_at"])]
                   + [add_e(f"c{i}", "CONTRIBUTES_TO", p, j) for i, (p, j) in enumerate(DATASET["contributes_to"])])
        r.note(f"# one write batch, one transaction: {len(entries)} entries (add_n per node, add_e per edge)")
        status, reply = post(r, write("load", entries, []))
        if status != 200:
            r.save()
            raise SystemExit(f"helixdb: load failed: {status} {reply}")

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        r.note("# company_projects.json: Company acme <-WORKS_AT- Person -CONTRIBUTES_TO-> dedup, order_by name asc")
        r.note((HERE / "company_projects.json").read_text())
        reply = query(r, "company_projects.json")
        got = [row["name"] for row in reply["company_projects"]]
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")
        # Cross-check only, no result key: the same question through the openCypher endpoint.
        cypher = (HERE / "company_projects.cypher").read_text()
        r.note("# Same question through POST /v2/cypher (cross-check, not scored):")
        r.note(cypher)
        status, reply = post(r, {"query": cypher, "parameters": {"company": "acme"}, "query_name": "company_projects"},
                             "/v2/cypher")
        r.note(f"# /v2/cypher answered HTTP {status}: {reply.get('rows', reply)}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
            body = write(f"probe_{probe}", [add_n("probe", "Person", DATASET["probes"][probe])], [])
            r.note(f"# {label}: {json.dumps(body['query']['write']['entries'][0])}")
            status, reply = post(r, body)
            r.result(f"rejects_{probe}", "accepted" if status == 200 else "rejected",
                     "" if status == 200 else f"HTTP {status}: {reply.get('msg', reply)}")
        r.note((HERE / "people_count.json").read_text())
        n = query(r, "people_count.json")["people"]
        r.result("people_after_probes", n, "12 means neither probe wrote")

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the HelixDB profile rates branching 'no' and time_travel 'no'.")
        r.result("branch_isolation", "not available", "profile: branching = no (None in docs)")
        r.result("merge", "not available", "profile: branching = no (None in docs)")
        r.result("historical_read", "not available",
                 "profile: time_travel = no (backups and point-in-time recovery on roadmap)")
    finally:
        r.run(["docker", "rm", "-f", NAME])
        r.save()
    print(f"helixdb: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
