"""JanusGraph: load, two-hop query, schema probes, branch and snapshot check.

    python compare/janusgraph/run.py   # needs Docker; starts and removes container cmp-janusgraph

The image's Gremlin Server runs with its bundled BerkeleyDB JE + Lucene config. The runner switches the
server to WsAndHttpChannelizer (WebSocket plus HTTP) and posts Gremlin scripts with curl inside the
container (docker exec), so no host ports are used. Each HTTP request is its own transaction, committed
when the script succeeds and rolled back when it fails.

The strict run uses a second, fresh container with schema.default=none and schema.constraints=true.
Both settings are GLOBAL_OFFLINE, so they have to be in place when the graph is first created.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATASET, Report, expected_projects  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE = "janusgraph/janusgraph:1.2.0"
NAME = "cmp-janusgraph"
HTTP = "gremlinserver.channelizer=org.apache.tinkerpop.gremlin.server.channel.WsAndHttpChannelizer"
STRICT = ["janusgraph.schema.default=none", "janusgraph.schema.constraints=true"]
CURL = ["docker", "exec", "-i", NAME, "curl", "-s", "--fail-with-body", "-X", "POST", "-d", "@-", "http://localhost:8182"]


def gremlin(r, script, bindings=None, check=False):
    """POST one script; the JSON body goes on stdin, the script itself is noted above it."""
    r.note(script.strip())
    body = json.dumps({"gremlin": script, **({"bindings": bindings} if bindings else {})})
    code, out = r.run(CURL, input=body, check=check)
    return code, out


def values(out):
    """Plain values from a GraphSON v3 response (strings, or typed numbers like g:Int64)."""
    data = json.loads(out)["result"]["data"]["@value"]
    return [v["@value"] if isinstance(v, dict) else v for v in data]


def g_lit(v):
    return json.dumps(v)  # double-quoted strings and plain ints are valid Groovy literals


def load_script():
    lines = []
    for p in DATASET["people"]:
        lines.append(f"g.addV('Person').property('slug', {g_lit(p['slug'])}).property('name', {g_lit(p['name'])})"
                     f".property('age', {p['age']}).iterate()")
    for label, key in (("Company", "companies"), ("Project", "projects")):
        for c in DATASET[key]:
            lines.append(f"g.addV('{label}').property('slug', {g_lit(c['slug'])}).property('name', {g_lit(c['name'])}).iterate()")
    for edge, to, key in (("WorksAt", "Company", "works_at"), ("ContributesTo", "Project", "contributes_to")):
        for a, b in DATASET[key]:
            lines.append(f"g.V().has('Person', 'slug', {g_lit(a)}).as('a').V().has('{to}', 'slug', {g_lit(b)})"
                         f".addE('{edge}').from('a').iterate()")
    lines.append("'loaded'")
    return "\n".join(lines) + "\n"


def start(r, env):
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    flags = [x for e in [HTTP, *env] for x in ("-e", e)]
    r.run(["docker", "run", "-d", "--name", NAME, *flags, IMAGE], check=True)
    for _ in range(120):
        p = subprocess.run(CURL, input='{"gremlin": "1"}', capture_output=True, text=True)
        if p.returncode == 0:
            return
        time.sleep(2)
    raise SystemExit("janusgraph: server did not become ready")


def schema_and_load(r):
    gremlin(r, "org.janusgraph.graphdb.configuration.JanusGraphConstants.VERSION", check=True)
    gremlin(r, "mgmt = graph.openManagement(); s = [mgmt.get('schema.default'), mgmt.get('schema.constraints')].toString(); "
               "mgmt.rollback(); 'schema.default, schema.constraints = ' + s", check=True)
    gremlin(r, (HERE / "schema.groovy").read_text(), check=True)
    gremlin(r, load_script(), check=True)
    gremlin(r, "g.V().groupCount().by(label).next().toString()", check=True)
    gremlin(r, "g.E().groupCount().by(label).next().toString()", check=True)


def probes(r, suffix):
    for probe, label in (("wrong_type", "age is a string"), ("undeclared_property", "nickname isn't in the schema")):
        r.note(f"# {label}")
        steps = "".join(f".property('{k}', {g_lit(v)})" for k, v in DATASET["probes"][probe].items())
        code, _ = gremlin(r, f"g.addV('Person'){steps}.iterate(); 'written'")
        r.result(f"rejects_{probe}{suffix}", "rejected" if code != 0 else "accepted")
    _, out = gremlin(r, (HERE / "people_count.groovy").read_text(), check=True)
    return values(out)[0]


def main():
    r = Report("janusgraph", f"1.2.0 (docker image {IMAGE}, BerkeleyDB JE + Lucene)")
    try:
        r.step("1. Schema and load")
        r.note("# Default setup: the image's stock graph config (schema.default=default, schema.constraints=false).")
        start(r, [])
        schema_and_load(r)

        r.step("2. Two-hop query: projects Acme's employees contribute to")
        r.note('# bindings: {"company": "acme"}')
        _, out = gremlin(r, (HERE / "company_projects.groovy").read_text(), {"company": "acme"}, check=True)
        got = values(out)
        r.result("two_hop_query", "pass" if got == expected_projects() else "FAIL",
                 f"got {got}, expected {expected_projects()}")

        r.step("3. Schema probes: a wrong value type, then an undeclared property")
        r.note("# Default setup: explicit schema from step 1, stock config (undeclared keys are created on first use).")
        n = probes(r, "")
        r.result("people_after_probes", n, "12 means neither probe wrote")

        r.note("")
        r.note("# Stricter mode: a fresh container with schema.default=none and schema.constraints=true,")
        r.note("# the same schema and data from step 1, then the same probes.")
        start(r, STRICT)
        schema_and_load(r)
        n = probes(r, "_strict")
        r.result("people_after_probes_strict", n, "12 means neither probe wrote")

        r.step("4. Branch, merge and a historical read")
        r.note("# Not run: the JanusGraph profile rates branching and time travel as no, and branches are not emulated.")
        r.result("branch_isolation", "not available", "profile: branching = no (none found)")
        r.result("merge", "not available", "profile: branching = no")
        r.result("historical_read", "not available",
                 "profile: time_travel = no (no versioned reads in the docs; backups are left to the storage backend)")
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        r.save()
    print(f"janusgraph: {json.dumps(r.results)}")


if __name__ == "__main__":
    main()
