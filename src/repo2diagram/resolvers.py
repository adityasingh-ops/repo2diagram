"""Import resolvers for languages beyond Python, JS/TS and Go.

Each resolver is built once per repo (``__init__`` indexes the files) and then
maps one source file to the repo files it depends on (``targets``).
"""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from pathlib import Path, PurePosixPath


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- Rust

RUST_USE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([^;]+);", re.MULTILINE)
RUST_MOD = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", re.MULTILINE)
RUST_PATH_EXPR = re.compile(r"\b(crate|super|self)((?:::[A-Za-z_]\w*)+)")


class RustResolver:
    """Resolves ``mod x;``, ``use crate::a::b``, ``super::`` and workspace crates (``use other_crate::…``)."""

    def __init__(self, root: Path, files: list[str]):
        self.fileset = {f for f in files if f.endswith(".rs")}
        self.crates: dict[str, str] = {}  # crate dir ("" for root) -> crate src dir
        self.crate_names: dict[str, str] = {}  # crate name (snake_case) -> crate root file
        for f in files:
            if PurePosixPath(f).name != "Cargo.toml":
                continue
            try:
                data = tomllib.loads(_read(root, f))
            except tomllib.TOMLDecodeError:
                continue
            cdir = str(PurePosixPath(f).parent)
            cdir = "" if cdir == "." else cdir
            src = f"{cdir}/src" if cdir else "src"
            self.crates[cdir] = src
            name = (data.get("package") or {}).get("name")
            lib = (data.get("lib") or {}).get("path")
            for cand in ([f"{cdir}/{lib}" if cdir else lib] if lib else []) + [f"{src}/lib.rs", f"{src}/main.rs"]:
                if name and cand in self.fileset:
                    self.crate_names[name.replace("-", "_")] = cand
                    break

    def _crate_src(self, rel: str) -> str | None:
        parent = PurePosixPath(rel).parent
        for d in [parent, *parent.parents]:
            key = "" if str(d) == "." else str(d)
            if key in self.crates and rel.startswith(self.crates[key] + "/"):
                return self.crates[key]
        return None

    @staticmethod
    def _module_dir(rel: str) -> PurePosixPath:
        """Directory that holds a file's child modules (Rust 2018 rules)."""
        p = PurePosixPath(rel)
        if p.name in ("mod.rs", "lib.rs", "main.rs"):
            return p.parent
        return p.parent / p.stem

    def _lookup(self, base: PurePosixPath, segments: list[str]) -> str | None:
        """Longest prefix of ``segments`` under ``base`` that names a module file."""
        for n in range(len(segments), 0, -1):
            path = base.joinpath(*segments[:n])
            for cand in (f"{path}.rs", f"{path}/mod.rs"):
                if cand in self.fileset:
                    return cand
        return None

    def _resolve(self, rel: str, path: str) -> str | None:
        segs = [s.strip() for s in path.split("::") if s.strip()]
        if not segs:
            return None
        head, rest = segs[0], segs[1:]
        src = self._crate_src(rel)
        if head == "crate" and src:
            return self._lookup(PurePosixPath(src), rest)
        if head in ("self", "super"):
            base = self._module_dir(rel)
            while rest and rest[0] == "super":
                base, rest = base.parent, rest[1:]
            if head == "super":
                base = base.parent
            return self._lookup(base, rest) or (self._parent_file(base) if head == "super" and not rest else None)
        if head in self.crate_names:
            target = self.crate_names[head]
            return self._lookup(PurePosixPath(target).parent, rest) or target
        return None

    def _parent_file(self, mod_dir: PurePosixPath) -> str | None:
        for cand in (f"{mod_dir}.rs", f"{mod_dir}/mod.rs", f"{mod_dir}/lib.rs", f"{mod_dir}/main.rs"):
            if cand in self.fileset:
                return cand
        return None

    @staticmethod
    def _expand_use(tree: str) -> list[str]:
        """``a::{b, c::{d, e}}`` -> ["a::b", "a::c::d", "a::c::e"]."""
        tree = re.sub(r"\s+", "", tree.split(" as ")[0])
        m = re.match(r"^(.*?)\{(.*)\}$", tree)
        if not m:
            return [tree]
        prefix, body = m.groups()
        parts, depth, cur = [], 0, ""
        for ch in body:
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
            depth += ch == "{"
            depth -= ch == "}"
            cur += ch
        parts.append(cur)
        out = []
        for part in filter(None, parts):
            out += [prefix + p if p != "self" else prefix.rstrip(":") for p in RustResolver._expand_use(part)]
        return out

    def targets(self, root: Path, rel: str) -> list[str]:
        text = _read(root, rel)
        out = []
        mod_dir = self._module_dir(rel)
        for name in RUST_MOD.findall(text):
            hit = self._lookup(mod_dir, [name])
            if hit:
                out.append(hit)
        paths = [p for tree in RUST_USE.findall(text) for p in self._expand_use(tree)]
        paths += [h + t for h, t in RUST_PATH_EXPR.findall(text)]
        for path in paths:
            hit = self._resolve(rel, path)
            if hit and hit != rel:
                out.append(hit)
        return out


