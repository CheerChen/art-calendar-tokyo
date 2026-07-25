import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError

import extract


def quota_error():
    request = httpx.Request("POST", "https://example.test/chat/completions")
    response = httpx.Response(403, request=request)
    return APIStatusError(
        "free tier exhausted",
        response=response,
        body={"code": "AllocationQuota.FreeTierOnly"},
    )


def completion(content="[]"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def __init__(self, responses):
        self.calls = []
        self._responses = iter(responses)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        self.calls.append(kwargs["model"])
        result = next(self._responses)
        if isinstance(result, Exception):
            raise result
        return result


class ModelFallbackTests(unittest.TestCase):
    ENV = {
        "LLM_MODEL": "qwen3.6-plus",
        "LLM_FALLBACK_MODELS": (
            '["qwen3.7-flash","qwen3.7-flash-2026-07-15"]'
        ),
    }

    def test_falls_through_ordered_models_and_remembers_selection(self):
        client = FakeClient([quota_error(), quota_error(), completion(), completion()])

        with patch.dict(os.environ, self.ENV, clear=False):
            self.assertEqual(extract._call_llm(client, "system", "user"), [])
            self.assertEqual(extract._call_llm(client, "system", "user"), [])

        self.assertEqual(
            client.calls,
            [
                "qwen3.6-plus",
                "qwen3.7-flash",
                "qwen3.7-flash-2026-07-15",
                "qwen3.7-flash-2026-07-15",
            ],
        )

    def test_does_not_fallback_for_other_403_errors(self):
        request = httpx.Request("POST", "https://example.test/chat/completions")
        response = httpx.Response(403, request=request)
        other_error = APIStatusError(
            "permission denied",
            response=response,
            body={"code": "AccessDenied"},
        )
        client = FakeClient([other_error])

        with patch.dict(os.environ, self.ENV, clear=False):
            with self.assertRaises(APIStatusError):
                extract._call_llm(client, "system", "user")

        self.assertEqual(client.calls, ["qwen3.6-plus"])

    def test_empty_fallback_list_disables_fallback(self):
        client = FakeClient([quota_error()])

        with patch.dict(
            os.environ,
            {"LLM_MODEL": "qwen3.6-plus", "LLM_FALLBACK_MODELS": "[]"},
            clear=False,
        ):
            with self.assertRaises(APIStatusError):
                extract._call_llm(client, "system", "user")

        self.assertEqual(client.calls, ["qwen3.6-plus"])


if __name__ == "__main__":
    unittest.main()
