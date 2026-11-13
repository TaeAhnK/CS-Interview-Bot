"""Initialize and load the user's local knowledge library."""

from __future__ import annotations

import ntpath
import os
import stat
from collections import defaultdict
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path

from pydantic import ValidationError

from bot.knowledge_schema import KnowledgeDocument
from bot.knowledge_write_directory import KnowledgeDirectory, open_knowledge_directory


BUNDLED_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
INITIALIZATION_MARKER = ".initialized"
MAX_KNOWLEDGE_FILE_BYTES = 2 * 1024 * 1024
MAX_KNOWLEDGE_LIBRARY_BYTES = 32 * 1024 * 1024
MAX_KNOWLEDGE_CONCEPTS = 100
MAX_REPORTED_ERRORS = 50
MAX_ERROR_DETAIL = 400
DUPLICATE_FILENAME_EXAMPLES = 3


class KnowledgeFileTooLarge(OSError):
    pass


class KnowledgeLibraryLimitExceeded(OSError):
    pass


class _NotKnowledgeFile(FileNotFoundError):
    """The file was absent or ineligible before opening it."""


@dataclass
class KnowledgeReadBudget:
    remaining: int = MAX_KNOWLEDGE_LIBRARY_BYTES

    def consume(self, size: int) -> None:
        if size > self.remaining:
            raise KnowledgeLibraryLimitExceeded("학습 자료 전체 읽기 한도(32 MiB)를 초과합니다.")
        self.remaining -= size


@dataclass(frozen=True)
class KnowledgeFileError:
    filename: str
    detail: str


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def bounded_validation_issues(error: ValidationError, limit: int) -> list[dict]:
    if not limit:
        return []
    issues = []
    # The document's before-validator bounds how many errors can exist here.
    for issue in islice(error.errors(include_input=False, include_url=False, include_context=False), limit):
        path = list(issue["loc"])
        half = MAX_ERROR_DETAIL // 2
        display_path = ".".join(_truncate(str(part), half) for part in path)
        if len(display_path) > half or any(isinstance(part, str) and len(part) > half for part in path):
            path = [_truncate(display_path, half)]
        issues.append({"path": path, "message": _truncate(issue["msg"], half - 2)})
    return issues


@dataclass
class _KnowledgeErrors:
    entries: list[KnowledgeFileError] = field(default_factory=list)
    omitted: int = 0

    @property
    def remaining(self) -> int:
        return MAX_REPORTED_ERRORS - len(self.entries)

    def add(self, filename: str, detail: str) -> None:
        if self.remaining:
            self.entries.append(KnowledgeFileError(filename, _truncate(detail, MAX_ERROR_DETAIL)))
        else:
            self.omitted += 1

    def validation(self, filename: str, exc: ValidationError) -> None:
        issues = bounded_validation_issues(exc, self.remaining)
        for issue in issues:
            location = ".".join(map(str, issue["path"])) or "$"
            self.add(filename, f"{location}: {issue['message']}")
        self.omitted += exc.error_count() - len(issues)

    def report(self) -> tuple[KnowledgeFileError, ...]:
        if self.omitted:
            return (*self.entries, KnowledgeFileError("", f"그 외 오류 {self.omitted:,}건"))
        return tuple(self.entries)


@dataclass(frozen=True)
class KnowledgeLibrary:
    _documents: tuple[KnowledgeDocument, ...] = field(repr=False)
    errors: tuple[KnowledgeFileError, ...]

    @property
    def documents(self) -> tuple[KnowledgeDocument, ...]:
        """Borrow the validated documents for read-only processing."""
        return self._documents

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
    with open_knowledge_directory(directory, create=True) as root:
        _initialize_knowledge(root, bundled_directory, _KnowledgeErrors())


def _initialize_knowledge(root: KnowledgeDirectory, bundled_directory: Path, errors: _KnowledgeErrors) -> None:
    if root.exists(INITIALIZATION_MARKER):
        if not _is_regular_knowledge_file(root.stat(INITIALIZATION_MARKER)):
            raise OSError("학습 자료 초기화 표시는 일반 파일이어야 합니다.")
        return

    with open_knowledge_directory(bundled_directory) as bundle:
        defaults = [path for path in knowledge_json_files(bundle) if is_knowledge_file(path, root=bundle)]
        if not defaults:
            raise FileNotFoundError(f"No bundled knowledge JSON files in {bundled_directory}")

        budget = KnowledgeReadBudget()
        for source in defaults:
            if root.exists(source.name):
                continue
            try:
                contents = read_knowledge_bytes(source, budget, root=bundle)
            except (KnowledgeFileTooLarge, KnowledgeLibraryLimitExceeded) as exc:
                errors.add(source.name, str(exc))
                continue
            with root.stage(contents) as publish:
                try:
                    publish(source.name)
                except FileExistsError:
                    continue

    # A successfully initialized but empty library must stay empty on restart.
    with suppress(FileExistsError):
        root.create_empty(INITIALIZATION_MARKER)


