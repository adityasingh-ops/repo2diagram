"""Walk a repository, group files into modules and extract the import graph.

Everything here is static analysis — no LLM needed.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import pathspec

from repo2diagram.model import Edge, Module, RepoGraph

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C",
    ".hpp": "C++", ".swift": "Swift", ".dart": "Dart", ".scala": "Scala", ".vue": "Vue", ".svelte": "Svelte",
    ".sh": "Shell", ".sql": "SQL", ".tf": "Terraform", ".proto": "Protobuf", ".lua": "Lua", ".ex": "Elixir",
}
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", ".next", ".nuxt",
    "target", "vendor", ".idea", ".vscode", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".gradle", "Pods", ".dart_tool", "site-packages", ".turbo", ".cache", "out",
}
# Directories that are usually just containers; we look inside them for the real modules.
CONTAINER_DIRS = {"src", "apps", "packages", "services", "internal", "pkg", "cmd", "modules",
                  "libs", "crates", "source"}
TEST_MARKERS = re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing|e2e)(/|$)|(_test\.go|\.test\.|\.spec\.|^test_)")
MAX_FILE_BYTES = 1_000_000
MAX_MODULES = 40
TARGET_MODULES = 18
TOOLING_FILE = re.compile(r"(\.config\.|^(setup|conftest|noxfile|fabfile|tasks|manage|next-env\.d|vite-env\.d|"
                          r"instrumentation(-client)?|middleware|env\.d)\.)", re.IGNORECASE)
AUX_DIRS = re.compile(r"(^|/)(docs?|docs_src|documentation|examples?|samples|demos|benchmarks?|\.github|\.circleci|"
                      r"fixtures|testdata|mocks?|stories|storybook)(/|$)")

ROLE_HINTS = [
    (r"(^|/)(api|routes?|routers?|controllers?|handlers?|endpoints?|views)$", "API layer"),
    (r"(^|/)(models?|schemas?|entities|types|dto|domain)$", "Data models"),
    (r"(^|/)(db|database|migrations?|repositor(y|ies)|store|storage|dao|persistence)$", "Data access"),
    (r"(^|/)(services?|core|engine|business|usecases?)$", "Core logic"),
    (r"(^|/)(utils?|helpers?|common|shared|lib|libs|tools)$", "Shared utilities"),
    (r"(^|/)(ui|components?|pages|screens|widgets|views?|layouts?|frontend|web)$", "UI"),
    (r"(^|/)(clients?|sdk)$", "Client"),
    (r"(^|/)(cli|cmd|commands?|bin)$", "CLI"),
    (r"(^|/)(config|configs|settings|conf)$", "Configuration"),
    (r"(^|/)(tests?|__tests__|specs?|e2e|testing)$", "Tests"),
    (r"(^|/)(docs?|documentation|examples?)$", "Docs & examples"),
    (r"(^|/)(scripts?|tasks)$", "Scripts"),
    (r"(^|/)(infra|deploy|deployment|terraform|k8s|kubernetes|helm|charts|docker|\.github)$", "Infrastructure"),
    (r"(^|/)(auth|security|permissions?)$", "Auth"),
    (r"(^|/)(agents?|llm|prompts?|ai|ml|models?_?zoo|inference|pipelines?)$", "AI / ML"),
    (r"(^|/)(plugins?|extensions?|integrations?|adapters?|providers?)$", "Integrations"),
    (r"(^|/)(server|backend|gateway)$", "Server"),
    (r"(^|/)(workers?|jobs?|queues?|tasks?|scheduler)$", "Background jobs"),
    (r"(^|/)(hooks|store|state|redux|context)$", "State"),
    (r"(^|/)(assets|static|public|images|styles|css)$", "Assets"),
]


# --------------------------------------------------------------------------- files

def list_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of tracked + untracked-but-not-ignored text files."""
    files: list[str] | None = None
    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "ls-files", "-co", "--exclude-standard", "-z"],
                cwd=root, capture_output=True, check=True,
            ).stdout.decode("utf-8", "replace")
            files = [f for f in out.split("\0") if f]
        except (OSError, subprocess.CalledProcessError):
            files = None
    if files is None:
        spec = None
        gi = root / ".gitignore"
        if gi.exists():
            spec = pathspec.PathSpec.from_lines("gitwildmatch", gi.read_text(errors="replace").splitlines())
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                rel = Path(dirpath, fn).relative_to(root).as_posix()
                if spec is None or not spec.match_file(rel):
                    files.append(rel)
    result = []
    for f in files:
        parts = PurePosixPath(f).parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        p = root / f
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            continue
        result.append(f)
    return sorted(result)


def language_of(path: str) -> str:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower(), "")


def is_test_path(path: str) -> bool:
    return bool(TEST_MARKERS.search(path))


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


