"""Omnigraph: load, two-hop query, schema probes, branch and snapshot.

    python compare/omnigraph/run.py        # OMNIGRAPH_BIN overrides the CLI path
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
BIN = os.environ.get("OMNIGRAPH_BIN") or shutil.which("omnigraph") or str(Path.home() / ".local/bin/omnigraph")


def jsonl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def nodes(kind, items):
    return [{"type": kind, "data": d} for d in items]


def edges(kind, pairs):
    return [{"edge": kind, "from": a, "to": b} for a, b in pairs]


def main():
    work = Path(tempfile.mkdtemp(prefix="omnigraph-compare-"))
    graph = str(work / "graph.omni")
    version = json.loads(os.popen(f"{BIN} version --json").read())["version"]
    r = Report("omnigraph", f"v{version}")
    tgt = ["--target", graph]

    r.step("1. Schema and load")
    r.note((HERE / "schema.pg").read_text())
    r.run([BIN, "init", "--schema", str(HERE / "schema.pg"), graph], check=True)
    data = work / "data.jsonl"
    data.write_text(jsonl(
        nodes("Person", DATASET["people"]) + nodes("Company", DATASET["companies"])
        + nodes("Project", DATASET["projects"])
        + edges("WorksAt", DATASET["works_at"]) + edges("ContributesTo", DATASET["contributes_to"])
    ))
    r.run([BIN, "load", "--data", str(data), "--mode", "overwrite", *tgt], check=True)

    r.step("2. Two-hop query: projects Acme's employees contribute to")
    r.note((HERE / "queries.gq").read_text())
    q = [BIN, "query", "company_projects", "--query", str(HERE / "queries.gq"), "--params", '{"company":"acme"}']
    r.run([*q, "--format", "table", *tgt], check=True)
    _, out = r.run([*q, "--json", *tgt], check=True)
    got = [row["project"] for row in json.loads(out)["rows"]]
    r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL", f"got {got}, expected {expected_projects()}")

    r.step("3. Schema probes: a wrong value type, then an undeclared property")
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        f = work / f"{probe}.jsonl"
        f.write_text(jsonl(nodes("Person", [DATASET["probes"][probe]])))
        r.note(f"# {label}: {f.read_text().strip()}")
        code, _ = r.run([BIN, "load", "--data", str(f), "--mode", "append", *tgt])
        r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
    count = [BIN, "query", "people_count", "--query", str(HERE / "queries.gq"), "--json"]
    _, out = r.run([*count, *tgt], check=True)
    probed = json.loads(out)
    r.result("people_after_probes", probed["rows"][0]["people"], "12 means neither probe wrote")
    snapshot = probed["graph_commit_id"]

    r.step("4. Branch, merge and a historical read")
    r.run([BIN, "branch", "create", "review/add-omar", *tgt], check=True)
    f = work / "branch.jsonl"
    f.write_text(jsonl(nodes("Person", [DATASET["probes"]["branch_insert"]])))
    r.run([BIN, "load", "--data", str(f), "--mode", "append", "--branch", "review/add-omar", *tgt], check=True)
    _, on_branch = r.run([*count, "--branch", "review/add-omar", *tgt], check=True)
    _, on_main = r.run([*count, *tgt], check=True)
    r.run([BIN, "branch", "merge", "review/add-omar", "--into", "main", *tgt], check=True)
    _, merged = r.run([*count, *tgt], check=True)
    n = lambda s: json.loads(s)["rows"][0]["people"]  # noqa: E731
    r.result("branch_isolation", "pass" if (n(on_branch), n(on_main)) == (13, 12) else "FAIL",
             f"branch {n(on_branch)}, main {n(on_main)} before merge")
    r.result("merge", "pass" if n(merged) == 13 else "FAIL", f"main {n(merged)} after merge")
    _, then = r.run([*count, "--snapshot", snapshot, *tgt], check=True)
    r.result("historical_read", "pass" if n(then) == 12 else "FAIL",
             f"{n(then)} people at commit {snapshot}, read after the merge")

    r.save()
    print(f"omnigraph: {json.dumps(r.results)}")
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
