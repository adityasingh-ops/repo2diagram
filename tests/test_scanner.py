from repo2diagram.scanner import group_modules, loads_jsonc, scan


def names(graph):
    return {m.path for m in graph.modules}


def edge_paths(graph):
    by_id = {m.id: m.path for m in graph.modules}
    return {(by_id[e.source], by_id[e.target]) for e in graph.edges}


def test_modules_open_up_container_dirs(repo):
    graph = scan(repo)
    assert {"src/demo/api", "src/demo/services", "src/demo/db", "src/demo/utils"} <= names(graph)


def test_ignores_vendor_tests_and_docs_by_default(repo):
    paths = names(scan(repo))
    assert not any(p.startswith(("node_modules", "tests", "docs")) for p in paths)
    assert any(p.startswith("tests") for p in names(scan(repo, include_tests=True)))


def test_python_imports_absolute_and_relative(repo):
    edges = edge_paths(scan(repo))
    assert ("src/demo/api", "src/demo/services") in edges
    assert ("src/demo/api", "src/demo/db") in edges  # from ..db import models
    assert ("src/demo/services", "src/demo/db") in edges


def test_js_relative_and_tsconfig_alias_imports(repo):
    graph = scan(repo)
    edges = edge_paths(graph)
    web = [p for p in names(graph) if p.startswith("web")]
    assert web
    lib = next(p for p in web if p.endswith("lib"))
    comps = next(p for p in web if p.endswith("components"))
    assert (comps, lib) in edges
    assert any(dst == comps for _, dst in edges)  # resolved through the @/ alias


def test_dependencies_and_roles(repo):
    graph = scan(repo)
    assert graph.dependencies[:2] == ["fastapi", "sqlalchemy"]
    roles = {m.path: m.role for m in graph.modules}
    assert roles["src/demo/api"] == "API layer"
    assert roles["src/demo/db"] == "Data access"


def test_flat_package_is_split_into_files():
    files = [f"pkg/{n}.py" for n in ("a", "b", "c", "d", "e")] + ["README.md"]
    groups = group_modules(files)
    assert "pkg/a" in groups and groups["pkg/a"] == ["pkg/a.py"]


def test_loads_jsonc_keeps_glob_strings():
    text = '{"compilerOptions": {"paths": {"@/*": ["./*"]}}, /* c */ "include": ["**/*.ts",],}'
    assert loads_jsonc(text)["compilerOptions"]["paths"]["@/*"] == ["./*"]