# --------------------------------------------------------------------------- grouping

def is_auxiliary(path: str) -> bool:
    """Tests, docs, examples, CI — useful, but noise in an architecture diagram."""
    name = path.lower()
    return bool(path) and (bool(TEST_MARKERS.search(name + "/")) or bool(AUX_DIRS.search(name)))


def group_modules(files: list[str]) -> dict[str, list[str]]:
    """Map module path -> files.

    Start from top-level directories and greedily "open up" the directory that holds
    the most code (containers like src/ and packages/ first) until the diagram has a
    useful number of boxes.
    """
    src = [f for f in files if language_of(f) and not is_auxiliary(str(PurePosixPath(f).parent))]
    src = src or [f for f in files if language_of(f)] or files
    expanded: set[str] = set()

    def module_of(f: str) -> str:
        prefix = ""
        for part in PurePosixPath(f).parts[:-1]:
            prefix = f"{prefix}/{part}" if prefix else part
            if prefix not in expanded:
                return prefix
        return prefix  # file sits directly in the root or in an expanded directory

    def subdirs(mod: str) -> Counter:
        depth = len(PurePosixPath(mod).parts)
        return Counter(PurePosixPath(f).parts[depth] for f in src
                       if f.startswith(mod + "/") and len(PurePosixPath(f).parts) > depth + 1)

    for _ in range(16):
        groups = Counter(module_of(f) for f in src)
        if len(groups) >= TARGET_MODULES:
            break
        total = sum(groups.values())
        best, best_score = None, 0.0
        for mod, n in groups.items():
            if not mod or mod in expanded or is_auxiliary(mod):
                continue
            subs = subdirs(mod)
            if not subs or len(groups) - 1 + len(subs) + 1 > MAX_MODULES:
                continue
            share = n / total
            nested = sum(subs.values()) / n  # how much of it lives in subdirectories
            if len(subs) == 1 and nested < 0.6:
                continue
            depth = len(PurePosixPath(mod).parts)
            boost = 3 if PurePosixPath(mod).name in CONTAINER_DIRS or len(subs) == 1 else 1
            score = share * nested * boost / (1 + 0.6 * (depth - 1))
            if score > best_score and (share >= 0.12 or len(subs) == 1 or len(groups) <= 3):
                best, best_score = mod, score
        if best is None:
            break
        expanded.add(best)

    out: dict[str, list[str]] = defaultdict(list)
    for f in files:
        out[module_of(f)].append(f)

    # Scripts sitting in the repo root (train.py, main.go …) are usually the important bits.
    root_code = [f for f in out.get("", []) if language_of(f) and not is_test_path(f)
                 and not TOOLING_FILE.search(f)]
    if 3 <= len(root_code) <= 25:
        for f in root_code:
            out[""].remove(f)
            out[str(PurePosixPath(f).with_suffix(""))] = [f]

    # A dominant flat directory (e.g. a single-package repo) makes a one-box diagram;
    # show its files as nodes instead.
    code = {k: [f for f in v if language_of(f) and not is_test_path(f)] for k, v in out.items()
            if not is_auxiliary(k)}
    code = {k: v for k, v in code.items() if v}
    if code and len(code) <= 4:
        big, big_files = max(code.items(), key=lambda kv: len(kv[1]))
        flat = all(str(PurePosixPath(f).parent) == (big or ".") for f in big_files)
        if flat and 4 <= len(big_files) <= MAX_MODULES and len(big_files) >= 0.5 * sum(map(len, code.values())):
            for f in big_files:
                out[big].remove(f)
                out[str(PurePosixPath(f).with_suffix(""))] = [f]
            if not out[big]:
                del out[big]
    return dict(out)


def guess_role(module_path: str, files: list[str]) -> str:
    name = module_path.lower() or "(root)"
    for pattern, role in ROLE_HINTS:
        if re.search(pattern, name):
            return role
    if all(is_test_path(f) for f in files):
        return "Tests"
    if module_path == "":
        return "Project root"
    return ""


# --------------------------------------------------------------------------- imports

