"""Windows entry point for the CS Interview Bot desktop shell."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from threading import Lock

import webview

from bot.evaluation import (
    EvaluationProvider,
    EvaluationRequest,
    EvaluationResponse,
    EvaluationAuthenticationError,
    InitialQuestionProvider,
)
from bot.gemini_provider import GeminiClient, GeminiProvider
from bot.interview_session import InterviewSessionEngine, SessionSnapshot
from bot.knowledge_editor import KnowledgeEditor
from bot.knowledge_loader import BUNDLED_KNOWLEDGE_DIR, KnowledgeLibrary, load_knowledge, user_knowledge_directory
from bot.knowledge_parser import KnowledgeParseError, parse_knowledge_text
from bot.settings_store import PublicSettings, SettingsError, SettingsStore, user_data_directory


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
APP_DOCUMENT = FRONTEND / "dist" / "index.html"
APP_HOST = "cs-interview-bot.invalid"
WINDOW_TITLE = os.environ.get("CS_INTERVIEW_BOT_WINDOW_TITLE", "CS Interview Bot")


class _DemoProvider:
    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        return EvaluationResponse(
            correct_points=[],
            missing_points=["예시 평가입니다. 답변에 핵심 개념과 구체적인 사례를 포함했는지 확인해 보세요."],
            incorrect_points=[],
            follow_up_question="이 개념이 적용되는 구체적인 예를 설명해 주세요." if not request.history else None,
        )


class _SavedGeminiProvider:
    def __init__(self, model: str, settings: SettingsStore, client: GeminiClient) -> None:
        self._model = model
        self._settings = settings
        self._client = client

    def _provider(self) -> GeminiProvider:
        try:
            key = self._settings.get_key_for_provider("gemini")
        except SettingsError:
            raise EvaluationAuthenticationError("저장된 Gemini 키를 읽지 못했습니다. 키를 다시 등록해 주세요.") from None
        return GeminiProvider(self._model, key or "", client=self._client)

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        return self._provider().evaluate(request)

    def generate_initial_question(self, topic: str, unit: str, concept: str) -> str:
        return self._provider().generate_initial_question(topic, unit, concept)


class PythonBridge:
    """Methods exposed to the React window through pywebview."""

    def __init__(
        self,
        *,
        provider: EvaluationProvider | None = None,
        settings_store: SettingsStore | None = None,
        knowledge_directory: Path | None = None,
        bundled_knowledge_directory: Path | None = BUNDLED_KNOWLEDGE_DIR,
    ) -> None:
        directory = knowledge_directory or user_knowledge_directory()
        self._knowledge_directory = directory
        self._knowledge: KnowledgeLibrary = load_knowledge(
            directory,
            bundled_directory=bundled_knowledge_directory,
        )
        self._knowledge_editor = KnowledgeEditor(directory)
        self._knowledge_lock = Lock()
        self._last_reload_id: str | None = None
        self._session = InterviewSessionEngine.from_documents(self._knowledge.documents)
        self._injected_provider = provider
        self._provider = provider if provider is not None else _DemoProvider()
        self._settings = settings_store or SettingsStore(user_data_directory())
        self._gemini_client = GeminiClient()

    def interview_state(self, include_library: bool = False) -> dict:
        if not include_library:
            return self._interview_response(self._session.snapshot(include_scope=True))
        with self._knowledge_lock:
            return {
                **self._interview_response(self._session.snapshot(include_scope=True)),
                "library": self.learning_data(),
                "reload_id": self._last_reload_id,
            }

    def interview_busy(self) -> bool:
        return self._session.is_busy()

    def _interview_response(self, snapshot: SessionSnapshot, ready_mode: str | None = None) -> dict:
        mode = snapshot.provider_id or ready_mode or ("gemini" if self._ready_gemini_model() is not None else "fake")
        response = {field.name: getattr(snapshot, field.name) for field in fields(snapshot)}
        if snapshot.scope_ids is None:
            del response["scope_ids"]
        if not snapshot.question_changed:
            del response["question_changed"]
        if snapshot.submission_blocked is None:
            del response["submission_blocked"]
        response["evaluation_mode"] = mode
        response["question_generation_available"] = self._can_generate_questions(mode)
        return response

    def learning_data(self) -> dict:
        return self._learning_data(self._knowledge)

    @staticmethod
    def _learning_data(library: KnowledgeLibrary) -> dict:
        return {
            "topics": library.study_documents(),
            "errors": [
                {"filename": error.filename, "detail": error.detail}
                for error in library.errors
            ],
        }

    def reload_knowledge(self, reload_id: str | None = None) -> dict:
        with self._knowledge_lock:
            if reload_id is not None and reload_id == self._last_reload_id:
                return {"library": self.learning_data(), "interview": self.interview_state()}
            library = load_knowledge(self._knowledge_directory, bundled_directory=None)
            data = self._learning_data(library)
            snapshot = self._session.reload_documents(library.documents)
            self._knowledge = library
            self._last_reload_id = reload_id
            return {"library": data, "interview": self._interview_response(snapshot)}

    def list_knowledge_files(self) -> dict:
        return self._knowledge_editor.list_files()

    def parse_knowledge_text(self, topic_name: str, body: str) -> dict:
        try:
            document = parse_knowledge_text(topic_name, body)
        except KnowledgeParseError as error:
            return {"ok": False, "error": error.as_dict()}
        return {"ok": True, "document": document.model_dump(mode="json")}

    def open_knowledge_file(self, filename: str) -> dict:
        return self._knowledge_editor.open_file(filename)

    def save_knowledge_file(
        self,
        document: dict,
        filename: str | None = None,
        revision: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        return self._knowledge_editor.save_file(document, filename, revision, overwrite)

    def set_interview_scope(self, concept_ids: list[str]) -> dict:
        return self._interview_response(self._session.set_scope(concept_ids))

    def restart_interview(self) -> dict:
        return self._interview_response(self._session.restart())

    def _ready_gemini_model(self, settings: PublicSettings | None = None) -> str | None:
        if self._injected_provider is not None:
            return None
        if settings is None:
            try:
                settings = self._settings.public_settings()
            except SettingsError:
                return None
        if settings.provider == "gemini" and settings.key_registered["gemini"]:
            return settings.models["gemini"]
        return None

    def redraw_question(self) -> dict:
        self._session.redraw_question()
        return self.interview_state()

    def submit_answer(
        self, answer: str, expected_mode: str = "fake", record_start: int = 0,
        expected_question_version: int | None = None,
    ) -> dict:
        provider_id = self._session.provider_id
        model = self._ready_gemini_model() if provider_id is None else None
        mode = provider_id or ("gemini" if model is not None else "fake")
        provider = None
        if model is not None:
            provider = self._saved_gemini_provider(model)
        elif provider_id is None:
            provider = self._provider
        snapshot = self._session.submit(
            answer, mode, provider, record_start=record_start,
            expected_question_version=expected_question_version, expected_mode=expected_mode,
        )
        return self._interview_response(snapshot, ready_mode=mode if snapshot.submission_blocked else None)

    def _can_generate_questions(self, mode: str) -> bool:
        return mode == "gemini" or isinstance(self._injected_provider, InitialQuestionProvider)

    def _saved_gemini_provider(self, model: str) -> _SavedGeminiProvider:
        return _SavedGeminiProvider(model, self._settings, self._gemini_client)

    def retry_answer(self, record_start: int = 0) -> dict:
        return self._interview_response(self._session.retry(record_start=record_start))

    def question_generation(self) -> dict[str, int | str] | None:
        return self._session.question_generation()

    def next_concept(self, record_start: int = 0, generation_token: int | None = None) -> dict:
        return self._interview_response(self._session.skip_concept(
            record_start=record_start, expected_generation=generation_token,
        ))

    def end_interview(self, record_start: int = 0) -> dict:
        return self._interview_response(self._session.end(record_start=record_start))

    def _settings_response(self, action: Callable[[], PublicSettings]) -> dict:
        try:
            settings = action()
            mode = self._session.provider_id or ("gemini" if self._ready_gemini_model(settings) is not None else "fake")
            return {
                "ok": True,
                "evaluation_mode": mode,
                "question_generation_available": self._can_generate_questions(mode),
                "settings": {
                    "provider": settings.provider,
                    "models": settings.models,
                    "key_registered": settings.key_registered,
                },
            }
        except SettingsError as error:
            return {"ok": False, "error": error.code}
        except Exception:
            return {"ok": False, "error": "settings_unavailable"}

    def settings_state(self) -> dict:
        return self._settings_response(self._settings.public_settings)

    def save_provider_settings(self, provider: str, models: dict[str, str]) -> dict:
        return self._settings_response(lambda: self._settings.save_preferences(provider, models))

    def save_api_key(self, provider: str, key: str) -> dict:
        return self._settings_response(lambda: self._settings.set_key(provider, key))

    def delete_api_key(self, provider: str) -> dict:
        return self._settings_response(lambda: self._settings.delete_key(provider))

    def open_knowledge_folder(self) -> dict:
        try:
            self._settings.open_knowledge_folder()
            return {"ok": True}
        except SettingsError as error:
            return {"ok": False, "error": error.code}

    def _close(self) -> None:
        try:
            self._session.end()
        finally:
            self._gemini_client.close()


def _show_startup_error(message: str) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "CS Interview Bot", 0x10)
        except Exception:
            pass


def main() -> int:
    if os.name != "nt":
        _show_startup_error("이 개발 실행 진입점은 Windows에서 사용하도록 구성되어 있습니다.")
        return 1

    if not APP_DOCUMENT.is_file():
        message = "프런트엔드 빌드 파일(frontend/dist/index.html)이 없습니다."
        if not getattr(sys, "frozen", False):
            message += " frontend 폴더에서 npm.cmd ci와 npm.cmd run build를 실행한 뒤 다시 시작하세요."
        _show_startup_error(message)
        return 1

    try:
        if getattr(sys, "frozen", False):
            release_root = Path(sys.executable).resolve().parent
            data = release_root / "data"
            bridge = PythonBridge(
                knowledge_directory=data,
                bundled_knowledge_directory=None,
                settings_store=SettingsStore(
                    release_root / "config",
                    knowledge_directory=data,
                    key_relative_path=Path("api-keys.enc"),
                ),
            )
        else:
            bridge = PythonBridge()

        _run_window(APP_DOCUMENT, bridge)
        return 0
    except Exception as error:
        _show_startup_error(f"앱을 시작하지 못했습니다: {error}")
        return 1


def _run_window(document: Path, bridge: PythonBridge) -> None:
    document = Path(document).resolve()
    url = f"https://{APP_HOST}/{document.name}"
    methods = (
        bridge.interview_state,
        bridge.interview_busy,
        bridge.learning_data,
        bridge.reload_knowledge,
        bridge.parse_knowledge_text,
        bridge.list_knowledge_files,
        bridge.open_knowledge_file,
        bridge.save_knowledge_file,
        bridge.set_interview_scope,
        bridge.restart_interview,
        bridge.redraw_question,
        bridge.submit_answer,
        bridge.retry_answer,
        bridge.question_generation,
        bridge.next_concept,
        bridge.end_interview,
        bridge.settings_state,
        bridge.save_provider_settings,
        bridge.save_api_key,
        bridge.delete_api_key,
        bridge.open_knowledge_folder,
    )
    allowed_methods = {method.__name__ for method in methods}
    restore_backend = None
    startup_error = None

    try:
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            min_size=(860, 620),
            background_color="#f7f8fa",
        )
        window.expose(*methods)

        def configure_backend(renderer: str) -> bool:
            nonlocal restore_backend, startup_error
            try:
                if renderer != "edgechromium":
                    raise RuntimeError("Microsoft Edge WebView2 Runtime이 필요합니다.")
                restore_backend = _configure_webview2(window, url, document.parent, allowed_methods)
            except Exception as error:
                # Returning False aborts pywebview before it creates the native window.
                startup_error = error
                return False
            return True

        window.events.initialized += configure_backend
        webview.start(gui="edgechromium")
        if startup_error is not None:
            raise startup_error
    finally:
        if restore_backend is not None:
            restore_backend()
        bridge._close()


def _configure_webview2(window, url: str, assets: Path, allowed_methods: set[str]) -> Callable[[], None]:
    from webview.platforms import edgechromium
    from webview.util import js_bridge_call
    from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind

    # pywebview 6.2.1 binds these callbacks when it constructs EdgeChrome.
    backend = edgechromium.EdgeChrome

    class AppWebView(backend):
        app_assets_mapped = False

        def on_webview_ready(self, sender, args):
            if self.pywebview_window is window and args.IsSuccess:
                sender.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    APP_HOST, str(assets), CoreWebView2HostResourceAccessKind.Deny
                )
                self.app_assets_mapped = True
            super().on_webview_ready(sender, args)

        def on_navigation_start(self, sender, args):
            if self.pywebview_window is window and str(args.Uri) != url:
                args.Cancel = True
                return
            super().on_navigation_start(sender, args)

        def on_script_notify(self, sender, args):
            if self.pywebview_window is not window or not self.app_assets_mapped or str(args.Source) != url:
                return
            try:
                message = json.loads(args.WebMessageAsJson)
                if not isinstance(message, list) or len(message) != 3:
                    return
                name, serialized_params, callback_id = message
                params = json.loads(serialized_params)
            except (TypeError, ValueError):
                return
            if (
                isinstance(name, str) and name in allowed_methods
                and isinstance(params, list)
                # pywebview interpolates this ID into a JavaScript string on reply.
                and isinstance(callback_id, str) and re.fullmatch(r"[0-9.e-]{0,32}", callback_id)
            ):
                js_bridge_call(window, name, params, callback_id)

    edgechromium.EdgeChrome = AppWebView

    def restore_backend() -> None:
        edgechromium.EdgeChrome = backend

    return restore_backend
