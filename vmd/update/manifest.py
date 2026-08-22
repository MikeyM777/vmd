"""What is on the stick, and whether all of it arrived.

There is no signature on an update and there is not going to be one: the stick
is carried by hand to a machine in a locked room, and a signing key on a
borrowed laptop is a worse risk than the one it removes. What this replaces is
something else entirely - a stick written to while somebody pulled it out, a
file that did not fit, a folder copied by Explorer that stopped at 94%. Every
one of those produces a tree that looks complete.

So the manifest is a list of every file with its SHA-256, and nothing on the
machine is touched until every one of them has been read back and matched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build(folder: Path | str) -> dict:
    """A manifest for every file under `folder`, deepest path and all."""
    folder = Path(folder)
    entries = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        entries.append(
            {
                # Forward slashes, because this is compared as text on both
                # sides and the format is not Windows'.
                "path": path.relative_to(folder).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return {"files": entries}


def write(folder: Path | str, target: Path | str) -> dict:
    """Build a manifest for `folder` and write it to `target`."""
    manifest = build(folder)
    Path(target).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def verify(folder: Path | str, manifest: dict) -> list[str]:
    """Every disagreement between `folder` and `manifest`, in sentences.

    Sentences and not booleans: what this returns is shown to somebody standing
    at the machine, and "3 files do not match" with no names has told them
    nothing they can act on. An empty list means the tree is exactly what the
    manifest says it is.
    """
    folder = Path(folder)
    problems: list[str] = []
    listed = set()

    for entry in manifest.get("files", []):
        name = entry["path"]
        listed.add(name)
        path = folder / name
        if not path.is_file():
            problems.append(f"{name} is missing from the stick")
            continue
        if path.stat().st_size != entry["size"]:
            problems.append(f"{name} is the wrong size")
            continue
        if sha256(path) != entry["sha256"]:
            problems.append(f"{name} does not match what the stick says it should be")

    for path in sorted(folder.rglob("*")):
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if name not in listed:
                problems.append(f"{name} is on the stick but not in its manifest")

    return problems
