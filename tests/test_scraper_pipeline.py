import os
import unittest
from unittest.mock import patch

import scraper


class ScraperPipelineTests(unittest.TestCase):
    ENV = {
        "LLM_MODEL": "primary",
        "LLM_FALLBACK_MODELS": '["fallback"]',
    }

    def test_fingerprint_changes_with_model_policy(self):
        with patch.dict(os.environ, self.ENV, clear=False):
            first = scraper._content_fingerprint("same input")
        with patch.dict(
            os.environ,
            {"LLM_MODEL": "different", "LLM_FALLBACK_MODELS": '["fallback"]'},
            clear=False,
        ):
            second = scraper._content_fingerprint("same input")

        self.assertNotEqual(first, second)

    def test_fingerprint_changes_with_prompt(self):
        with patch.dict(os.environ, self.ENV, clear=False):
            first = scraper._content_fingerprint("same input")
            with patch.object(scraper, "ENRICH_PROMPT_BASIC", "changed prompt"):
                second = scraper._content_fingerprint("same input")

        self.assertNotEqual(first, second)

    def test_finalize_assigns_venue_key_and_reports_unknown_venue(self):
        events = [
            {"title": "Known", "venue": "Museum Room A"},
            {"title": "Unknown", "venue": "New Gallery"},
        ]
        meta = {"warnings": []}

        finalized = scraper._finalize_events(
            events,
            {"url": "https://example.test"},
            meta,
            (["Museum"], {}),
        )

        self.assertEqual(finalized[0]["venue_key"], "Museum")
        self.assertIsNone(finalized[1]["venue_key"])
        self.assertTrue(any("New Gallery" in warning for warning in meta["warnings"]))


if __name__ == "__main__":
    unittest.main()
