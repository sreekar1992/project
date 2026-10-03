"""Local, read-only medicine-name catalog for clinician autocomplete.

The catalog is deliberately separate from the clinical decision-support model.
It contains only public product labels and non-clinical display metadata from a
locally supplied dataset.  It does not provide dose, contraindication,
interaction, or treatment recommendations.

The source CSV is intentionally not imported into the application database.
Instead, a compact SQLite lookup index is built under ``var/`` on first use.
This keeps the third-party source data out of migrations, backups, and Git,
while making prefix autocomplete fast enough for the prescription form.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import csv
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Iterator, TextIO
import zipfile


MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_QUERY_LENGTH = 100
MAX_RESULT_LIMIT = 20
INDEX_FORMAT_VERSION = "2"


class MedicineCatalogError(RuntimeError):
    """Raised for a malformed or unavailable local medicine source."""


@dataclass(frozen=True)
class MedicineCatalogItem:
    """A display-only medicine entry appropriate for a clinician selector."""

    id: str
    name: str
    composition: str | None = None
    manufacturer: str | None = None
    category: str | None = None

    def api_dict(self) -> dict[str, str]:
        value = {"id": self.id, "name": self.name}
        if self.composition:
            value["composition"] = self.composition
        if self.manufacturer:
            value["manufacturer"] = self.manufacturer
        if self.category:
            value["category"] = self.category
        return value


def _normalise(value: str) -> str:
    """Make a conservative lookup key without changing the display name."""
    return " ".join(value.casefold().split())


def _safe_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = " ".join(value.replace("\x00", " ").split())
    if not clean:
        return None
    return clean[:maximum]


def _category_from_name(name: str) -> str | None:
    """Use only an explicit dosage-form suffix, never infer a clinical route."""
    match = re.search(
        r"\b(tablet|capsule|syrup|suspension|injection|cream|ointment|gel|drops|"
        r"spray|inhaler|solution|lotion|powder|sachet|lozenge|granules|patch)\s*$",
        name,
        flags=re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def _source_composition(value: object) -> str | None:
    """Keep a composition only when the source supplies a complete label.

    The downloaded source sometimes stores the first ingredient in a merged
    packaging/manufacturer column and leaves this field beginning with a dose
    such as ``(500mg)``.  Returning that fragment as a composition would be
    misleading, so it is omitted rather than reconstructed or guessed.
    """
    composition = _safe_text(value, 1_000)
    if composition is not None and composition.startswith("("):
        return None
    return composition


def _next_prefix(prefix: str) -> str:
    """Return the smallest string lexically greater than all prefix matches."""
    # SQLite's BINARY ordering lets this bounded range use the indexed column.
    # Appending U+10FFFF is portable and avoids locale-dependent collation.
    return prefix + "\U0010ffff"


class MedicineCatalog:
    """Build and query a compact local prefix index for medicine names.

    The current Kaggle source has clean ``Medicine Name`` and ``Composition``
    columns but no reliable standalone manufacturer field.  The loader therefore
    does not attempt to reconstruct a manufacturer from concatenated scrape
    text: incorrect provenance is worse than an omitted optional field.
    """

    def __init__(self, source_path: Path, index_path: Path):
        self.source_path = Path(source_path)
        self.index_path = Path(index_path)
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return self.source_path.is_file() and self.source_path.suffix.lower() in {".csv", ".zip"}

    def search(self, query: str, limit: int = 8) -> list[MedicineCatalogItem]:
        """Return up to ``limit`` case-insensitive medicine-name prefix matches."""
        if not isinstance(query, str):
            raise MedicineCatalogError("The medicine search text is invalid.")
        if len(query) > MAX_QUERY_LENGTH:
            raise MedicineCatalogError("The medicine search text is too long.")
        prefix = _normalise(query)
        if not prefix:
            return []
        if not self.available:
            return []
        bounded_limit = max(1, min(int(limit), MAX_RESULT_LIMIT))
        self._ensure_index()
        try:
            with sqlite3.connect(self.index_path) as connection:
                rows = connection.execute(
                    """
                    SELECT medicine_id, name, composition, manufacturer, category
                    FROM medicines
                    WHERE name_norm >= ? AND name_norm < ?
                    ORDER BY name_norm, medicine_id
                    LIMIT ?
                    """,
                    (prefix, _next_prefix(prefix), bounded_limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MedicineCatalogError("The local medicine index could not be queried.") from exc
        return [MedicineCatalogItem(
            id=f"medicine-{row[0]}", name=row[1], composition=row[2],
            manufacturer=row[3], category=row[4],
        ) for row in rows]

    def _ensure_index(self) -> None:
        with self._lock:
            if self._index_matches_source():
                return
            self._build_index()

    def _source_metadata(self) -> dict[str, int | str]:
        try:
            stat = self.source_path.stat()
        except OSError as exc:
            raise MedicineCatalogError("The local medicine source is unavailable.") from exc
        if stat.st_size <= 0 or stat.st_size > MAX_SOURCE_BYTES:
            raise MedicineCatalogError("The local medicine source is not an accepted size.")
        return {
            "source_name": self.source_path.name,
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
        }

    def _index_matches_source(self) -> bool:
        if not self.index_path.is_file() or not self.available:
            return False
        try:
            with sqlite3.connect(self.index_path) as connection:
                rows = dict(connection.execute("SELECT key, value FROM catalog_meta").fetchall())
        except sqlite3.Error:
            return False
        try:
            expected = self._source_metadata()
        except MedicineCatalogError:
            return False
        return (rows.get("format") == INDEX_FORMAT_VERSION
                and rows.get("source") == json.dumps(expected, sort_keys=True))

    @contextmanager
    def _open_source(self) -> Iterator[TextIO]:
        """Open a CSV in place; ZIP members are never extracted to disk."""
        if self.source_path.suffix.lower() == ".csv":
            with self.source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
                yield source
            return
        try:
            with zipfile.ZipFile(self.source_path) as archive:
                candidates = [entry for entry in archive.infolist()
                              if not entry.is_dir() and entry.filename.lower().endswith(".csv")]
                if len(candidates) != 1:
                    raise MedicineCatalogError("The archive must contain exactly one CSV file.")
                member = candidates[0]
                if member.file_size <= 0 or member.file_size > MAX_SOURCE_BYTES:
                    raise MedicineCatalogError("The catalog CSV is not an accepted size.")
                with archive.open(member, "r") as raw:
                    with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as source:
                        yield source
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise MedicineCatalogError("The local medicine archive cannot be read.") from exc

    def _build_index(self) -> None:
        source_metadata = self._source_metadata()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_name(f".{self.index_path.name}.{os.getpid()}.new")
        try:
            if temporary_path.exists():
                temporary_path.unlink()
            with sqlite3.connect(temporary_path) as connection:
                connection.executescript("""
                    PRAGMA journal_mode=OFF;
                    PRAGMA synchronous=OFF;
                    CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE medicines (
                        medicine_id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        name_norm TEXT NOT NULL,
                        composition TEXT,
                        composition_norm TEXT NOT NULL,
                        manufacturer TEXT,
                        category TEXT,
                        UNIQUE(name_norm, composition_norm)
                    );
                """)
                rows: list[tuple[str, str, str | None, str, None, str | None]] = []
                with self._open_source() as source:
                    reader = csv.DictReader(source)
                    expected_columns = {"Medicine Name", "Composition"}
                    if not reader.fieldnames or not expected_columns.issubset(set(reader.fieldnames)):
                        raise MedicineCatalogError("The medicine catalog has an unsupported CSV schema.")
                    for raw in reader:
                        name = _safe_text(raw.get("Medicine Name"), 300)
                        if name is None:
                            continue
                        composition = _source_composition(raw.get("Composition"))
                        composition_norm = _normalise(composition or "")
                        rows.append((name, _normalise(name), composition, composition_norm, None,
                                     _category_from_name(name)))
                        if len(rows) >= 2_000:
                            connection.executemany(
                                """INSERT OR IGNORE INTO medicines
                                   (name, name_norm, composition, composition_norm, manufacturer, category)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                rows,
                            )
                            rows.clear()
                if rows:
                    connection.executemany(
                        """INSERT OR IGNORE INTO medicines
                           (name, name_norm, composition, composition_norm, manufacturer, category)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
                connection.execute("CREATE INDEX medicines_name_norm_idx ON medicines(name_norm, medicine_id)")
                connection.execute("INSERT INTO catalog_meta(key, value) VALUES (?, ?)",
                                   ("source", json.dumps(source_metadata, sort_keys=True)))
                connection.execute("INSERT INTO catalog_meta(key, value) VALUES (?, ?)",
                                   ("format", INDEX_FORMAT_VERSION))
                connection.commit()
            os.replace(temporary_path, self.index_path)
        except (OSError, sqlite3.Error, csv.Error) as exc:
            raise MedicineCatalogError("The local medicine index could not be built.") from exc
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
