"""Neo4j Community Edition: load, two-hop query, schema probes, branch and snapshot check.

    python compare/neo4j/run.py        # needs Docker; starts and removes container cmp-neo4j

Everything goes through cypher-shell inside the container (docker exec), so no host ports are used.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "neo4j:2026.10.2-community"
NAME = "cmp-neo4j"
AUTH = ["-u", "neo4j", "-p", "compare-pass"]
SHELL = ["docker", "exec", "-i", NAME, "cypher-shell", *AUTH, "--format", "plain"]


def lit(value):
    """Python value as a Cypher literal (maps with bare keys, JSON-quoted strings)."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {lit(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(lit(v) for v in value) + "]"
    return json.dumps(value)


def load_script():
    pairs = lambda rows: [{"a": a, "b": b} for a, b in rows]  # noqa: E731
    return "\n".join([
        f"UNWIND {lit(DATASET['people'])} AS row CREATE (:Person {{slug: row.slug, name: row.name, age: row.age}});",
        f"UNWIND {lit(DATASET['companies'])} AS row CREATE (:Company {{slug: row.slug, name: row.name}});",
        f"UNWIND {lit(DATASET['projects'])} AS row CREATE (:Project {{slug: row.slug, name: row.name}});",
        f"UNWIND {lit(pairs(DATASET['works_at']))} AS row MATCH (p:Person {{slug: row.a}}), (c:Company {{slug: row.b}}) "
        "CREATE (p)-[:WORKS_AT]->(c);",
        f"UNWIND {lit(pairs(DATASET['contributes_to']))} AS row MATCH (p:Person {{slug: row.a}}), (j:Project {{slug: row.b}}) "
        "CREATE (p)-[:CONTRIBUTES_TO]->(j);",
    ]) + "\n"


def plain_rows(out):
    """cypher-shell --format plain: a header line, then one line per row; strings come back quoted."""
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    return [json.loads(ln) if ln.startswith('"') else ln for ln in lines[1:]]


def wait_ready():
    for _ in range(90):
        p = subprocess.run(["docker", "exec", NAME, "cypher-shell", *AUTH, "RETURN 1"], capture_output=True)
        if p.returncode == 0:
            return
        time.sleep(2)
    raise SystemExit("neo4j: server did not become ready")


def probes(r, work, suffix, count_query):
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        row = {k: v for k, v in DATASET["probes"][probe].items()}
        stmt = f"CREATE (:Person {lit(row)});\n"
        f = work / f"{probe}{suffix}.cypher"
        f.write_text(stmt)
        r.note(f"# {label}: {stmt.strip()}")
        code, _ = r.run(SHELL, input=stmt)
        r.result(f"rejects_{probe}{suffix}", "rejected" if code != 0 else "accepted")
    _, out = r.run(SHELL, input=count_query, check=True)
    return int(plain_rows(out)[0])


def main():
    work = Path(tempfile.mkdtemp(prefix="neo4j-compare-"))
    r = Report("neo4j", f"Community Edition (docker image {IMAGE})")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    try:
        r.run(["docker", "run", "-d", "--name", NAME, "-e", "NEO4J_AUTH=neo4j/compare-pass", IMAGE], check=True)
        wait_ready()
        r.run(["docker", "exec", NAME, "cypher-shell", *AUTH,
               "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition"], check=True)

        r.step("1. Schema and load")
        schema = (HERE / "schema.cypher").read_text()
        r.note(schema)
        r.run(SHELL, input=schema, check=True)
        r.run(SHELL, input="SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties;", check=True)
        script = load_script()
        (work / "load.cypher").write_text(script)
        r.note(script)
        r.run(SHELL, input=script, check=True)
        r.run(SHELL, input="MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label;", check=True)
        r.run(SHELL, input="MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS n ORDER BY type;", check=True)

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        query = (HERE / "company_projects.cypher").read_text()
        r.note(query)
        _, out = r.run([*SHELL, "--param", 'company => "acme"'], input=query, check=True)
        got = plain_rows(out)
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        count_query = (HERE / "people_count.cypher").read_text()
        r.note("# Default setup: the uniqueness constraints from step 1, nothing else.")
        n = probes(r, work, "", count_query)
        r.result("people_after_probes", n, "12 means neither probe wrote")

        r.note("")
        r.note("# Stricter mode: property type constraints. Community Edition may refuse to create them.")
        strict = (HERE / "strict.cypher").read_text()
        r.note(strict)
        code, _ = r.run(SHELL, input=strict)
        if code == 0:
            r.run(SHELL, input="MATCH (p:Person {slug: 'mo'}) DETACH DELETE p;\nMATCH (p:Person {slug: 'nia'}) DETACH DELETE p;",
                  check=True)
            r.note("# Strict mode: type constraints created; probes rerun on a clean Person set.")
            n = probes(r, work, "_strict", count_query)
            r.result("people_after_probes_strict", n, "12 means neither probe wrote")
        else:
            why = "Community Edition refused to create a property type constraint (see the error above)"
            for key in ("rejects_wrong_type_strict", "rejects_undeclared_property_strict", "people_after_probes_strict"):
                r.result(key, "not available", why)

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the Neo4j profile rates branching and time travel below yes, and branches are not emulated.")
        r.result("branch_isolation", "not available", "profile: branching = no (no branching in the CE or EE feature list)")
        r.result("merge", "not available", "profile: branching = no")
        r.result("historical_read", "not available",
                 "profile: time_travel = partial (CE has offline dump/load only; online backup is Enterprise only)")
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        r.save()
    print(f"neo4j: {json.dumps(r.results)}")
    for f in work.iterdir():
        f.unlink()
    work.rmdir()


if __name__ == "__main__":
    main()
