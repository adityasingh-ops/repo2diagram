"""MCP server: lets Claude Code, Claude Desktop, Cursor & co. diagram and explore repos.

By default the tools return structure only and let the host model do the explaining,
so no API key or local model is needed inside the MCP client.
"""

from __future__ import annotations

import json
from pathlib import Path

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from repo2diagram.pipeline import build_graph
from repo2diagram.render import to_html, to_markdown

mcp = MCPServer("repo2diagram")


def _build(path: str, diff_base: str | None = None, summarize: bool = False, include_tests: bool = False):
    return build_graph(path, summarize_with_llm=summarize, include_tests=include_tests, diff_base=diff_base or None)


@mcp.tool()
def diagram_repo(
    path: str,
    diff_base: str | None = None,
    summarize: bool = False,
    include_tests: bool = False,
    save_html: bool = True,
) -> str:
    """Draw the architecture of a codebase as a Mermaid diagram plus a module table.

    Args:
        path: Absolute path to a local repository, a git URL, or `owner/repo` on GitHub.
        diff_base: Optional git ref (e.g. "main"). Highlights modules changed since that ref and
            the modules that depend on them (blast radius).
        summarize: Ask the configured LLM (Ollama / Claude / OpenAI-compatible) to label modules.
            Leave False to label them yourself from `module_details`.
        include_tests: Include test directories as modules.
        save_html: Also write an interactive HTML diagram next to the repo and return its path.
    """
    graph, report = _build(path, diff_base, summarize, include_tests)
    md = to_markdown(graph, report)
    if save_html and Path(graph.root).is_dir():
        out = Path(graph.root) / f"{graph.name}-architecture.html"
        try:
            out.write_text(to_html(graph, report))
            md += f"\nInteractive diagram saved to: {out}\n"
        except OSError:
            pass
    return md


@mcp.tool()
def repo_structure(path: str, include_tests: bool = False) -> str:
    """Return the module graph as JSON: modules (path, files, languages, LOC, entrypoints, heuristic role),
    import edges between modules (with weights) and third-party dependencies. Use it to reason about
    how a codebase is organised before reading files.

    Args:
        path: Absolute path to a local repository, a git URL, or `owner/repo` on GitHub.
        include_tests: Include test directories as modules.
    """
    graph, _ = _build(path, include_tests=include_tests)
    data = graph.to_dict()
    for m in data["modules"]:
        n = len(m["files"])
        m["files"] = m["files"][:40] + ([f"… {n - 40} more"] if n > 40 else [])
    return json.dumps(data, indent=2)


@mcp.tool()
def module_details(path: str, module: str, max_lines_per_file: int = 60) -> str:
    """Show one module in depth: its files, what it imports, what imports it, and the opening lines of
    its most important files.

    Args:
        path: Absolute path to the repository.
        module: Module path or name as shown by `repo_structure` / `diagram_repo` (e.g. "src/api").
        max_lines_per_file: How many lines to show from each key file.
    """
    graph, _ = _build(path, include_tests=True)
    mod = graph.find(module)
    if mod is None:
        return f"No module {module!r}. Available: " + ", ".join(m.path or "(root)" for m in graph.modules)
    names = {m.id: m.path or "(root)" for m in graph.modules}
    uses = [names[e.target] for e in graph.edges if e.source == mod.id]
    used_by = [names[e.source] for e in graph.edges if e.target == mod.id]
    root = Path(graph.root)
    key = mod.entrypoints + [f for f in mod.files if f not in mod.entrypoints]
    excerpts = []
    for f in key[:5]:
        try:
            text = "\n".join((root / f).read_text(errors="replace").splitlines()[:max_lines_per_file])
        except OSError:
            continue
        excerpts.append(f"### {f}\n```\n{text}\n```")
    return "\n".join([
        f"## {mod.path or '(root)'}  — {mod.role or mod.main_language}",
        f"{len(mod.files)} files · {mod.loc} LOC · languages: {mod.languages}",
        f"Uses: {', '.join(uses) or 'nothing'}",
        f"Used by: {', '.join(used_by) or 'nothing'}",
        "Files:\n" + "\n".join(f"- {f}" for f in mod.files[:80]),
        *excerpts,
    ])


@mcp.tool()
def diff_impact(path: str, base: str = "main") -> str:
    """Explain the architectural blast radius of the current changes: which modules a branch/PR touches
    and which other modules depend on them (and so might break). Great before reviewing or merging.

    Args:
        path: Absolute path to a local git repository.
        base: Git ref to compare against (default "main").
    """
    graph, report = _build(path, diff_base=base)
    lines = [f"Diff vs {base}: {report['changed_files']} files changed."]
    for m in graph.modules:
        if m.changed:
            lines.append(f"- CHANGED {m.path or '(root)'}: {', '.join(m.changed_files[:10])}")
    impacted = [m.path for m in graph.modules if m.impacted]
    lines.append(f"Blast radius (depend on changed code): {', '.join(impacted) or 'none'}")
    if report["unmapped_files"]:
        lines.append(f"Other files (tests/docs/config): {', '.join(report['unmapped_files'][:20])}")
    lines.append("")
    lines.append(to_markdown(graph, report))
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
