"""Shared helpers: the dataset, the expected answer, and the output format every runner writes."""

import json
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = json.loads((HERE / "dataset.json").read_text())


def expected_projects(company="acme"):
    staff = {p for p, c in DATASET["works_at"] if c == company}
    names = {p["slug"]: p["name"] for p in DATASET["projects"]}
    return sorted({names[j] for p, j in DATASET["contributes_to"] if p in staff})


class Report:
    """Collects one engine's run into output/<engine>.txt: every command, its real output, and a verdict per step."""

    def __init__(self, engine, version):
        self.engine, self.lines, self.results = engine, [], {}
        self.lines += [f"# {engine} {version}", f"# run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", ""]

    def step(self, title):
        self.lines += ["", f"## {title}", ""]

    def run(self, cmd, cwd=None, input=None, check=False):
        shown = cmd if isinstance(cmd, str) else shlex.join(cmd)
        p = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, text=True, shell=isinstance(cmd, str))
        out = (p.stdout + p.stderr).rstrip()
        self.lines += [f"$ {shown}", out, f"[exit {p.returncode}]", ""]
        if check and p.returncode != 0:
            self.save()
            raise SystemExit(f"{self.engine}: command failed: {shown}\n{out}")
        # The log keeps both streams; callers parse stdout alone.
        return p.returncode, p.stdout

    def note(self, text):
        self.lines.append(text)

    def result(self, key, value, detail=""):
        self.results[key] = {"value": value, "detail": detail}
        self.lines.append(f"=> {key}: {value}" + (f" ({detail})" if detail else ""))

    def save(self):
        out = HERE / "output"
        out.mkdir(exist_ok=True)
        text = "\n".join(self.lines) + "\n"
        # Logs get quoted on the page: replace machine paths with placeholders.
        text = re.sub(r"\S*/(?:[\w.-]+-compare-|tmp)[\w.-]*", "$WORK", text)
        text = text.replace(str(HERE.parent), "$CODE").replace(str(Path.home()), "~")
        (out / f"{self.engine}.txt").write_text(text)
        (out / f"{self.engine}.json").write_text(json.dumps(self.results, indent=2) + "\n")
