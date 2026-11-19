import json
from http.client import RemoteDisconnected
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.core.pipelines.novel import model_client
from src.core.pipelines.novel.batch import CompletionLengthError
from src.core.pipelines.novel.usage import CURRENT, OUTPUT_CAP, UsageLimitError, UsageMeter


class OpenAIAdapterTest(TestCase):
    def test_harness_output_cap_includes_reasoning_budget(self):
        opener=MagicMock()
        opener.open.return_value=self.response({"status":"completed","model":"gpt-6.1-sol",
            "output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}]})
        token=OUTPUT_CAP.set(3000)
        try:
            with patch.object(model_client,"build_opener",return_value=opener),patch.object(model_client,"read_openai_key",return_value="test-only"):
                model_client.openai_completion([],5000,None,CompletionLengthError)
        finally:
            OUTPUT_CAP.reset(token)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)["max_output_tokens"],3000)

    def test_transport_retry_cannot_bypass_harness_request_budget(self):
        opener=MagicMock()
        opener.open.side_effect=RemoteDisconnected("offline test")
        meter=UsageMeter(max_requests=1)
        token=CURRENT.set(meter)
        try:
            with patch.object(model_client,"build_opener",return_value=opener),patch.object(model_client,"read_openai_key",return_value="test-only"),patch.object(model_client.time,"sleep"):
                with self.assertRaises(UsageLimitError):
                    model_client.openai_completion([],100,None,CompletionLengthError)
        finally:
            CURRENT.reset(token)
        self.assertEqual(opener.open.call_count,1)
        self.assertFalse(meter.summary()["usage_complete"])

    def response(self, result):
        response = MagicMock()
        response.__enter__.return_value.headers = {"Content-Type": "application/json"}
        response.__enter__.return_value.read.return_value = json.dumps(result).encode()
        return response

    def test_responses_request_and_schema(self):
        opener = MagicMock()
        opener.open.return_value = self.response({
            "status": "completed", "model": "gpt-6.1-sol",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "回答"}]}],
        })
        schema = {"type": "json_schema", "json_schema": {
            "name": "answer", "strict": True, "schema": {"type": "object"},
        }}
        with patch.object(model_client, "build_opener", return_value=opener):
            with patch.object(model_client, "read_openai_key", return_value="test-only-placeholder"):
                value = model_client.openai_completion(
                    [{"role": "user", "content": "问题"}], 2000, schema, CompletionLengthError
                )
        self.assertEqual(value, "回答")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        payload = json.loads(request.data)
        self.assertEqual(payload["max_output_tokens"], 8000)
        self.assertFalse(payload["store"])
        self.assertNotIn("temperature", payload)
        self.assertNotIn("chat_template_kwargs", payload)
        self.assertEqual(payload["text"]["format"]["name"], "answer")

    def test_incomplete_output_uses_existing_retry_signal(self):
        opener = MagicMock()
        opener.open.return_value = self.response({
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        })
        with patch.object(model_client, "build_opener", return_value=opener):
            with patch.object(model_client, "read_openai_key", return_value="test-only-placeholder"):
                with self.assertRaises(CompletionLengthError):
                    model_client.openai_completion([], 100, None, CompletionLengthError)

    def test_remote_disconnect_is_retried(self):
        opener = MagicMock()
        opener.open.side_effect = [
            RemoteDisconnected("connection closed"),
            self.response({
                "status": "completed", "model": "gpt-6.1-sol",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "恢复"}]}],
            }),
        ]
        with patch.object(model_client, "build_opener", return_value=opener):
            with patch.object(model_client, "read_openai_key", return_value="test-only-placeholder"):
                with patch.object(model_client.time, "sleep"):
                    result = model_client.openai_completion([], 100, None, CompletionLengthError)
        self.assertEqual(result, "恢复")
        self.assertEqual(opener.open.call_count, 2)

    def test_stream_usage_is_read_from_terminal_response(self):
        response = MagicMock()
        response.headers = {"Content-Type": "text/event-stream"}
        result = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
        response.__iter__.return_value = iter([
            b'data: {"type":"response.created"}\n',
            ("data: " + json.dumps({"type": "response.completed", "response": result}) + "\n").encode(),
        ])
        self.assertEqual(model_client.read_api_response(response), result)

    def test_stream_disconnect_cannot_publish_partial_answer(self):
        response = MagicMock()
        response.headers = {"Content-Type": "text/event-stream"}
        response.__iter__.return_value = iter([b'data: {"type":"response.created"}\n'])
        with self.assertRaises(ConnectionError):
            model_client.read_api_response(response)