def read_knowledge_document(
    path: Path, budget: KnowledgeReadBudget | None = None, *, root: KnowledgeDirectory | None = None
) -> tuple[KnowledgeDocument, bytes]:
    contents = read_knowledge_bytes(path, budget, root=root)
    return KnowledgeDocument.model_validate_json(contents.decode("utf-8")), contents


def check_knowledge_file_size(size: int) -> None:
    if size > MAX_KNOWLEDGE_FILE_BYTES:
        raise KnowledgeFileTooLarge("학습 자료 파일당 읽기 한도(2 MiB)를 초과합니다.")


def count_knowledge_concepts(document: KnowledgeDocument) -> int:
    return sum(len(unit.concepts) for unit in document.topic.units)


def read_knowledge_bytes(
    path: Path, budget: KnowledgeReadBudget | None = None, *, root: KnowledgeDirectory | None = None
) -> bytes:
    if root is None:
        with open_knowledge_directory(path.parent) as directory:
            return read_knowledge_bytes(path, budget, root=directory)
    if not is_knowledge_file(path, root=root):
        raise _NotKnowledgeFile("학습 폴더의 일반 JSON 파일만 읽을 수 있습니다.")
    with os.fdopen(root.open_read(path.name), "rb", buffering=0) as source:
        metadata = os.fstat(source.fileno())
        if not _is_regular_knowledge_file(metadata):
            raise FileNotFoundError("학습 폴더의 일반 JSON 파일만 읽을 수 있습니다.")
        size = metadata.st_size
        check_knowledge_file_size(size)
        if budget is not None:
            budget.consume(size)
        contents = source.read(size)
        current = os.fstat(source.fileno())
        if len(contents) != size or current.st_size != size or current.st_mtime_ns != metadata.st_mtime_ns:
            raise OSError("읽는 중 학습 자료 파일이 변경되었습니다.")
        return contents


def load_knowledge(
    directory: Path | None = None,
    *,
    bundled_directory: Path | None = BUNDLED_KNOWLEDGE_DIR,
) -> KnowledgeLibrary:
    if directory is None:
        directory = user_knowledge_directory()
    errors = _KnowledgeErrors()
    documents: dict[Path, KnowledgeDocument] = {}
    topic_files: dict[str, list[Path]] = defaultdict(list)
    budget = KnowledgeReadBudget()
    with ExitStack() as stack:
        try:
            root = stack.enter_context(open_knowledge_directory(directory, create=bundled_directory is not None))
        except OSError as exc:
            errors.add(directory.name, str(exc))
            return KnowledgeLibrary((), errors.report())
        if bundled_directory is not None:
            _initialize_knowledge(root, bundled_directory, errors)
        for path in knowledge_json_files(root):
            try:
                document, _ = read_knowledge_document(path, budget, root=root)
            except _NotKnowledgeFile:
                continue
            except ValidationError as exc:
                errors.validation(path.name, exc)
                continue
            except (OSError, UnicodeError) as exc:
                errors.add(path.name, str(exc))
                continue
            documents[path] = document
            topic_files[document.topic.id].append(path)

    for topic_id, paths in topic_files.items():
        if len(paths) < 2:
            continue
        examples = ", ".join(path.name[:80] for path in paths[:DUPLICATE_FILENAME_EXAMPLES])
        shown_id = repr(topic_id[:80])[:80]
        errors.add("", f"topic.id: 중복 ID {shown_id} (총 {len(paths):,}개 파일). 예시: {examples}")
        for path in paths:
            del documents[path]

    accepted = []
    remaining_concepts = MAX_KNOWLEDGE_CONCEPTS
    for path, document in documents.items():
        count = count_knowledge_concepts(document)
        if count > remaining_concepts:
            errors.add(path.name, f"학습 자료 전체 개념 한도({MAX_KNOWLEDGE_CONCEPTS}개)를 초과합니다.")
            continue
        accepted.append(document)
        remaining_concepts -= count
    return KnowledgeLibrary(tuple(accepted), errors.report())


def knowledge_json_files(root: KnowledgeDirectory) -> list[Path]:
    return sorted(root.path / name for name in root.names() if Path(name).suffix.lower() == ".json")


def is_knowledge_file(path: Path, *, root: KnowledgeDirectory) -> bool:
    if path.suffix.lower() != ".json" or (os.name == "nt" and ntpath.isreserved(path.name)):
        return False
    try:
        return _is_regular_knowledge_file(root.stat(path.name))
    except FileNotFoundError:
        return False


def _is_regular_knowledge_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and not _is_reparse_point(metadata)
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
