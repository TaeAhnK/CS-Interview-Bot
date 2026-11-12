"""Gemini Developer API adapter for the shared interview evaluation contract."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

import httpx
from pydantic import ValidationError

from bot.evaluation import (
    EVALUATION_INSTRUCTIONS,
    EvaluationAuthenticationError,
    EvaluationConnectionError,
    EvaluationError,
    EvaluationRequest,
    EvaluationResponse,
    EvaluationResponseFormatError,
    EvaluationTimeoutError,
)


_RESPONSE_SCHEMA = EvaluationResponse.model_json_schema()
_RESPONSE_SCHEMA["properties"]["follow_up_question"].pop("default", None)
_RESPONSE_SCHEMA["required"].append("follow_up_question")
_REQUEST_TIMEOUT = 60


class GeminiClient:
    """Share a client across requests and defer closing it until they finish."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.Client | None = None
        self._lock = Lock()
        self._active_requests = 0
        self._closed = False

    @contextmanager
    def request_client(self) -> Iterator[httpx.Client]:
        with self._lock:
            if self._closed:
                raise EvaluationConnectionError("Gemini 연결이 종료되었습니다.")
            if self._client is None:
                self._client = httpx.Client(timeout=_REQUEST_TIMEOUT, transport=self._transport)
            client = self._client
            self._active_requests += 1
        try:
            yield client
        finally:
            with self._lock:
                self._active_requests -= 1
                self._close_if_idle()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._close_if_idle()

    def _close_if_idle(self) -> None:
        if self._closed and self._active_requests == 0 and self._client is not None:
            client, self._client = self._client, None
            client.close()


class GeminiProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        client: GeminiClient | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("Pass a client or a transport")
        self._model = model.removeprefix("models/")
        self._api_key = api_key
        self._transport = transport
        self._client = client

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        if not self._api_key:
            raise EvaluationAuthenticationError("설정에서 Gemini API 키를 등록해 주세요.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self._model):
            raise EvaluationError("설정에서 올바른 Gemini 모델 ID를 입력해 주세요.")

        try:
            client_context = (
                self._client.request_client() if self._client is not None
                else httpx.Client(timeout=_REQUEST_TIMEOUT, transport=self._transport)
            )
            with client_context as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json={
                        "systemInstruction": {"parts": [{"text": EVALUATION_INSTRUCTIONS}]},
                        "contents": [{"role": "user", "parts": [{"text": request.model_dump_json()}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "responseJsonSchema": _RESPONSE_SCHEMA,
                        },
                    },
                )
        except httpx.TimeoutException:
            raise EvaluationTimeoutError("Gemini 응답 시간이 초과되었습니다.") from None
        except httpx.RequestError:
            raise EvaluationConnectionError("Gemini에 연결하지 못했습니다.") from None

        if response.is_error:
            self._raise_api_error(response)
        try:
            candidate = response.json()["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise ValueError("Incomplete generation")
            parts = candidate["content"]["parts"]
            content = "".join(part["text"] for part in parts if "text" in part and not part.get("thought"))
            return EvaluationResponse.model_validate_json(content)
        except (ValueError, ValidationError, TypeError, AttributeError, KeyError, IndexError):
            raise EvaluationResponseFormatError("Gemini 평가 응답을 읽지 못했습니다.") from None

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        try:
            error = response.json().get("error", {})
            invalid_key = any(
                detail.get("reason") == "API_KEY_INVALID" for detail in error.get("details", [])
            )
        except (ValueError, TypeError, AttributeError):
            invalid_key = False
        status = response.status_code
        if status in (401, 403) or invalid_key:
            raise EvaluationAuthenticationError("Gemini API 키 또는 모델 접근 권한을 확인해 주세요.")
        if status == 404:
            raise EvaluationError("설정한 Gemini 모델을 사용할 수 없습니다. 모델 ID를 확인해 주세요.")
        if status == 429:
            raise EvaluationError("Gemini 사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.")
        if status == 408:
            raise EvaluationTimeoutError("Gemini 응답 시간이 초과되었습니다.")
        if status >= 500:
            raise EvaluationConnectionError("Gemini 서비스에 연결하지 못했습니다.")
        raise EvaluationError("Gemini가 평가 요청을 거부했습니다. 모델 설정을 확인해 주세요.")
