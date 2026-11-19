"""Runtime model settings and the authenticated OpenAI Responses adapter."""
import json
import logging
import os
from pathlib import Path
import shlex
from http.client import HTTPException
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .usage import output_limit, record_usage, request_started


def read_assignments(path):
    values = {}
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.lstrip().startswith("#"):
            parts = shlex.split(value, comments=True)
            if len(parts) == 1:
                values[name.strip().removeprefix("export ")] = parts[0]
    return values


settings_path = Path(__file__).resolve().parents[4] / ".env"
if settings_path.exists():
    for name, value in read_assignments(settings_path).items():
        if name.startswith("LUMINA_"):
            os.environ.setdefault(name, value)

PROVIDER = os.getenv("LUMINA_PROVIDER", "qwen")
MODEL = os.getenv("LUMINA_MODEL", "qwen3.8-27b-bf16")
REASONING = os.getenv("LUMINA_REASONING_EFFORT", "medium")


def model_identity():
    return [PROVIDER, MODEL, REASONING if PROVIDER == "openai" else "no-thinking"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_openai_key():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        path = os.getenv("LUMINA_OPENAI_ENV_FILE")
        if not path:
            raise ValueError("未配置 OpenAI Key 来源")
        values = read_assignments(path)
        key = values.get("OPENAI_API_KEY") or values.get("OpenAI_Key")
    if not key:
        raise ValueError("配置文件中未找到 OpenAI Key")
    return key


def read_api_response(response):
    if not response.headers.get("Content-Type", "").startswith("text/event-stream"):
        return json.load(response)
    for line in response:
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        event = json.loads(data)
        if event.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
            return event["response"]
        if event.get("type") == "error":
            raise ConnectionError("OpenAI stream returned an error")
    raise ConnectionError("OpenAI stream ended before completion")


def openai_completion(messages, tokens, response_format, length_error):
    payload = {
        "model": MODEL,
        "input": messages,
        "reasoning": {"effort": REASONING},
        # Reasoning and visible text share this budget.
        "max_output_tokens": output_limit(tokens + 6000),
        "store": False,
        "stream": True,
    }
    if response_format:
        if response_format["type"] == "json_schema":
            payload["text"] = {"format": {
                "type": "json_schema", **response_format["json_schema"],
            }}
        else:
            payload["text"] = {"format": response_format}
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + read_openai_key(), "Content-Type": "application/json"},
    )
    opener = build_opener(NoRedirect())
    for attempt in range(3):
        try:
            request_started()
            with opener.open(request, timeout=240) as response:
                result = read_api_response(response)
            break
        except HTTPError as error:
            code = error.code
            request_id = error.headers.get("x-request-id", "unavailable")
            error.close()
            if attempt == 2 or code not in {429, 500, 502, 503, 504}:
                raise OSError(f"OpenAI HTTP {code}, request {request_id}") from None
            time.sleep(2 ** (attempt + 1))
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            if attempt == 2:
                raise OSError("OpenAI connection failed after retry") from None
            time.sleep(2 ** (attempt + 1))
    record_usage(result.get("model", MODEL), result.get("usage"), result.get("service_tier", "default"))
    if result.get("status") == "incomplete":
        reason = (result.get("incomplete_details") or {}).get("reason")
        if reason == "max_output_tokens":
            raise length_error("OpenAI output token limit")
        raise ValueError(f"OpenAI incomplete response: {reason}")
    if result.get("status") != "completed":
        raise ValueError("OpenAI response did not complete")
    output = []
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise ValueError("OpenAI declined this request")
            if part.get("type") == "output_text":
                output.append(part["text"])
    text = "\n".join(output).strip()
    if not text:
        raise ValueError("OpenAI returned no answer")
    logging.info("Reader model=%s response=%s usage=%s", result.get("model"), result.get("id"), result.get("usage"))
    return text
