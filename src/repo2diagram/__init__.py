"""repo2diagram: turn any codebase into an interactive architecture diagram."""

from repo2diagram.model import Edge, Module, RepoGraph
from repo2diagram.pipeline import build_graph

__version__ = "0.2.1"
__all__ = ["Edge", "Module", "RepoGraph", "__version__", "build_graph"]
