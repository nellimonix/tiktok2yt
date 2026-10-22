import unittest
from unittest.mock import Mock, patch

import ai_title


class TitleGenerationTests(unittest.TestCase):
    def test_requests_json_with_complete_response_budget(self):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"title": "Тестовое название"}'}}],
        }
        with patch.object(ai_title.requests, "post", return_value=response) as post:
            title = ai_title.generate_title(
                "Тестовая транскрипция", "test-key", "https://example.invalid",
                raise_on_failure=True,
            )

        self.assertEqual(title, "Тестовое название")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertEqual(payload["messages"][0]["content"], ai_title.SYSTEM_PROMPT)
        self.assertNotIn("model", payload)

    def test_configured_model_is_preserved(self):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"title": "Тестовое название"}'}}],
        }
        with patch.object(ai_title.requests, "post", return_value=response) as post:
            ai_title.generate_title(
                "Тестовая транскрипция", "test-key", "https://example.invalid",
                model="configured-model", raise_on_failure=True,
            )
        self.assertEqual(post.call_args.kwargs["json"]["model"], "configured-model")

    def test_incomplete_reasoning_response_remains_a_failure(self):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "Хм, нужно придумать название"}}],
        }
        with patch.object(ai_title.requests, "post", return_value=response) as post:
            with self.assertRaises(ai_title.TitleGenerationError):
                ai_title.generate_title(
                    "Тестовая транскрипция", "test-key", "https://example.invalid",
                    raise_on_failure=True,
                )
        self.assertEqual(post.call_count, ai_title.MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
