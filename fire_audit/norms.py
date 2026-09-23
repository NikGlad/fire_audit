from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree

from .io_utils import read_json, sha256_file, utc_now
from .norm_lock import NormativeUpdateBusy, normative_update_lock
from .paths import AppPaths
from .document_text import DocumentTextExtractor
from .io_utils import write_json


TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class SourceInfo:
    kind: str
    path: Path
    repository_url: str


class NormativeIndex:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def app_config(self) -> dict[str, Any]:
        return read_json(self.paths.app_config, {})

    def repository_path(self) -> Path:
        return self.paths.normative_cache / "repository"

    def seed_zip_path(self) -> Path:
        configured = self.app_config().get("local_seed_zip", "../fire-docs-main.zip")
        return (self.paths.root / configured).resolve()

    def select_source(self) -> SourceInfo:
        repo = self.repository_path()
        url = self.app_config().get("normative_repository", "")
        if (repo / ".git").exists():
            return SourceInfo("git", repo, url)
        seed = self.seed_zip_path()
        if seed.exists():
            return SourceInfo("zip", seed, url)
        return SourceInfo("missing", repo, url)

    def sync_repository(
        self, *, rebuild_index: bool = False, full_text: bool = True,
        include_letters: bool = True, full_text_letters: bool = True,
    ) -> dict[str, Any]:
        # Hold one lock across pull/clone AND extraction, including MCP/CLI calls.
        with normative_update_lock(self.paths.normative_cache / "update.lock"):
            result = self._sync_repository()
            if rebuild_index:
                result["index"] = self._rebuild(full_text, include_letters, full_text_letters)
            return result

    def _sync_repository(self) -> dict[str, Any]:
        config = self.app_config()
        url = config.get("normative_repository")
        if not url:
            raise ValueError("Не указан адрес нормативного репозитория")
        git = shutil.which("git")
        if not git:
            raise RuntimeError("Git не найден")
        repo = self.repository_path()
        repo.parent.mkdir(parents=True, exist_ok=True)
        if (repo / ".git").exists():
            command = [git, "-C", str(repo), "pull", "--ff-only"]
        elif repo.exists() and any(repo.iterdir()):
            raise RuntimeError(f"Каталог кэша не пуст и не является Git-репозиторием: {repo}")
        else:
            command = [git, "clone", "--depth", "1", url, str(repo)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout).strip())
        return {
            "ok": True,
            "repository": str(repo),
            "command_output": (completed.stdout or completed.stderr).strip(),
            "commit": self.git_commit(),
        }

    def git_commit(self) -> str:
        repo = self.repository_path()
        if not (repo / ".git").exists():
            return ""
        git = shutil.which("git")
        if not git:
            return self._read_local_commit(repo)
        completed = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return completed.stdout.strip() if completed.returncode == 0 else self._read_local_commit(repo)

    @staticmethod
    def _read_local_commit(repo: Path) -> str:
        # Read-only fallback when Git cannot run under a sandbox identity. Do not
        # change ownership or broaden Git's safe.directory configuration.
        metadata = repo / ".git"
        try:
            head = (metadata / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                name = head[5:]
                ref = (metadata / name).resolve()
                if not name.startswith("refs/") or not ref.is_relative_to(metadata.resolve()):
                    return ""
                if ref.is_file():
                    head = ref.read_text(encoding="utf-8").strip()
                else:
                    for line in (metadata / "packed-refs").read_text(encoding="utf-8").splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == name:
                            head = parts[0]
                            break
            return head if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head) else ""
        except (OSError, UnicodeError):
            return ""

    @contextmanager
    def _connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        database = self.paths.index.resolve().as_uri() + "?mode=ro" if read_only else str(self.paths.index)
        connection = sqlite3.connect(database, uri=read_only, timeout=30)
        try:
            if not read_only:
                connection.execute("PRAGMA journal_mode=WAL")
                self._create_documents_table(connection)
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(relative_path, content, content='documents', content_rowid='id')"
                    )
                except sqlite3.OperationalError as exc:
                    if "no such module: fts5" not in str(exc).lower():
                        raise
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _create_documents_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_path TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                folder TEXT NOT NULL,
                suffix TEXT NOT NULL,
                sha256 TEXT NOT NULL DEFAULT '',
                text_status TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                extraction_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        if "extraction_json" not in {row[1] for row in connection.execute("PRAGMA table_info(documents)")}:
            connection.execute("ALTER TABLE documents ADD COLUMN extraction_json TEXT NOT NULL DEFAULT '{}'")

    def rebuild(
        self,
        full_text: bool = True,
        include_letters: bool = True,
        full_text_letters: bool = True,
    ) -> dict[str, Any]:
        with normative_update_lock(self.paths.normative_cache / "update.lock"):
            return self._rebuild(full_text, include_letters, full_text_letters)

    def _rebuild(
        self, full_text: bool, include_letters: bool, full_text_letters: bool,
    ) -> dict[str, Any]:
        source = self.select_source()
        if source.kind == "missing":
            raise FileNotFoundError("Не найден ни кэш Git, ни локальный ZIP нормативной базы")
        # Extract documents into an isolated database. The published index remains
        # readable throughout PDF/Word parsing, without a long write transaction.
        with tempfile.TemporaryDirectory(prefix="norms-build-", dir=self.paths.index.parent) as temp:
            prepared = Path(temp) / "prepared.sqlite3"
            with closing(sqlite3.connect(prepared)) as staging, staging:
                self._create_documents_table(staging)
                count, text_count, skipped_letters = self._stage_records(
                    staging, source, full_text, include_letters, full_text_letters
                )
            try:
                with self._connect() as connection:
                    connection.execute("ATTACH DATABASE ? AS prepared", (str(prepared),))
                    with connection:
                        # SQLite waits here for a short-lived external writer BEFORE
                        # changing any published row. Failure rolls the entire update back.
                        connection.execute("BEGIN IMMEDIATE")
                        connection.execute("DELETE FROM documents")
                        connection.execute(
                            "INSERT INTO documents (id,source_kind,source_path,relative_path,folder,suffix,sha256,text_status,indexed_at,content,extraction_json) "
                            "SELECT id,source_kind,source_path,relative_path,folder,suffix,sha256,text_status,indexed_at,content,extraction_json FROM prepared.documents"
                        )
                        if connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name = 'documents_fts'"
                        ).fetchone():
                            connection.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")
            except sqlite3.OperationalError as exc:
                if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise NormativeUpdateBusy(
                        "Индекс занят другой операцией. Предыдущий индекс сохранён; повторите обновление после её завершения."
                    ) from exc
                raise
        report = {
            "ok": True,
            "source_kind": source.kind,
            "source_path": str(source.path),
            "repository_url": source.repository_url,
            "git_commit": self.git_commit(),
            "documents": count,
            "documents_with_text": text_count,
            "full_text": full_text,
            "full_text_letters": full_text_letters,
            "include_letters": include_letters,
            "skipped_letters": skipped_letters,
            "index": str(self.paths.index),
        }
        report.update(self.coverage())
        return report

    def _stage_records(
        self, connection: sqlite3.Connection, source: SourceInfo,
        full_text: bool, include_letters: bool, full_text_letters: bool,
    ) -> tuple[int, int, int]:
        count = 0
        text_count = 0
        skipped_letters = 0
        for record in self._iter_records(
            source,
            full_text=full_text,
            include_letters=include_letters,
            full_text_letters=full_text_letters,
        ):
            if record["folder"] == "письма ВНИИПО" and not include_letters:
                skipped_letters += 1
                continue
            connection.execute(
                """
                INSERT INTO documents
                (source_kind, source_path, relative_path, folder, suffix, sha256, text_status, indexed_at, content, extraction_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.kind,
                    str(source.path),
                    record["relative_path"],
                    record["folder"],
                    record["suffix"],
                    record.get("sha256", ""),
                    record["text_status"],
                    utc_now(),
                    record.get("content", ""),
                    json.dumps(record.get("extraction", {}), ensure_ascii=False),
                ),
            )
            count += 1
            if record.get("content"):
                text_count += 1
        return count, text_count, skipped_letters

    def status(self) -> dict[str, Any]:
        source = self.select_source()
        result: dict[str, Any] = {
            "source_kind": source.kind,
            "source_path": str(source.path),
            "repository_url": source.repository_url,
            "git_commit": self.git_commit(),
            "index_path": str(self.paths.index),
            "index_exists": self.paths.index.exists(),
            "documents": 0,
            "documents_with_text": 0,
        }
        if self.paths.index.exists():
            with self._connect(read_only=True) as connection:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone():
                    return result
                row = connection.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN content <> '' THEN 1 ELSE 0 END) FROM documents"
                ).fetchone()
            result["documents"] = int(row[0] or 0)
            result["documents_with_text"] = int(row[1] or 0)
        result.update(self.coverage())
        return result

    def search(self, query: str, limit: int = 20, folder: str = "") -> list[dict[str, Any]]:
        if not self.paths.index.exists():
            return []
        query = query.strip()
        if not query:
            return []
        with self._connect(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone():
                return []
            return self._search_connection(connection, query, limit, folder)

    @staticmethod
    def _search_connection(connection: sqlite3.Connection, query: str, limit: int, folder: str) -> list[dict[str, Any]]:
        connection.row_factory = sqlite3.Row
        tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё._-]+", query)
        fts_query = " AND ".join(f'"{token}"' for token in tokens if token)
        rows: list[sqlite3.Row] = []
        if fts_query:
            try:
                sql = (
                    "SELECT d.relative_path, d.folder, d.suffix, d.text_status, d.sha256, "
                    "snippet(documents_fts, 1, '[', ']', ' … ', 24) AS fragment "
                    "FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid "
                    "WHERE documents_fts MATCH ?"
                )
                params: list[Any] = [fts_query]
                if folder:
                    sql += " AND d.folder = ?"
                    params.append(folder)
                sql += " ORDER BY CASE d.folder WHEN 'рабочая' THEN 0 WHEN 'письма ВНИИПО' THEN 1 ELSE 2 END, bm25(documents_fts) LIMIT ?"
                params.append(max(1, min(limit, 100)))
                rows = connection.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                if not any(value in str(exc).lower() for value in ("no such table: documents_fts", "no such module: fts5")):
                    raise
        if not rows:
            pattern = f"%{query}%"
            sql = "SELECT relative_path, folder, suffix, text_status, sha256, substr(content, 1, 500) AS fragment FROM documents WHERE (relative_path LIKE ? OR content LIKE ?)"
            params = [pattern, pattern]
            if folder:
                sql += " AND folder = ?"
                params.append(folder)
            sql += " ORDER BY CASE folder WHEN 'рабочая' THEN 0 WHEN 'письма ВНИИПО' THEN 1 ELSE 2 END, relative_path LIMIT ?"
            params.append(max(1, min(limit, 100)))
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def coverage(self) -> dict[str, Any]:
        if not self.paths.index.exists():
            return {"full_text_coverage_complete": False, "documents_requiring_attention": 0}
        with self._connect(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone():
                return {"full_text_coverage_complete": False, "documents_requiring_attention": 0}
            rows = connection.execute("SELECT text_status,COUNT(*) FROM documents GROUP BY text_status").fetchall()
        counts = dict(rows)
        unresolved = sum(value for status, value in rows if status not in {"text_extracted", "ocr_extracted"})
        return {"full_text_coverage_complete": bool(rows) and unresolved == 0,
                "documents_requiring_attention": unresolved, "text_status_counts": counts,
                "coverage_scope": "automated_text_extraction_not_normative_analysis",
                "ocr_documents_require_visual_verification": counts.get("ocr_extracted", 0)}

    def list_documents(self, folder: str = "", needs_attention: bool = False,
                       offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        with self._connect(read_only=True) as connection:
            connection.row_factory = sqlite3.Row
            conditions, params = [], []
            if folder:
                conditions.append("folder = ?")
                params.append(folder)
            if needs_attention:
                conditions.append("text_status NOT IN ('text_extracted','ocr_extracted')")
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            total = connection.execute("SELECT COUNT(*) FROM documents" + where, params).fetchone()[0]
            rows = connection.execute(
                "SELECT relative_path,folder,suffix,sha256,text_status FROM documents" + where
                + " ORDER BY relative_path LIMIT ? OFFSET ?", [*params, max(1, min(limit, 500)), offset]
            ).fetchall()
        return {"total": total, "documents": [dict(row) for row in rows],
                "next_offset": offset + len(rows) if offset + len(rows) < total else None}

    def find_letters(self, reference: str) -> dict[str, Any]:
        reference = reference.strip().strip("[]")
        if not re.fullmatch(r"\d+", reference):
            raise ValueError("Укажите номер ссылки, например 280 или [280]")
        with self._connect(read_only=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT relative_path,sha256,text_status FROM documents WHERE folder='письма ВНИИПО' ORDER BY relative_path"
            ).fetchall()
        matches = [dict(row) for row in rows if PurePosixPath(row["relative_path"]).name.startswith(reference)]
        nomenclature = [dict(row) for row in rows if "номенклатур" in row["relative_path"].lower()]
        return {"reference": reference, "all_parts": matches, "total_parts": len(matches),
                "nomenclature": nomenclature, "missing": not matches,
                "instruction": "Открыть все части и страницы, сверить дату и номер с изображением; письмо является разъяснением."}

    def read_document(self, relative_path: str, page: int | None = None,
                      offset: int = 0, max_chars: int = 20000) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        with self._connect(read_only=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM documents WHERE relative_path=?", (relative_path,)).fetchone()
        if row is None:
            raise FileNotFoundError(relative_path)
        row = dict(row)
        extraction = json.loads(row.get("extraction_json") or "{}")
        pages = extraction.get("pages", [])
        if page is not None:
            selected = next((p for p in pages if p["page"] == page), None)
            if selected is None:
                raise ValueError("Страница/часть не найдена; обновите полный индекс, если он устарел")
            text = selected["text"]
            if selected.get("comments"):
                text += "\n[Комментарии документа, не нормативный текст]\n" + "\n".join(selected["comments"])
        else:
            text = row["content"]
        max_chars = max(1000, min(max_chars, 40000))
        chunk = text[offset:offset + max_chars]
        source_path = Path(row["source_path"]) / relative_path if row["source_kind"] == "git" else Path(row["source_path"])
        source_matches = (source_path.is_file() and sha256_file(source_path) == row["sha256"]) if row["source_kind"] == "git" else None
        return {
            "relative_path": relative_path, "source_path": str(source_path), "source_sha256": row["sha256"],
            "source_matches_index": source_matches, "text_status": row["text_status"],
            "page_count": extraction.get("page_count"), "page": page,
            "page_metadata": [{k: v for k, v in p.items() if k not in {"text", "comments"}} for p in pages],
            "full_text_complete": extraction.get("complete", False),
            "visual_review_required": extraction.get("visual_review_required", True),
            "text": chunk, "total_chars": len(text), "offset": offset,
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(text) else None,
            "letter_references_in_fragment": sorted(set(re.findall(r"\[(\d+)\]", chunk))),
            "letter_references_in_document": sorted(set(re.findall(r"\[(\d+)\]", row["content"]))),
            "instruction": "Прочитать весь относящийся пункт, контекст, таблицы и ссылки; OCR, реквизиты и комментарии сверять с оригиналом. Индексация не означает выполненный нормативный анализ.",
        }

    def render_page(self, relative_path: str, page: int = 1) -> dict[str, Any]:
        from .document_text import native_tool, run_hidden

        if page < 1:
            raise ValueError("Номер страницы начинается с 1")
        document = self.read_document(relative_path)
        if document["source_matches_index"] is not True:
            raise ValueError("Для визуального чтения нужен соответствующий индексу файл Git-кэша")
        source = Path(document["source_path"])
        if not source.resolve().is_relative_to(self.repository_path().resolve()):
            raise ValueError("Файл вне нормативного репозитория")
        preview = self.paths.data / "norm_previews" / document["source_sha256"]
        preview.mkdir(parents=True, exist_ok=True)
        target = preview / f"page-{page}.png"
        if not target.exists():
            if source.suffix.lower() == ".pdf":
                renderer = native_tool("pdftoppm")
                if not renderer:
                    raise RuntimeError("Не найден визуализатор PDF")
                completed = run_hidden(
                    [renderer, "-f", str(page), "-l", str(page), "-singlefile",
                     "-scale-to", "3000", "-png", str(source), str(target.with_suffix(""))],
                    capture_output=True, timeout=120,
                )
                if completed.returncode or not target.exists():
                    raise ValueError("Страница PDF не найдена или не визуализируется")
            elif source.suffix.lower() in IMAGE_SUFFIXES:
                from PIL import Image, ImageOps
                with Image.open(source) as image:
                    image.seek(page - 1)
                    frame = ImageOps.exif_transpose(image).convert("RGB")
                    frame.thumbnail((3000, 3000))
                    frame.save(target)
            else:
                raise ValueError("Для Word открыть исходный файл; номер части XML не является печатной страницей")
        return {"image_path": str(target), "page": page, "source_sha256": document["source_sha256"],
                "relative_path": relative_path, "instruction": "Визуально сверить цитату, реквизиты, таблицы и сноски с оригиналом"}

    def _iter_records(
        self,
        source: SourceInfo,
        full_text: bool,
        include_letters: bool,
        full_text_letters: bool,
    ) -> Iterable[dict[str, Any]]:
        if source.kind == "git":
            root = source.path
            paths = [p for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts]
            if not include_letters:
                paths = [p for p in paths if p.relative_to(root).parts[0] != "письма ВНИИПО"]
            extractor = DocumentTextExtractor(self.paths.data / "text_cache")

            def read_path(path: Path) -> dict:
                relative = path.relative_to(root).as_posix()
                folder = relative.split("/", 1)[0] if "/" in relative else ""
                extract_text = full_text and (folder != "письма ВНИИПО" or full_text_letters)
                digest = sha256_file(path)
                extracted = extractor.extract(path, digest) if extract_text else {}
                return {
                    "relative_path": relative,
                    "folder": folder,
                    "suffix": path.suffix.lower(),
                    "sha256": digest,
                    "content": extracted.get("content", ""),
                    "text_status": extracted.get("text_status", "metadata_only"),
                    "extraction": extracted,
                }
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(read_path, path) for path in paths]
                for index, future in enumerate(as_completed(futures), 1):
                    result = future.result()
                    if index % 10 == 0 or index == len(paths):
                        write_json(self.paths.data / "index/progress.json",
                                   {"processed": index, "total": len(paths), "file": result["relative_path"], "at": utc_now()})
                    yield result
        else:
            with zipfile.ZipFile(source.path) as archive:
                for info in sorted(archive.infolist(), key=lambda value: value.filename):
                    if info.is_dir():
                        continue
                    pure = PurePosixPath(info.filename)
                    parts = pure.parts
                    if len(parts) < 3:
                        continue
                    relative = PurePosixPath(*parts[1:]).as_posix()
                    folder = parts[1]
                    if folder == "письма ВНИИПО" and not include_letters:
                        continue
                    suffix = pure.suffix.lower()
                    content = ""
                    status = "metadata_only"
                    extracted = {}
                    digest = ""
                    extract_text = full_text and (folder != "письма ВНИИПО" or full_text_letters)
                    if extract_text:
                        with tempfile.TemporaryDirectory(prefix="norm-zip-") as temp:
                            target = Path(temp) / pure.name
                            target.write_bytes(archive.read(info))
                            digest = sha256_file(target)
                            extracted = DocumentTextExtractor(self.paths.data / "text_cache").extract(target, digest)
                            content, status = extracted["content"], extracted["text_status"]
                    yield {
                        "relative_path": relative,
                        "folder": folder,
                        "suffix": suffix,
                        "sha256": digest,
                        "content": content,
                        "text_status": status,
                        "extraction": extracted,
                    }

    def _extract_zip_member(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, suffix: str) -> tuple[str, str]:
        if suffix in TEXT_SUFFIXES:
            raw = archive.read(info)
            for encoding in ("utf-8-sig", "cp1251", "utf-16"):
                try:
                    return raw.decode(encoding), "text_extracted"
                except UnicodeDecodeError:
                    continue
            return "", "decode_failed"
        with tempfile.TemporaryDirectory(prefix="fire-audit-") as temp:
            path = Path(temp) / Path(info.filename).name
            with archive.open(info) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target)
            return self._extract_path(path)

    def _extract_path(self, path: Path) -> tuple[str, str]:
        result = DocumentTextExtractor(self.paths.data / "text_cache").extract(path, sha256_file(path))
        return result["content"], result["text_status"]

    @staticmethod
    def _extract_docx(path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        paragraphs: list[str] = []
        for paragraph in root.iter(WORD_NS + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(WORD_NS + "t"))
            if text.strip():
                paragraphs.append(text)
        return "\n".join(paragraphs)

    def _extract_pdf(self, path: Path) -> str:
        executable = self._find_pdftotext()
        if executable:
            completed = subprocess.run(
                [str(executable), "-layout", "-enc", "UTF-8", str(path), "-"],
                capture_output=True,
                timeout=120,
            )
            if completed.returncode == 0:
                return completed.stdout.decode("utf-8", errors="replace")
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path), strict=False)
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except (ImportError, OSError, ValueError):
            return ""

    @staticmethod
    def _find_pdftotext() -> Path | None:
        found = shutil.which("pdftotext")
        if found:
            return Path(found)
        candidate = (
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "native"
            / "poppler"
            / "Library"
            / "bin"
            / "pdftotext.exe"
        )
        if candidate.exists():
            return candidate
        alternative = candidate.parents[2] / "bin" / "pdftotext.exe"
        return alternative if alternative.exists() else None
