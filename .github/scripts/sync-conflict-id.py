"""Identify an unresolved merge without including unrelated ref movement."""
import hashlib
from pathlib import Path
import subprocess


def git(*args):
    return subprocess.check_output(("git", *args))


rerere = {}
rr_path = Path(git("rev-parse", "--git-path", "MERGE_RR").decode().strip())
if rr_path.exists():
    for entry in filter(None, rr_path.read_bytes().split(b"\0")):
        key, path = entry.split(b"\t", 1)
        rerere[path] = key.split(b".", 1)[0]

stages = {}
for entry in filter(None, git("ls-files", "--unmerged", "-z").split(b"\0")):
    stage, path = entry.split(b"\t", 1)
    stages.setdefault(path, []).append(stage)
if not stages:
    raise SystemExit("Expected unmerged index entries")

fingerprint = hashlib.sha256()
for path, entries in sorted(stages.items()):
    # Git's normalized conflict ID ignores line positions and nonconflicting edits.
    # For binary/rename conflicts with no rerere record, use the stage records.
    fingerprint.update(path + b"\0" + rerere.get(path, b"\t".join(sorted(entries))) + b"\0")
print(fingerprint.hexdigest())
