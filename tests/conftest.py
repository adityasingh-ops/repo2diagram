import subprocess
from pathlib import Path

import pytest

FILES = {
    "README.md": "# demo\n",
    "pyproject.toml": '[project]\nname = "demo"\ndependencies = ["fastapi>=0.1", "sqlalchemy"]\n',
    "src/demo/__init__.py": "",
    "src/demo/api/__init__.py": "",
    "src/demo/api/routes.py": "from demo.services.users import get_user\nfrom ..db import models\n",
    "src/demo/services/__init__.py": "",
    "src/demo/services/users.py": "from demo.db.models import User\n",
    "src/demo/db/__init__.py": "",
    "src/demo/db/models.py": "class User: ...\n",
    "src/demo/utils/__init__.py": "",
    "src/demo/utils/text.py": "def slug(s): return s\n",
    "web/package.json": "{}",
    "web/src/app.ts": "import { api } from './lib/api'\nimport Button from '@/components/Button'\n",
    "web/src/lib/api.ts": "export const api = 1\n",
    "web/src/components/Button.tsx": "import { api } from '../lib/api'\nexport default 1\n",
    "tests/test_users.py": "from demo.services.users import get_user\n",
    "docs/index.md": "docs\n",
    "node_modules/x/index.js": "ignored\n",
}


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = write_tree(tmp_path / "demo", FILES)
    (root / "web/tsconfig.json").write_text('{\n  // comment\n  "compilerOptions": {"paths": {"@/*": ["./src/*"]}},\n}\n')
    return root


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_repo(repo: Path) -> Path:
    (repo / ".gitignore").write_text("node_modules/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    git(repo, "checkout", "-qb", "feature")
    return repo
