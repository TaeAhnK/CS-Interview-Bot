"""Initialize and load the user's local knowledge library."""

from __future__ import annotations

import os
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from bot.knowledge_schema import KnowledgeDocument, Topic


BUNDLED_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
INITIALIZATION_MARKER = ".initialized"


@dataclass(frozen=True)
class KnowledgeFileError:
    filename: str
    detail: str


@dataclass(frozen=True)
class KnowledgeLibrary:
    _documents: tuple[KnowledgeDocument, ...] = field(repr=False)
    errors: tuple[KnowledgeFileError, ...]

    @property
    def documents(self) -> tuple[KnowledgeDocument, ...]:
        """Borrow the validated documents for read-only processing."""
        return self._documents

    def study_topics(self) -> tuple[Topic, ...]:
        return tuple(doc.topic.model_copy(deep=True) for doc in self._documents)

    def study_documents(self) -> list[dict]:
        return [document.model_dump() for document in self._documents]


def user_knowledge_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "CS Interview Bot" / "knowledge"


def initialize_knowledge(
    directory: Path,
    bundled_directory: Path = BUNDLED_KNOWLEDGE_DIR,
) -> None:
    marker = directory / INITIALIZATION_MARKER
    if marker.is_file():
        return

    defaults = [path for path in _json_files(bundled_directory) if not path.is_dir()]
    if not defaults:
        raise FileNotFoundError(f"No bundled knowledge JSON files in {bundled_directory}")

    directory.mkdir(parents=True, exist_ok=True)
    for source in defaults:
        destination = directory / source.name
        if destination.exists():
            continue
        # Publish the complete file in one operation without replacing an existing file.
        with TemporaryDirectory(dir=directory) as staging_directory:
            staged = Path(staging_directory) / source.name
            shutil.copyfile(source, staged)
            try:
                if os.name == "nt":
                    staged.rename(destination)
                else:
                    os.link(staged, destination)
            except FileExistsError:
                continue

    # A successfully initialized but empty library must stay empty on restart.
    marker.touch(exist_ok=True)


def load_knowledge(
    directory: Path | None = None,
    *,
    bundled_directory: Path | None = BUNDLED_KNOWLEDGE_DIR,
) -> KnowledgeLibrary:
    if directory is None:
        directory = user_knowledge_directory()
    if bundled_directory is not None:
        initialize_knowledge(directory, bundled_directory)

    documents: dict[Path, KnowledgeDocument] = {}
    topic_files: dict[str, list[Path]] = defaultdict(list)
    errors: list[KnowledgeFileError] = []
    for path in _json_files(directory):
        try:
            if path.is_dir():
                continue
            document = KnowledgeDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False):
                location = ".".join(map(str, error["loc"])) or "$"
                errors.append(KnowledgeFileError(path.name, f"{location}: {error['msg']}"))
            continue
        except (OSError, UnicodeError) as exc:
            errors.append(KnowledgeFileError(path.name, str(exc)))
            continue
        documents[path] = document
        topic_files[document.topic.id].append(path)

    for topic_id, paths in topic_files.items():
        if len(paths) < 2:
            continue
        filenames = ", ".join(path.name for path in paths)
        for path in paths:
            errors.append(
                KnowledgeFileError(path.name, f"topic.id: Duplicate ID {topic_id!r} in {filenames}")
            )
            del documents[path]

    return KnowledgeLibrary(tuple(documents.values()), tuple(errors))


def _json_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.suffix.lower() == ".json")
