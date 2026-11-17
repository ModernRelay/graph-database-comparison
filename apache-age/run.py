"""Apache AGE: load, two-hop query, schema probes. No branches or versioned reads.

    python compare/apache-age/run.py

Needs Docker. Runs apache/age:release_PG18_1.8.0 (AGE 1.8.0 on PostgreSQL 18) as container
cmp-apache-age, talks to it with psql through `docker exec`, and removes it at the end.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "apache/age:release_PG18_1.8.0"
NAME = "cmp-apache-age"
PSQL = ["docker", "exec", "-i", NAME, "psql", "-X", "-q", "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
PRELUDE = "LOAD 'age';\nSET search_path = ag_catalog, \"$user\", public;\n"


def lit(v):
    """A Cypher literal for a dataset value."""
    if isinstance(v, str):
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return str(v)


def props(d):
    return "{" + ", ".join(f"{k}: {lit(v)}" for k, v in d.items()) + "}"


def cypher(stmt, cols="v agtype"):
    return f"SELECT * FROM cypher('cmp', $$ {stmt} $$) AS ({cols});"


def load_sql():
    out = [PRELUDE]
    for label, key in (("Person", "people"), ("Company", "companies"), ("Project", "projects")):
        out += [cypher(f"CREATE (:{label} {props(d)})") for d in DATASET[key]]
    for edge, (a_label, b_label), key in (("WorksAt", ("Person", "Company"), "works_at"),
                                          ("ContributesTo", ("Person", "Project"), "contributes_to")):
        out += [cypher(f"MATCH (a:{a_label} {{slug: {lit(a)}}}), (b:{b_label} {{slug: {lit(b)}}}) "
                       f"CREATE (a)-[:{edge}]->(b)") for a, b in DATASET[key]]
    return "\n".join(out) + "\n"


def wait_ready():
    # The postgres entrypoint runs init scripts on a socket-only server, then restarts;
    # a TCP connection only succeeds once the final server is up.
    for _ in range(60):
        p = subprocess.run(["docker", "exec", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres", "-c", "SELECT 1"],
                           capture_output=True)
        if p.returncode == 0:
            return
        time.sleep(1)
    raise SystemExit("apache-age: postgres did not come up")


def main():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", NAME, "-e", "POSTGRES_PASSWORD=compare", IMAGE],
                   check=True, capture_output=True)
    try:
        wait_ready()
        q = "SELECT extversion FROM pg_extension WHERE extname = 'age'; SHOW server_version;"
        ver = subprocess.run([*PSQL, "-At", "-c", q], capture_output=True, text=True, check=True).stdout.split()
        r = Report("apache-age", f"{ver[0]} on PostgreSQL {ver[1]} (image {IMAGE})")
        run(r)
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def run(r):
    def sql(text, *flags, check=False):
        return r.run([*PSQL, *flags, "-f", "-"], input=text, check=check)

    r.step("1. Schema and load")
    r.note((HERE / "schema.sql").read_text())
    sql((HERE / "schema.sql").read_text(), check=True)
    load = load_sql()
    lines = load.strip().splitlines()
    n = sum(1 for s in lines if s.startswith("SELECT"))
    r.note(f"# load.sql, {n} cypher() statements; first node and first edge:")
    r.note(next(s for s in lines if "CREATE (:Person" in s))
    r.note(next(s for s in lines if "WorksAt" in s))
    # Each statement prints an empty result table; drop stdout, keep errors.
    sql(load, "-o", "/dev/null", check=True)
    r.run([*PSQL, "-c", "SELECT 'Person' AS label, count(*) FROM cmp.\"Person\" UNION ALL "
           "SELECT 'Company', count(*) FROM cmp.\"Company\" UNION ALL SELECT 'Project', count(*) FROM cmp.\"Project\" "
           "UNION ALL SELECT 'WorksAt', count(*) FROM cmp.\"WorksAt\" UNION ALL "
           "SELECT 'ContributesTo', count(*) FROM cmp.\"ContributesTo\";"], check=True)

    r.step("2. Two-hop query: projects Acme's employees contribute to")
    two_hop = (HERE / "two_hop.sql").read_text()
    r.note(two_hop)
    sql(two_hop, check=True)
    _, out = sql(two_hop, "-At", check=True)
    # agtype strings come back JSON-quoted: "Atlas"
    got = [line.strip().strip('"') for line in out.splitlines() if line.strip()]
    r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL", f"got {got}, expected {expected_projects()}")

    r.step("3. Schema probes: a wrong value type, then an undeclared property")
    r.note("# AGE documents no schema mode for properties, so there is no strict run.")
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        stmt = PRELUDE + cypher(f"CREATE (:Person {props(DATASET['probes'][probe])})")
        r.note(f"# {label}:")
        r.note(stmt.strip())
        code, _ = sql(stmt)
        r.result(f"rejects_{probe}", "rejected" if code != 0 else "accepted")
    people = (HERE / "people_count.sql").read_text()
    r.note(people)
    _, out = sql(people, "-At", check=True)
    r.note("# the probe rows as stored:")
    sql(PRELUDE + cypher("MATCH (p:Person) WHERE p.slug IN ['mo', 'nia'] RETURN p", "p agtype"))
    r.result("people_after_probes", int(out.split()[-1]), "12 means neither probe wrote")

    r.step("4. Branch, merge and a historical read")
    detail = ("profile: branching = no (None in AGE or PostgreSQL docs); time_travel = partial "
              "(No versioned reads; PostgreSQL point-in-time recovery restores to a timestamp)")
    r.note("# Not run: AGE has no branches or versioned reads. Not emulated.")
    for key in ("branch_isolation", "merge", "historical_read"):
        r.result(key, "not available", detail)

    r.save()
    print(f"apache-age: {r.results}")


if __name__ == "__main__":
    main()
