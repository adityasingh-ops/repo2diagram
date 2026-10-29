from pathlib import Path

from conftest import write_tree

from repo2diagram.scanner import scan


def edge_paths(graph):
    by_id = {m.id: m.path for m in graph.modules}
    return {(by_id[e.source], by_id[e.target]) for e in graph.edges}


def links(graph, src_prefix, dst_prefix):
    return any(s.startswith(src_prefix) and d.startswith(dst_prefix) for s, d in edge_paths(graph))


def test_rust_workspace(tmp_path: Path):
    root = write_tree(tmp_path / "rs", {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        "crates/core/Cargo.toml": '[package]\nname = "my-core"\n',
        "crates/core/src/lib.rs": "pub mod engine;\npub mod util;\n",
        "crates/core/src/engine/mod.rs": "use crate::util::helpers::{trim, pad};\nmod plan;\n",
        "crates/core/src/engine/plan.rs": "use super::super::util;\n",
        "crates/core/src/util/mod.rs": "pub mod helpers;\n",
        "crates/core/src/util/helpers.rs": "pub fn trim() {}\n",
        "crates/cli/Cargo.toml": '[package]\nname = "cli"\n',
        "crates/cli/src/main.rs": "use my_core::engine::run;\nfn main() { my_core::util::helpers::trim(); }\n",
        "crates/server/Cargo.toml": '[package]\nname = "server"\n',
        "crates/server/src/main.rs": "use my_core::{engine, util::helpers};\n",
    })
    graph = scan(root)
    assert links(graph, "crates/cli", "crates/core")
    assert links(graph, "crates/server", "crates/core")
    assert not links(graph, "crates/core", "crates/cli")


def test_rust_modules_inside_one_crate(tmp_path: Path):
    root = write_tree(tmp_path / "one", {
        "Cargo.toml": '[package]\nname = "one"\n',
        "src/main.rs": "mod api;\nmod db;\nuse crate::api::routes;\n",
        "src/api/mod.rs": "pub mod routes;\n",
        "src/api/routes.rs": "use crate::db::Pool;\nuse super::super::db;\n",
        "src/db/mod.rs": "pub struct Pool;\n",
        "src/db/schema.rs": "use self::super::Pool;\n",
    })
    assert links(scan(root), "src/api", "src/db")


def test_java_and_kotlin_packages(tmp_path: Path):
    j = "src/main/java/com/shop"
    root = write_tree(tmp_path / "jvm", {
        "pom.xml": "<project/>",
        f"{j}/api/OrderController.java": "package com.shop.api;\nimport com.shop.service.OrderService;\n"
                                         "import static com.shop.util.Strings.trim;\nclass OrderController {}\n",
        f"{j}/service/OrderService.java": "package com.shop.service;\nimport com.shop.model.*;\n"
                                          "class OrderService { PriceCalc c; }\n",
        f"{j}/service/PriceCalc.java": "package com.shop.service;\nclass PriceCalc {}\n",
        f"{j}/model/Order.java": "package com.shop.model;\nclass Order {}\n",
        f"{j}/util/Strings.java": "package com.shop.util;\nclass Strings {}\n",
        f"{j}/jobs/Cleanup.kt": "package com.shop.jobs\n\nimport com.shop.model.Order\n\nclass Cleanup\n",
    })
    graph = scan(root)
    assert links(graph, f"{j}/api", f"{j}/service")
    assert links(graph, f"{j}/api", f"{j}/util")
    assert links(graph, f"{j}/service", f"{j}/model")
    assert links(graph, f"{j}/jobs", f"{j}/model")


def test_csharp_namespaces(tmp_path: Path):
    root = write_tree(tmp_path / "cs", {
        "Shop.sln": "",
        "src/Shop.Api/Controllers/OrdersController.cs":
            "using Shop.Core.Services;\nnamespace Shop.Api.Controllers;\nclass OrdersController { OrderService s; }\n",
        "src/Shop.Api/Program.cs": "namespace Shop.Api;\nclass Program {}\n",
        "src/Shop.Core/Services/OrderService.cs":
            "using Shop.Core.Models;\nnamespace Shop.Core.Services\n{\n  class OrderService { Order o; }\n}\n",
        "src/Shop.Core/Models/Order.cs": "namespace Shop.Core.Models;\nclass Order {}\n",
        "src/Shop.Core/Models/Customer.cs": "namespace Shop.Core.Models;\nclass Customer {}\n",
    })
    graph = scan(root)
    assert links(graph, "src/Shop.Api", "src/Shop.Core")
    assert not links(graph, "src/Shop.Core", "src/Shop.Api")