def _python_index(files: list[str]) -> dict[str, str]:
    """dotted module name (and its suffixes after src-like roots) -> file."""
    index: dict[str, str] = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        parts = list(PurePosixPath(f).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        for i in range(len(parts)):
            dotted = ".".join(parts[i:])
            index.setdefault(dotted, f)
    return index


def python_imports(path: Path, rel: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, ValueError):
        return []
    pkg = list(PurePosixPath(rel).parent.parts)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join(base + ([node.module] if node.module else []))
                out.append(mod)
                out.extend(f"{mod}.{a.name}" for a in node.names)
            elif node.module:
                out.append(node.module)
                out.extend(f"{node.module}.{a.name}" for a in node.names)
    return out


JS_IMPORT = re.compile(
    r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|export\s[^'"]*?from\s*|import\s+)['"]([^'"]+)['"]"""
)
JS_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
           "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]


def _resolve_js(spec: str, rel: str, fileset: set[str], aliases: dict[str, str]) -> str | None:
    if spec.startswith("."):
        base = PurePosixPath(rel).parent.joinpath(spec)
    else:
        for alias, target in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
            if spec == alias or spec.startswith(alias + "/"):
                base = PurePosixPath((target + spec[len(alias):]).lstrip("/") or ".")
                break
        else:
            return None
    norm = os.path.normpath(str(base)).replace(os.sep, "/")
    for ext in JS_EXTS:
        if norm + ext in fileset:
            return norm + ext
    return None


JSONC_TOKENS = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/')


def loads_jsonc(text: str) -> dict:
    """json.loads for tsconfig-style JSON with comments and trailing commas."""
    text = JSONC_TOKENS.sub(lambda m: m.group(1) or "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _js_aliases(root: Path, files: list[str]) -> dict[str, dict[str, str]]:
    """Path aliases per tsconfig/jsconfig directory: {config_dir: {alias: repo-relative target}}."""
    out: dict[str, dict[str, str]] = {}
    configs = [f for f in files if PurePosixPath(f).name in ("tsconfig.json", "jsconfig.json")]
    for cfg in sorted(configs, key=lambda f: PurePosixPath(f).name != "tsconfig.json"):
        cdir = str(PurePosixPath(cfg).parent)
        cdir = "" if cdir == "." else cdir
        try:
            opts = loads_jsonc((root / cfg).read_text(errors="replace")).get("compilerOptions") or {}
        except (json.JSONDecodeError, AttributeError, OSError):
            continue
        aliases = out.setdefault(cdir, {})
        base = os.path.join(cdir, opts.get("baseUrl", "."))
        for key, targets in (opts.get("paths") or {}).items():
            if targets and key not in aliases:
                t = os.path.normpath(os.path.join(base, targets[0].rstrip("*").rstrip("/"))).replace(os.sep, "/")
                aliases[key.rstrip("*").rstrip("/")] = "" if t == "." else t
    if not out.get("", {}).get("@") and (root / "src").is_dir():
        out.setdefault("", {})["@"] = "src"
    return out


def _nearest_aliases(rel: str, alias_map: dict[str, dict[str, str]]) -> dict[str, str]:
    parent = PurePosixPath(rel).parent
    for d in [parent, *parent.parents]:
        key = "" if str(d) == "." else str(d)
        if key in alias_map:
            return alias_map[key]
    return {}


GO_IMPORT_BLOCK = re.compile(r"import\s*\(([\s\S]*?)\)")
GO_IMPORT_LINE = re.compile(r'import\s+(?:\w+\s+)?"([^"]+)"')


def go_imports(text: str) -> list[str]:
    out = GO_IMPORT_LINE.findall(text)
    for block in GO_IMPORT_BLOCK.findall(text):
        out.extend(re.findall(r'"([^"]+)"', block))
    return out


# --------------------------------------------------------------------------- dependencies

def third_party_dependencies(root: Path) -> list[str]:
    deps: list[str] = []
    pj = root / "package.json"
    if pj.exists():
        try:
            data = json.loads(pj.read_text(errors="replace"))
            deps += list((data.get("dependencies") or {}).keys())
        except json.JSONDecodeError:
            pass
    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text(errors="replace").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                deps.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0])
    pp = root / "pyproject.toml"
    if pp.exists():
        try:
            data = tomllib.loads(pp.read_text(errors="replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        reqs = list(data.get("project", {}).get("dependencies", []))
        reqs += [k for k in data.get("tool", {}).get("poetry", {}).get("dependencies", {}) if k != "python"]
        deps += [re.split(r"[<>=!~\[; ]", d.strip(), maxsplit=1)[0] for d in reqs if isinstance(d, str)]
    gm = root / "go.mod"
    if gm.exists():
        deps += re.findall(r"^\s*([\w.\-]+\.[\w.\-/]+)\s+v", gm.read_text(errors="replace"), re.MULTILINE)
    cargo = root / "Cargo.toml"
    if cargo.exists():
        m = re.search(r"^\[dependencies\]([\s\S]*?)(^\[|\Z)", cargo.read_text(errors="replace"), re.MULTILINE)
        if m:
            deps += re.findall(r"^([\w\-]+)\s*=", m.group(1), re.MULTILINE)
    seen, out = set(), []
    for d in deps:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


ENTRY_NAMES = {"main.py", "__main__.py", "app.py", "manage.py", "server.py", "wsgi.py", "asgi.py", "cli.py",
               "main.go", "main.rs", "index.ts", "index.js", "server.ts", "server.js", "app.ts", "app.js",
               "main.ts", "main.js", "main.dart", "Main.java", "Application.java", "Program.cs"}


# --------------------------------------------------------------------------- web url

def web_url(root: Path) -> str:
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
        ref = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    m = re.match(r"(?:git@|https://)(github\.com|gitlab\.com)[:/](.+?)(?:\.git)?$", remote)
    if not m:
        return ""
    host, slug = m.groups()
    sep = "/-/blob/" if host == "gitlab.com" else "/blob/"
    return f"https://{host}/{slug}{sep}{ref}"


# --------------------------------------------------------------------------- main entry

def scan(root: Path, include_tests: bool = False) -> RepoGraph:
    root = root.resolve()
    files = list_files(root)
    groups = group_modules(files)

    modules: list[Module] = []
    file_to_module: dict[str, str] = {}
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "", kv[0]))
    for path, mfiles in ordered:
        is_test = bool(path) and (is_auxiliary(path) or all(is_test_path(f) for f in mfiles))
        if is_test and not include_tests:
            continue
        code_files = [f for f in mfiles if language_of(f)]
        # docs/, assets/ … keep only if they're meaningful directories
        if not code_files and path != "" and guess_role(path, mfiles) not in (
                "Infrastructure", "Docs & examples", "Configuration"):
            continue
        mid = f"m{len(modules)}"
        langs = Counter(language_of(f) for f in code_files)
        entry = [f for f in mfiles if PurePosixPath(f).name in ENTRY_NAMES and not is_test_path(f)]
        mod = Module(
            id=mid,
            path=path,
            name=path.split("/")[-1] if path else "(root)",
            files=mfiles,
            languages=dict(langs),
            loc=sum(count_lines(root / f) for f in code_files),
            role=guess_role(path, mfiles),
            entrypoints=entry[:5],
            is_test=is_test,
        )
        modules.append(mod)
        for f in mfiles:
            file_to_module[f] = mid

    # Disambiguate duplicate display names (e.g. apps/web/utils vs packages/ui/utils).
    names = Counter(m.name for m in modules)
    for m in modules:
        if names[m.name] > 1 and m.path:
            m.name = "/".join(m.path.split("/")[-2:])

    edges = _import_edges(root, files, file_to_module)
    graph = RepoGraph(
        name=root.name,
        root=str(root),
        modules=modules,
        edges=edges,
        dependencies=third_party_dependencies(root)[:40],
        languages=dict(Counter(lang for f in files if (lang := language_of(f)))),
        web_url=web_url(root),
    )
    _drop_trivial_root(graph)
    return graph


