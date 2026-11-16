"""TerminusDB: load, two-hop query, schema probes, branch, merge and a read at an older commit.

    python compare/terminusdb/run.py

Needs Docker. Runs terminusdb/terminusdb-server:v12.0.9 as container cmp-terminusdb and
drives it with the terminusdb CLI that ships in the image, through `docker exec`. No host
port is opened. The container is removed at the end.

TerminusDB stores typed JSON documents, so the two edge types are link fields on Person:
works_at (an optional Company) and contributes_to (a set of Projects).
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "terminusdb/terminusdb-server:v12.0.9"
NAME = "cmp-terminusdb"
TDB = ["docker", "exec", "-i", NAME, "./terminusdb"]
DB = "admin/cmp"
MAIN = f"{DB}/local/branch/main"
BRANCH = f"{DB}/local/branch/review-add-omar"


def documents():
    staff = dict(DATASET["works_at"])
    contrib = {}
    for p, j in DATASET["contributes_to"]:
        contrib.setdefault(p, []).append(f"Project/{j}")
    docs = [{"@type": "Company", **c} for c in DATASET["companies"]]
    docs += [{"@type": "Project", **j} for j in DATASET["projects"]]
    docs += [{"@type": "Person", **p, "works_at": f"Company/{staff[p['slug']]}",
              "contributes_to": contrib.get(p["slug"], [])} for p in DATASET["people"]]
    return docs


def wait_ready():
    for _ in range(60):
        if subprocess.run([*TDB, "list"], capture_output=True).returncode == 0:
            return
        time.sleep(1)
    raise SystemExit("terminusdb: server did not come up")


def main():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", NAME, IMAGE], check=True, capture_output=True)
    try:
        wait_ready()
        ver = subprocess.run([*TDB, "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        r = Report("terminusdb", f"{ver.removeprefix('TerminusDB ')} (image {IMAGE})")
        run(r)
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def run(r):
    people_q = (HERE / "people_count.woql").read_text().strip()

    def count(spec):
        _, out = r.run([*TDB, "query", spec, people_q, "--json"], check=True)
        return int(json.loads(out)["bindings"][0]["People"]["@value"])

    def head(spec):
        _, out = r.run([*TDB, "log", spec, "--json", "--count", "1"], check=True)
        return json.loads(out)[0]["identifier"]

    r.step("1. Schema and load")
    r.run([*TDB, "db", "create", DB], check=True)
    schema = (HERE / "schema.json").read_text()
    r.note(schema)
    # Replacing the whole schema graph is how the CLI accepts a schema with its @context.
    r.run([*TDB, "doc", "insert", DB, "--graph_type", "schema", "--full_replace"], input=schema, check=True)
    docs = documents()
    r.note(f"# {len(docs)} documents, one commit; the first Person:")
    r.note(json.dumps(next(d for d in docs if d["@type"] == "Person")))
    r.run([*TDB, "doc", "insert", DB], input=json.dumps(docs), check=True)

    r.step("2. Two-hop query: projects Acme's employees contribute to")
    two_hop = (HERE / "two_hop.woql").read_text().strip()
    r.note(two_hop)
    r.run([*TDB, "query", DB, two_hop], check=True)
    _, out = r.run([*TDB, "query", DB, two_hop, "--json"], check=True)
    got = [b["Project"]["@value"] for b in json.loads(out)["bindings"]]
    r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL", f"got {got}, expected {expected_projects()}")

    r.step("3. Schema probes: a wrong value type, then an undeclared property")
    r.note("# Default: the closed-world schema is checked on every commit. The opt-in raw_json mode is looser, "
           "not stricter, so there is no strict run.")
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        doc = json.dumps({"@type": "Person", **DATASET["probes"][probe]})
        r.note(f"# {label}: {doc}")
        code, _ = r.run([*TDB, "doc", "insert", DB], input=doc)
        r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
    r.note(people_q)
    r.result("people_after_probes", count(DB), "12 means neither probe wrote")
    snapshot = head(DB)

    r.step("4. Branch, merge and a historical read")
    r.run([*TDB, "branch", "create", BRANCH, "--origin", MAIN], check=True)
    omar = json.dumps({"@type": "Person", **DATASET["probes"]["branch_insert"]})
    r.run([*TDB, "doc", "insert", BRANCH, "-m", "add omar"], input=omar, check=True)
    on_branch, on_main = count(BRANCH), count(DB)
    # The docs merge with apply: the diff between the common ancestor (main's head when the
    # branch was cut) and the branch head, applied onto main.
    tip = head(BRANCH)
    r.run([*TDB, "apply", MAIN, "--before-commit", snapshot, "--after-commit", tip,
           "-m", "merge review-add-omar into main"], check=True)
    merged = count(DB)
    r.run([*TDB, "log", DB, "--count", "3"], check=True)
    r.result("branch_isolation", "pass" if (on_branch, on_main) == (13, 12) else "FAIL",
             f"branch {on_branch}, main {on_main} before merge")
    r.result("merge", "pass" if merged == 13 else "FAIL", f"main {merged} after merge")
    then = count(f"{DB}/local/commit/{snapshot}")
    r.result("historical_read", "pass" if then == 12 else "FAIL",
             f"{then} people at commit {snapshot}, read after the merge")

    r.save()
    print(f"terminusdb: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
