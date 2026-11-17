"""LadybugDB: load, two-hop query, schema probes. No branches or versioned reads.

    python compare/ladybugdb/run.py

LadybugDB is an embedded library, so there is no container. The runner itself is stdlib only;
each step pipes Cypher into lbug.py, which runs under
`uv run --with ladybug==0.22.3` (the official Python package, LadybugDB 0.22.3) without
touching the system Python. Needs uv on PATH.
"""

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
PKG = "ladybug==0.22.3"
LBUG = ["uv", "run", "--quiet", "--with", PKG, "python", str(HERE / "lbug.py")]


def write_csv(path, header, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def lit(v):
    if isinstance(v, str):
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return str(v)


def main():
    work = Path(tempfile.mkdtemp(prefix="ladybugdb-compare-"))
    db = str(work / "graph.lbug")
    ver = subprocess.run(["uv", "run", "--quiet", "--with", PKG, "python", "-c",
                          "import ladybug; print(ladybug.__version__)"],
                         capture_output=True, text=True, check=True).stdout.strip()
    r = Report("ladybugdb", f"{ver} (Python package {PKG}, embedded)")

    def cy(text, *flags, check=False, cwd=None):
        return r.run([*LBUG, db, *flags], input=text, check=check, cwd=cwd)

    r.step("1. Schema and load")
    schema = (HERE / "schema.cypher").read_text()
    r.note(schema)
    cy(schema, check=True)
    # Bulk load is COPY FROM files, the import path the docs recommend.
    write_csv(work / "person.csv", ["slug", "name", "age"], [[p["slug"], p["name"], p["age"]] for p in DATASET["people"]])
    write_csv(work / "company.csv", ["slug", "name"], [[c["slug"], c["name"]] for c in DATASET["companies"]])
    write_csv(work / "project.csv", ["slug", "name"], [[j["slug"], j["name"]] for j in DATASET["projects"]])
    write_csv(work / "works_at.csv", ["from", "to"], DATASET["works_at"])
    write_csv(work / "contributes_to.csv", ["from", "to"], DATASET["contributes_to"])
    # Run from the work dir so the COPY paths stay relative.
    load = "\n".join(f"COPY {t} FROM '{f}' (header=true);" for t, f in (
        ("Person", "person.csv"), ("Company", "company.csv"), ("Project", "project.csv"),
        ("WorksAt", "works_at.csv"), ("ContributesTo", "contributes_to.csv"))) + "\n"
    r.note(load)
    cy(load, check=True, cwd=work)

    r.step("2. Two-hop query: projects Acme's employees contribute to")
    two_hop = (HERE / "two_hop.cypher").read_text()
    r.note(two_hop)
    cy(two_hop, check=True)
    _, out = cy(two_hop, "--json", check=True)
    got = [row["project"] for row in json.loads(out)]
    r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL", f"got {got}, expected {expected_projects()}")

    r.step("3. Schema probes: a wrong value type, then an undeclared property")
    r.note("# Node tables are strictly typed by default (CREATE GRAPH ... ANY opts out), so the default run is the strict one.")
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        d = DATASET["probes"][probe]
        stmt = "CREATE (:Person {" + ", ".join(f"{k}: {lit(v)}" for k, v in d.items()) + "});\n"
        r.note(f"# {label}: {stmt.strip()}")
        code, _ = cy(stmt)
        r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
    people = (HERE / "people_count.cypher").read_text()
    r.note(people)
    _, out = cy(people, "--json", check=True)
    r.result("people_after_probes", json.loads(out)[0]["people"], "12 means neither probe wrote")

    r.step("4. Branch, merge and a historical read")
    detail = ("profile: branching = no (None in docs); time_travel = no "
              "(No versioned reads; EXPORT DATABASE dumps only)")
    r.note("# Not run: LadybugDB has no branches or versioned reads. Not emulated.")
    for key in ("branch_isolation", "merge", "historical_read"):
        r.result(key, "not available", detail)

    r.save()
    print(f"ladybugdb: {json.dumps(r.results)}")
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
