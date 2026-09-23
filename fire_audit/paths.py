from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def index(self) -> Path:
        return self.data / "index" / "norms.sqlite3"

    @property
    def normative_cache(self) -> Path:
        return self.data / "normative_cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def app_config(self) -> Path:
        return self.root / "config" / "app.json"

    def ensure(self) -> None:
        for path in (
            self.knowledge,
            self.projects,
            self.templates,
            self.inbox,
            self.data / "index",
            self.normative_cache,
            self.logs,
            self.root / "config",
        ):
            path.mkdir(parents=True, exist_ok=True)


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_paths(root: str | Path | None = None) -> AppPaths:
    paths = AppPaths(Path(root).resolve() if root else default_root())
    paths.ensure()
    return paths