# --------------------------------------------------------------------------- Java / Kotlin / Scala

JVM_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
JVM_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?(\w+(?:\.\w+)*)(\.\*)?", re.MULTILINE)
JVM_EXTS = (".java", ".kt", ".kts", ".scala")


class JvmResolver:
    """Resolves ``import a.b.Foo`` / ``import a.b.*`` (and same-package references) via declared packages."""

    def __init__(self, root: Path, files: list[str]):
        self.classes: dict[str, str] = {}  # fully-qualified class -> file
        self.packages: dict[str, list[str]] = defaultdict(list)  # package -> files
        self.package_of: dict[str, str] = {}
        for f in files:
            if not f.endswith(JVM_EXTS):
                continue
            m = JVM_PACKAGE.search(_read(root, f))
            pkg = m.group(1) if m else ""
            self.package_of[f] = pkg
            self.packages[pkg].append(f)
            self.classes[f"{pkg}.{PurePosixPath(f).stem}" if pkg else PurePosixPath(f).stem] = f

    def _resolve(self, name: str, wildcard: bool) -> list[str]:
        if wildcard:
            if name in self.packages:
                return self.packages[name]
            return [self.classes[name]] if name in self.classes else []  # import a.b.Outer.*
        parts = name.split(".")
        for n in range(len(parts), 0, -1):  # a.b.Outer.Inner / static a.b.Util.method
            hit = self.classes.get(".".join(parts[:n]))
            if hit:
                return [hit]
        return []

    def targets(self, root: Path, rel: str) -> list[str]:
        text = _read(root, rel)
        out = []
        for name, star in JVM_IMPORT.findall(text):
            out += self._resolve(name, bool(star))
        # Classes in the same package need no import; count the ones this file mentions.
        for other in self.packages.get(self.package_of.get(rel, ""), []):
            if other != rel and re.search(rf"\b{re.escape(PurePosixPath(other).stem)}\b", text):
                out.append(other)
        return [t for t in out if t != rel]


# --------------------------------------------------------------------------- C#

CS_NAMESPACE = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
CS_USING = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;", re.MULTILINE)


class CSharpResolver:
    """Resolves ``using A.B;`` to the files that declare ``namespace A.B``.

    A namespace can span many files, so only files whose type names the source
    file actually mentions are counted (falling back to the whole namespace).
    """

    def __init__(self, root: Path, files: list[str]):
        self.namespaces: dict[str, list[str]] = defaultdict(list)
        self.namespace_of: dict[str, list[str]] = {}
        for f in files:
            if not f.endswith(".cs"):
                continue
            spaces = CS_NAMESPACE.findall(_read(root, f))
            self.namespace_of[f] = spaces
            for ns in spaces:
                self.namespaces[ns].append(f)

    def targets(self, root: Path, rel: str) -> list[str]:
        text = _read(root, rel)
        visible = set(CS_USING.findall(text))
        for ns in self.namespace_of.get(rel, []):  # enclosing namespaces are visible without `using`
            parts = ns.split(".")
            visible |= {".".join(parts[:n]) for n in range(1, len(parts) + 1)}
        out = []
        for ns in visible:
            candidates = [f for f in self.namespaces.get(ns, []) if f != rel]
            used = [f for f in candidates if re.search(rf"\b{re.escape(PurePosixPath(f).stem)}\b", text)]
            out += used or ([] if ns in self.namespace_of.get(rel, []) else candidates)
        return out
