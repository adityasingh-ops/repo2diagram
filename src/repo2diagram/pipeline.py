"""High-level entry point shared by the CLI and the MCP server."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from repo2diagram import config as cfgmod
from repo2diagram.diff import apply_diff, changed_files
from repo2diagram.llm import make_client
from repo2diagram.model import RepoGraph
from repo2diagram.scanner import scan
from repo2diagram.summarize import summarize

REMOTE = re.compile(r"^(https?://|git@)|^[\w.-]+/[\w.-]+$")


def resolve_source(source: str) -> tuple[Path, Path | None]:
    """Return (local path, temp dir to clean up). Accepts a path, a git URL or `owner/repo`."""
    p = Path(source).expanduser()
    if p.exists():
        return p, None
    if not REMOTE.match(source):
        raise FileNotFoundError(f"{source} is not a directory or a git URL")
    url = source if "://" in source or source.startswith("git@") else f"https://github.com/{source}"
    tmp = Path(tempfile.mkdtemp(prefix="repo2diagram-"))
    dest = tmp / url.rstrip("/").split("/")[-1].removesuffix(".git")
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)], check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if "not found" in e.stderr.lower():
            raise RuntimeError(
                f"couldn't find the repository {source!r}. Use a real GitHub name like "
                "`BurntSushi/ripgrep`, a git URL, or a local folder such as `.` "
                "(private repos need git access to be set up)."
            ) from e
        raise RuntimeError(f"git clone failed: {e.stderr.strip()}") from e
    return dest, tmp


def build_graph(
    source: str | Path = ".",
    *,
    summarize_with_llm: bool = True,
    include_tests: bool = False,
    diff_base: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    workers: int = 4,
    on_progress=None,
) -> tuple[RepoGraph, dict | None]:
    """Scan ``source`` and return the graph plus an optional diff report."""
    path, tmp = resolve_source(str(source))
    try:
        graph = scan(path, include_tests=include_tests)
        report = None
        if diff_base:
            report = apply_diff(graph, changed_files(path, diff_base), diff_base)
        if summarize_with_llm:
            cfg = cfgmod.resolve(provider=provider, model=model)
            client = make_client(cfg)
            if client is not None:
                if client.name == "ollama":
                    workers = min(workers, 2)  # local models don't love parallel load
                summarize(graph, client, workers=workers, on_progress=on_progress)
        if tmp is not None:
            graph.root = str(source)
        return graph, report
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
