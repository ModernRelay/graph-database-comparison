"""Run every engine's comparison and collect the results into output/results.json.

    python compare/run_all.py [engine ...]     # default: every engine folder

Each runner writes output/<engine>.txt (the log the page quotes) and
output/<engine>.json (its results). This collects the .json files, so a
failed runner shows as missing rather than stopping the others.
"""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = [
    "two_hop_query",
    "rejects_wrong_type", "rejects_undeclared_property",
    "rejects_wrong_type_strict", "rejects_undeclared_property_strict",
    "branch_isolation", "merge", "historical_read",
]


def main():
    engines = sys.argv[1:] or sorted(p.parent.name for p in HERE.glob("*/run.py"))
    failed = []
    for e in engines:
        print(f"== {e}", flush=True)
        p = subprocess.run([sys.executable, "-I", str(HERE / e / "run.py")], env=os.environ)
        if p.returncode != 0:
            failed.append(e)

    results = {}
    for e in sorted(p.parent.name for p in HERE.glob("*/run.py")):
        f = HERE / "output" / f"{e}.json"
        log = HERE / "output" / f"{e}.txt"
        if f.exists():
            title = log.read_text().splitlines()[0].removeprefix("# ") if log.exists() else e
            run = log.read_text().splitlines()[1].removeprefix("# run ") if log.exists() else ""
            results[e] = {"version": title, "run": run, "results": json.loads(f.read_text())}
    (HERE / "output" / "results.json").write_text(
        json.dumps({"collected": date.today().isoformat(), "engines": results}, indent=2) + "\n")

    width = max(len(e) for e in results) if results else 10
    print("\n" + "engine".ljust(width), *(k[:14].ljust(14) for k in KEYS))
    for e, r in results.items():
        row = [str(r["results"].get(k, {}).get("value", "")) for k in KEYS]
        print(e.ljust(width), *(v[:14].ljust(14) for v in row))
    if failed:
        print("\nfailed:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
