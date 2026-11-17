# Graph database comparison

The same small dataset and the same four steps on 12 open-source graph databases, each in a fresh container at a pinned version. Every command and its real output is saved in `output/`.

This is the test behind [Best open source graph databases](https://www.omnigraph.dev/guide/open-source-graph-databases) on omnigraph.dev. It's written by the team behind Omnigraph, one of the engines compared, which is why everything here is a script you can rerun rather than a claim.

## The steps

**Dataset** (`dataset.json`): 12 people (name, age), 3 companies, 5 projects, `WorksAt` and `ContributesTo` edges, and three probe records.

1. **Schema and load.** The schema each engine's docs recommend for typed data, then the full dataset.
2. **Two-hop query.** Which projects do Acme's employees contribute to? Checked against the answer computed from the dataset (`Atlas, Beacon, Comet`).
3. **Schema probes.** A person with `age: "forty"`, then one with an undeclared `nickname`, through the engine's normal write path. Rejected or accepted, in the default setup and again in the engine's strict mode when it documents one.
4. **Branch, merge and a historical read.** Branch, write on it, check main is untouched, merge, then read the commit from before. Run only where the engine documents branches or versioned reads; elsewhere recorded as `not available`.

## Results (run 2026-11-11)

| Engine (version run) | Two-hop query | `age: "forty"` | undeclared `nickname` | Strict mode | Branch, merge, older commit |
|---|---|---|---|---|---|
| Omnigraph v0.15.0 | pass | rejected | rejected | not needed | pass, pass, pass |
| TerminusDB v12.0.9 | pass | rejected | rejected | not needed | pass, pass, pass |
| TypeDB CE 3.14.2 | pass | rejected | rejected | not needed | not available |
| LadybugDB 0.22.3 | pass | rejected | rejected | not needed | not available |
| NebulaGraph 3.8.0 | pass | rejected | rejected | not needed | not available |
| Dgraph v25.5.1 | pass | rejected | accepted | rejects both | not available |
| JanusGraph 1.2.0 | pass | rejected | accepted | rejects both | not available |
| OrientDB 3.2.58 | pass | rejected | accepted | rejects both | not available |
| ArcadeDB 26.11.1 | pass | rejected | accepted | none documented | not available |
| Neo4j CE 2026.10.2 | pass | accepted | accepted | refused in Community Edition | not available |
| Apache AGE 1.8.0 (PostgreSQL 18) | pass | accepted | accepted | none documented | not available |
| HelixDB (server image v0.0.15) | pass | accepted | accepted | none documented | not available |

Full logs: `output/<engine>.txt`. Machine-readable: `output/results.json`.

## Run it

Requirements: Python 3.11+, Docker, [`uv`](https://docs.astral.sh/uv/) (for LadybugDB), and the [`omnigraph` CLI](https://github.com/ModernRelay/omnigraph) (`OMNIGRAPH_BIN` overrides its path).

```bash
python run_all.py              # every engine
python run_all.py neo4j        # one engine
```

Each runner starts its own containers (named `cmp-<engine>`) and removes them when it's done. Images and package versions are pinned at the top of each `<engine>/run.py` and named in the first line of its log. Logs replace machine paths with `$WORK` (a temp dir), `$CODE` (this repo) and `~`.

## Notes on method

- **TerminusDB:** `works_at` is optional in the schema, since the probe records have no company; the merge uses `terminusdb apply` with the branch's before and after commits.
- **NebulaGraph:** the console exits 0 on errors, so the runner reads its output for `[ERROR`.
- **HelixDB:** v3 has no schema file or declared types, so step 1 only loads data. The query runs through the native DSL and is cross-checked through `/v2/cypher`.
- **OrientDB:** the console takes no parameters, so `'acme'` is inlined.
- **JanusGraph:** BerkeleyDB JE with Lucene, the single-container setup. Strict mode is a second container with `schema.default=none` and `schema.constraints=true`.
- **Dgraph:** strict mode is a second container started with `--limit mutations=strict`.

Found something wrong, or a stricter setting we missed? Open an issue or a PR: the page is updated from this repo.

## License

MIT
