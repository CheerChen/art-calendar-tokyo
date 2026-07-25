import unittest

from venue_matching import build_matchers, match_venue, normalize_venue


class VenueMatchingTests(unittest.TestCase):
    def setUp(self):
        self.venues = {
            "Museum": {"lat": 1, "lng": 2, "aliases": ["Museum Alias"]},
            "Museum Annex": {"lat": 3, "lng": 4},
        }
        self.canonicals, self.aliases = build_matchers(self.venues)

    def test_alias_match_wins(self):
        self.assertEqual(
            match_venue("Museum Alias", self.canonicals, self.aliases),
            "Museum",
        )

    def test_longest_contained_canonical_wins(self):
        self.assertEqual(
            match_venue("Museum Annex Gallery 2", self.canonicals, self.aliases),
            "Museum Annex",
        )

    def test_unknown_venue_has_no_public_key_but_is_preserved_for_maintenance(self):
        self.assertIsNone(match_venue("New Gallery", self.canonicals, self.aliases))
        self.assertEqual(
            normalize_venue("New Gallery", self.canonicals, self.aliases),
            "New Gallery",
        )


if __name__ == "__main__":
    unittest.main()
