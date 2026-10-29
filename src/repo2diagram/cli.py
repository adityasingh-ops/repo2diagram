"""Command-line interface.

  repo2diagram [SOURCE] [options]        generate a diagram (default command)
  repo2diagram config show|set|unset|path
  repo2diagram doctor                    check which LLM providers are usable
  repo2diagram mcp                       run the MCP server (stdio)
  repo2diagram setup claude-code|claude-desktop|cursor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from repo2diagram import __version__
from repo2diagram import config as cfgmod
from repo2diagram.llm import LLMError, make_client, ollama_models, ollama_url
from repo2diagram.pipeline import build_graph
from repo2diagram.render import FORMATS, render

SUBCOMMANDS = {"config", "doctor", "mcp", "setup", "generate"}


def _err(msg: str) -> None:
    print(f"repo2diagram: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- generate

def cmd_generate(args: argparse.Namespace) -> int:
    fmt = args.format
    if args.output and not fmt:
        fmt = next((f for f, ext in FORMATS.items() if args.output.endswith(ext)), "html")
    fmt = fmt or "html"

    done = [0]

    def progress(mod) -> None:
        done[0] += 1
        if not args.quiet:
            print(f"  ✓ {mod.name:<28} {mod.role}", file=sys.stderr)

    if args.watch:
        return _watch(args)

    t0 = time.time()
    if not args.quiet:
        print(f"Scanning {args.source} …", file=sys.stderr)
    try:
        graph, report = build_graph(
            args.source,
            summarize_with_llm=not args.no_llm,
            include_tests=args.tests,
            diff_base=args.diff,
            provider=args.provider,
            model=args.model,
            workers=args.workers,
            on_progress=progress,
        )
    except (FileNotFoundError, RuntimeError, LLMError) as e:
        _err(str(e))
        return 1

    if not graph.modules:
        _err("found no source modules to draw")
        return 1

    if graph.llm_errors:
        _err(f"warning: {graph.llm_errors} LLM call(s) failed, those labels fall back to static analysis "
             f"({graph.llm_error}). Check `repo2diagram doctor`.")
    out = render(graph, fmt, report, args.direction)
    if args.output == "-" or (fmt != "html" and not args.output):
        sys.stdout.write(out)
        return 0
    dest = Path(args.output or f"{graph.name}-architecture{FORMATS[fmt]}")
    dest.write_text(out, encoding="utf-8")
    if not args.quiet:
        label = graph.provider if graph.provider != "static" else "static (no LLM — try Ollama or `config set`)"
        print(f"\n{len(graph.modules)} modules · {len(graph.edges)} links · labels: {label} · "
              f"{time.time() - t0:.1f}s", file=sys.stderr)
        if report:
            print(f"diff vs {report['base']}: changed {', '.join(report['changed_modules']) or '—'}; "
                  f"blast radius {', '.join(report['impacted_modules']) or '—'}", file=sys.stderr)
        print(f"→ {dest.resolve()}", file=sys.stderr)
    if args.open and fmt == "html":
        webbrowser.open(dest.resolve().as_uri())
    return 0


def _watch(args: argparse.Namespace) -> int:
    from repo2diagram.watch import serve

    root = Path(args.source).expanduser()
    if not root.is_dir():
        _err("--watch needs a local directory")
        return 1

    def build() -> str:
        graph, report = build_graph(root, summarize_with_llm=not args.no_llm, include_tests=args.tests,
                                    diff_base=args.diff, provider=args.provider, model=args.model,
                                    workers=args.workers)
        return render(graph, "html", report, args.direction)

    try:
        serve(root.resolve(), build, port=args.port, open_browser=not args.no_open,
              log=lambda m: print(m, file=sys.stderr))
    except OSError as e:
        _err(f"could not start the server on port {args.port}: {e}")
        return 1
    return 0


# --------------------------------------------------------------------------- config

def cmd_config(args: argparse.Namespace) -> int:
    try:
        if args.action == "path":
            print(cfgmod.config_path())
        elif args.action == "show":
            print(json.dumps(cfgmod.redacted(cfgmod.resolve()), indent=2))
        elif args.action == "set":
            if not args.key or args.value is None:
                _err("usage: repo2diagram config set KEY VALUE")
                return 2
            path = cfgmod.set_value(args.key, args.value)
            shown = cfgmod.redacted({args.key: args.value})[args.key]
            print(f"{args.key} = {shown}  ({path})")
        elif args.action == "unset":
            if not args.key:
                _err("usage: repo2diagram config unset KEY")
                return 2
            cfgmod.unset_value(args.key)
            print(f"unset {args.key}")
    except ValueError as e:
        _err(str(e))
        return 2
    return 0


# --------------------------------------------------------------------------- doctor

def cmd_doctor(_: argparse.Namespace) -> int:
    cfg = cfgmod.resolve()
    print(f"repo2diagram {__version__}")
    print(f"config: {cfgmod.config_path()}")
    print(json.dumps(cfgmod.redacted(cfg), indent=2))
    print()
    print(f"git:        {'ok' if shutil.which('git') else 'missing (needed for --diff and remote repos)'}")
    url = ollama_url(cfg.get("base_url") if cfg.get("provider") == "ollama" else None)
    models = ollama_models(url)
    if models is None:
        print(f"ollama:     not running at {url} → install from https://ollama.com, then `ollama pull qwen2.5-coder:7b`")
    else:
        print(f"ollama:     running · models: {', '.join(models) or 'none — run `ollama pull qwen2.5-coder:7b`'}")
    print(f"anthropic:  {'key found' if os.environ.get('ANTHROPIC_API_KEY') or str(cfg.get('api_key', '')).startswith('sk-ant') else 'no key'}")
    try:
        client = make_client(cfg)
    except LLMError as e:
        print(f"\nactive:     ERROR — {e}")
        return 1
    print(f"\nactive:     {f'{client.name}:{client.model}' if client else 'static only (no LLM)'}")
    return 0


# --------------------------------------------------------------------------- mcp / setup

def cmd_mcp(_: argparse.Namespace) -> int:
    from repo2diagram.mcp_server import main as serve

    serve()
    return 0


def _server_command() -> list[str]:
    exe = shutil.which("repo2diagram")
    if exe:
        return [exe, "mcp"]
    return ["uvx", "repo2diagram", "mcp"]


def cmd_setup(args: argparse.Namespace) -> int:
    cmd = _server_command()
    entry = {"command": cmd[0], "args": cmd[1:]}
    if args.target == "claude-code":
        claude = shutil.which("claude")
        line = ["claude", "mcp", "add", "--scope", "user", "repo2diagram", "--", *cmd]
        if claude and args.yes:
            return subprocess.run(line, check=False).returncode
        print("Run this to add repo2diagram to Claude Code:\n")
        print("  " + " ".join(line))
        if claude:
            print("\n(or re-run with --yes and I'll do it for you)")
        return 0
    if args.target == "claude-desktop":
        if sys.platform == "darwin":
            path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
        elif sys.platform == "win32":
            path = Path(os.environ.get("APPDATA", "")) / "Claude/claude_desktop_config.json"
        else:
            path = Path.home() / ".config/Claude/claude_desktop_config.json"
    else:  # cursor
        path = Path.home() / ".cursor/mcp.json"

    snippet = {"mcpServers": {"repo2diagram": entry}}
    if not args.yes:
        print(f"Add this to {path}:\n")
        print(json.dumps(snippet, indent=2))
        print("\n(or re-run with --yes to merge it in automatically; a .bak copy is kept)")
        return 0
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            _err(f"{path} is not valid JSON; not touching it")
            return 1
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    data.setdefault("mcpServers", {})["repo2diagram"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Added repo2diagram to {path}. Restart the app to load it.")
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    gen = argparse.ArgumentParser(
        prog="repo2diagram",
        description="Turn any codebase into an interactive architecture diagram.",
        epilog="Other commands: config, doctor, mcp, setup. Run `repo2diagram <command> -h` for help.",
    )
    gen.add_argument("source", nargs="?", default=".", help="local path, git URL, or owner/repo (default: .)")
    gen.add_argument("-o", "--output", help="output file ('-' for stdout); format inferred from extension")
    gen.add_argument("-f", "--format", choices=list(FORMATS), help="html (default), md, mermaid, json, pr (compact PR comment, use with --diff)")
    gen.add_argument("--diff", metavar="REF", nargs="?", const="main",
                     help="highlight modules changed since REF (default: main) and their blast radius")
    gen.add_argument("--no-llm", action="store_true", help="static analysis only, no model calls")
    gen.add_argument("--provider", choices=list(cfgmod.PROVIDERS), help="override configured provider")
    gen.add_argument("--model", help="override configured model")
    gen.add_argument("--all", "--tests", dest="tests", action="store_true",
                     help="also draw tests, docs, examples and CI directories")
    gen.add_argument("--direction", choices=["LR", "TD"], default="LR", help="diagram direction")
    gen.add_argument("--workers", type=int, default=4, help="parallel LLM requests")
    gen.add_argument("--open", action="store_true", help="open the HTML in your browser")
    gen.add_argument("-w", "--watch", action="store_true",
                     help="serve a live diagram on localhost that redraws as you edit code")
    gen.add_argument("--port", type=int, default=8484, help="port for --watch (default 8484)")
    gen.add_argument("--no-open", action="store_true", help="with --watch, don't open a browser")
    gen.add_argument("-q", "--quiet", action="store_true")
    gen.add_argument("-V", "--version", action="version", version=f"repo2diagram {__version__}")

    top = argparse.ArgumentParser(prog="repo2diagram")
    sub = top.add_subparsers(dest="command", required=True)
    c = sub.add_parser("config", help="show or change settings (provider, model, base_url, api_key)")
    c.add_argument("action", choices=["show", "set", "unset", "path"])
    c.add_argument("key", nargs="?", choices=list(cfgmod.KEYS))
    c.add_argument("value", nargs="?")
    sub.add_parser("doctor", help="check which LLM providers are available")
    sub.add_parser("mcp", help="run the MCP server over stdio")
    s = sub.add_parser("setup", help="register the MCP server with an app")
    s.add_argument("target", choices=["claude-code", "claude-desktop", "cursor"])
    s.add_argument("--yes", action="store_true", help="apply the change instead of printing it")
    return gen, top


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy codepage
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    gen, top = build_parser()
    if argv and argv[0] in SUBCOMMANDS and argv[0] != "generate":
        args = top.parse_args(argv)
        return {"config": cmd_config, "doctor": cmd_doctor, "mcp": cmd_mcp, "setup": cmd_setup}[args.command](args)
    if argv and argv[0] == "generate":
        argv = argv[1:]
    return cmd_generate(gen.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
