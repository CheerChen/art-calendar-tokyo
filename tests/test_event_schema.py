import unittest

from event_schema import EVENT_FIELDS, make_event, normalize_event, normalize_events


class EventSchemaTests(unittest.TestCase):
    def test_factory_always_returns_the_complete_schema(self):
        event = make_event("Exhibition", "Museum", image="https://example.test/a.jpg")

        self.assertEqual(tuple(event.keys()), EVENT_FIELDS)
        self.assertFalse(event["reservation_required"])
        self.assertFalse(event["detail_fetched"])
        self.assertIsNone(event["closed_days"])

    def test_normalizer_repairs_invalid_fields_and_reports_them(self):
        event, warnings = normalize_event({
            "title": "  Exhibition  ",
            "venue": " Museum ",
            "start_date": "2026-02-30",
            "end_date": "2026-03-01",
            "start_time": "25:00",
            "reservation_required": "false",
            "recommendation": ["excellent"],
        })

        self.assertEqual(event["title"], "Exhibition")
        self.assertEqual(event["venue"], "Museum")
        self.assertIsNone(event["start_date"])
        self.assertIsNone(event["start_time"])
        self.assertFalse(event["reservation_required"])
        self.assertEqual(event["recommendation"], "normal")
        self.assertGreaterEqual(len(warnings), 4)

    def test_empty_titles_and_non_objects_are_dropped(self):
        events, warnings = normalize_events([{"title": " "}, "bad"])

        self.assertEqual(events, [])
        self.assertTrue(any("title is empty" in warning for warning in warnings))
        self.assertTrue(any("must be an object" in warning for warning in warnings))

    def test_end_date_before_start_date_is_cleared(self):
        event, warnings = normalize_event({
            "title": "Exhibition",
            "venue": "Museum",
            "start_date": "2026-08-02",
            "end_date": "2026-08-01",
        })

        self.assertIsNone(event["end_date"])
        self.assertTrue(any("precedes start_date" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