def _drop_trivial_root(graph: RepoGraph) -> None:
    """The root module is noise when it only holds README/config files."""
    root = graph.find("")
    if root and root.path == "" and not root.languages and not any(e.source == root.id or e.target == root.id
                                                                    for e in graph.edges):
        graph.modules.remove(root)


def _import_edges(root: Path, files: list[str], file_to_module: dict[str, str]) -> list[Edge]:
    fileset = set(files)
    py_index = _python_index(files)
    alias_map = _js_aliases(root, files)
    go_mod = ""
    gm = root / "go.mod"
    if gm.exists():
        m = re.search(r"^module\s+(\S+)", gm.read_text(errors="replace"), re.MULTILINE)
        go_mod = m.group(1) if m else ""
    go_dirs: dict[str, str] = {}
    for f in files:
        if f.endswith(".go"):
            go_dirs.setdefault(str(PurePosixPath(f).parent), f)

    weights: Counter[tuple[str, str]] = Counter()
    for f in files:
        src_mod = file_to_module.get(f)
        if src_mod is None:
            continue
        lang = language_of(f)
        targets: list[str] = []
        p = root / f
        if lang == "Python":
            for name in python_imports(p, f):
                hit = py_index.get(name)
                if hit:
                    targets.append(hit)
        elif lang in ("JavaScript", "TypeScript", "Vue", "Svelte"):
            text = p.read_text(errors="replace")
            for spec in JS_IMPORT.findall(text):
                hit = _resolve_js(spec, f, fileset, _nearest_aliases(f, alias_map))
                if hit:
                    targets.append(hit)
        elif lang == "Go" and go_mod:
            for imp in go_imports(p.read_text(errors="replace")):
                if imp.startswith(go_mod + "/"):
                    hit = go_dirs.get(imp[len(go_mod) + 1:])
                    if hit:
                        targets.append(hit)
        for t in set(targets):
            dst_mod = file_to_module.get(t)
            if dst_mod and dst_mod != src_mod:
                weights[(src_mod, dst_mod)] += 1
    return [Edge(s, t, w) for (s, t), w in sorted(weights.items())]
