"""Read and save authoring documents in the application's knowledge directory."""

from __future__ import annotations

import hashlib
import ntpath
import os
from pathlib import Path
from unicodedata import normalize
from uuid import uuid4

from pydantic import ValidationError

from bot.knowledge_loader import (
    KnowledgeFileTooLarge,
    KnowledgeLibraryLimitExceeded,
    KnowledgeReadBudget,
    MAX_REPORTED_ERRORS,
    MAX_KNOWLEDGE_CONCEPTS,
    bounded_validation_issues,
    check_knowledge_file_size,
    count_knowledge_concepts,
    is_knowledge_file,
    knowledge_json_files,
    read_knowledge_bytes,
    read_knowledge_document,
)
from bot.knowledge_schema import KnowledgeDocument, Topic
from bot.knowledge_write_directory import KnowledgeDirectory, open_knowledge_directory


_SAVE_LOCK_FILENAME = ".save.lock"


def _acquire_save_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _validation_error(error: ValidationError) -> dict:
    issues = bounded_validation_issues(error, MAX_REPORTED_ERRORS)
    return {
        "ok": False,
        "error": "invalid_document",
        "issues": issues,
        "omitted_issues": error.error_count() - len(issues),
    }


def _topic_name(topic: Topic) -> str:
    return normalize("NFC", topic.name.casefold())


def _revision(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _document_response(path: Path, document: KnowledgeDocument, contents: bytes) -> dict:
    return {
        "ok": True,
        "filename": path.name,
        "revision": _revision(contents),
        "document": document.model_dump(mode="json"),
    }


class KnowledgeEditor:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._topic_cache: dict[str, tuple[bytes, tuple[str, str, int] | None]] = {}

    def _files(self, root: KnowledgeDirectory) -> dict[str, Path]:
        return {
            path.name: path
            for path in knowledge_json_files(root)
            if is_knowledge_file(path, root=root)
        }

    def _file_path(self, filename: str) -> Path | None:
        if not isinstance(filename, str) or "/" in filename or "\\" in filename or "\0" in filename:
            return None
        if Path(filename).suffix.lower() != ".json" or (os.name == "nt" and ntpath.isreserved(filename)):
            return None
        return self._directory / filename

    def _topic_key(
        self, path: Path, budget: KnowledgeReadBudget, root: KnowledgeDirectory
    ) -> tuple[str, str, int] | None:
        contents = read_knowledge_bytes(path, budget, root=root)
        # External editors can change bytes without changing the size or timestamp.
        digest = hashlib.sha256(contents).digest()
        cached = self._topic_cache.get(path.name)
        if cached is not None and cached[0] == digest:
            return cached[1]
        try:
            document = KnowledgeDocument.model_validate_json(contents.decode("utf-8"))
            key = (document.topic.id, _topic_name(document.topic), count_knowledge_concepts(document))
        except (ValidationError, UnicodeError):
            key = None
        self._topic_cache[path.name] = (digest, key)
        return key

    def list_files(self) -> dict:
        try:
            with open_knowledge_directory(self._directory) as root:
                return {"ok": True, "files": list(self._files(root))}
        except OSError:
            return {"ok": False, "error": "read_failed"}

    def open_file(self, filename: str) -> dict:
        try:
            path = self._file_path(filename)
            if path is None:
                return {"ok": False, "error": "file_not_found"}
            document, contents = read_knowledge_document(path)
            return _document_response(path, document, contents)
        except ValidationError as error:
            return _validation_error(error)
        except FileNotFoundError:
            return {"ok": False, "error": "file_not_found"}
        except KnowledgeFileTooLarge:
            return {"ok": False, "error": "file_too_large"}
        except (OSError, UnicodeError):
            return {"ok": False, "error": "read_failed"}

    def save_file(
        self,
        data: dict,
        filename: str | None = None,
        revision: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        try:
            document = KnowledgeDocument.model_validate(data)
            with open_knowledge_directory(self._directory) as root:
                # Keep a stable lock file. The OS releases the lock when the handle closes.
                with os.fdopen(root.open_lock(_SAVE_LOCK_FILENAME), "r+b") as lock_file:
                    _acquire_save_lock(lock_file.fileno())
                    return self._save(root, document, filename, revision, overwrite)
        except ValidationError as error:
            return _validation_error(error)
        except KnowledgeFileTooLarge:
            return {"ok": False, "error": "file_too_large"}
        except KnowledgeLibraryLimitExceeded:
            return {"ok": False, "error": "library_too_large"}
        except (OSError, UnicodeError):
            return {"ok": False, "error": "save_failed"}

    def _save(
        self, root: KnowledgeDirectory, document: KnowledgeDocument,
        filename: str | None, revision: str | None, overwrite: bool,
    ) -> dict:
        contents = (document.model_dump_json(indent=2) + "\n").encode("utf-8")
        check_knowledge_file_size(len(contents))
        budget = KnowledgeReadBudget()
        budget.consume(len(contents))
        files = {path.name: path for path in knowledge_json_files(root)}
        if filename is None:
            destination = root.path / f"topic-{uuid4().hex}.json"
        else:
            destination = files.get(filename) if isinstance(filename, str) else None
            if destination is None:
                return {"ok": False, "error": "file_not_found"}
            if overwrite is not True:
                if not is_knowledge_file(destination, root=root):
                    return {"ok": False, "error": "file_not_found"}
                return {"ok": False, "error": "confirmation_required", "filename": filename}
            try:
                original_contents = read_knowledge_bytes(destination, root=root)
            except FileNotFoundError:
                return {"ok": False, "error": "file_not_found"}
            if revision != _revision(original_contents):
                return {"ok": False, "error": "file_changed", "filename": filename}
            original = KnowledgeDocument.model_validate_json(original_contents.decode("utf-8"))
            if document.topic.id != original.topic.id:
                return {"ok": False, "error": "topic_id_changed", "filename": filename}

        for name in self._topic_cache.keys() - files.keys():
            del self._topic_cache[name]
        topic_name = _topic_name(document.topic)
        concept_count = count_knowledge_concepts(document)
        for name, path in files.items():
            if name == filename:
                continue
            try:
                key = self._topic_key(path, budget, root)
            except (FileNotFoundError, KnowledgeFileTooLarge):
                self._topic_cache.pop(name, None)
                continue
            except KnowledgeLibraryLimitExceeded:
                raise
            except OSError:
                return {"ok": False, "error": "duplicate_check_failed", "filename": name}
            if key is not None and (key[0] == document.topic.id or key[1] == topic_name):
                return {"ok": False, "error": "duplicate_topic", "filename": name}
            if key is not None:
                concept_count += key[2]

        if concept_count > MAX_KNOWLEDGE_CONCEPTS:
            return {"ok": False, "error": "library_concept_limit"}

        with root.stage(contents) as publish:
            if filename is None:
                publish(destination.name)
            else:
                try:
                    current_revision = _revision(read_knowledge_bytes(destination, root=root))
                except FileNotFoundError:
                    return {"ok": False, "error": "file_not_found"}
                except KnowledgeFileTooLarge:
                    return {"ok": False, "error": "file_changed", "filename": filename}
                if current_revision != revision:
                    return {"ok": False, "error": "file_changed", "filename": filename}
                publish(destination.name, replace=True)

        return _document_response(destination, document, contents)
