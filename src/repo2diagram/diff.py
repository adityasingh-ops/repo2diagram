"""Map a git diff onto the module graph: what changed, and what's in the blast radius."""

from __future__ import annotations

import subprocess
from collections import defaultdict, deque
from pathlib import Path

from repo2diagram.model import RepoGraph


def changed_files(root: Path, base: str) -> list[str]:
    """Files changed between ``base`` and the working tree (committed, staged, unstaged and untracked)."""
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
        return [line for line in out.stdout.splitlines() if line]

    merge_base = git("merge-base", base, "HEAD")[0]
    files = set(git("diff", "--name-only", merge_base))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files)


def apply_diff(graph: RepoGraph, files: list[str], base: str) -> dict:
    """Mark changed/impacted modules on the graph and return a short report."""
    graph.diff_base = base
    owner = {f: m for m in graph.modules for f in m.files}
    for f in files:
        mod = owner.get(f)
        if mod:
            mod.changed = True
            mod.changed_files.append(f)

    # Anything that (transitively) imports a changed module may be affected.
    dependents = defaultdict(set)
    for e in graph.edges:
        dependents[e.target].add(e.source)
    queue = deque(m.id for m in graph.modules if m.changed)
    seen = set(queue)
    while queue:
        cur = queue.popleft()
        for dep in dependents[cur]:
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
                mod = graph.module(dep)
                if mod and not mod.changed:
                    mod.impacted = True

    return {
        "base": base,
        "changed_files": len(files),
        "changed_modules": [m.name for m in graph.modules if m.changed],
        "impacted_modules": [m.name for m in graph.modules if m.impacted],
        "unmapped_files": [f for f in files if f not in owner],
    }
