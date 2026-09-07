import json
import unittest

from sources import OPERACITY_UPCOMING_URL, parse_operacity, parse_yurakucho


class OperaCityParserTests(unittest.TestCase):
    def _html(self, more=""):
        return f"""
        <section class="p-exhList__section">
          <h2 class="c-exhHeading">2026.10.17［土］ - 12.20［日］</h2>
          <div class="p-exhList__item">
            <div class="p-exhList__info">
              <h3 class="p-exhList__headerTitle">Example Exhibition</h3>
              <span class="p-exhList__headerPlace">ギャラリー 1</span>
              {more}
            </div>
          </div>
        </section>
        """

    def test_uses_upcoming_page_when_no_detail_page_exists(self):
        events = parse_operacity(self._html(), "2026-07-25")

        self.assertEqual(events[0]["url"], OPERACITY_UPCOMING_URL)

    def test_prefers_an_individual_detail_link_when_present(self):
        events = parse_operacity(
            self._html(
                '<div class="p-exhList__more"><a href="/ag/exh/example/">More</a></div>'
            ),
            "2026-07-25",
        )

        self.assertEqual(
            events[0]["url"],
            "https://www.operacity.jp/ag/exh/example/",
        )


class YurakuchoParserTests(unittest.TestCase):
    def _json(self, **overrides):
        item = {
            "title": "Example Exhibition",
            "image": {"path": "/cms-data/images/exhibitions/example/pc.png"},
            "image_sp": None,
            "gallery": "gallery_a_b",
            "start_date": "2026-09-16",
            "end_date": "2026-12-07 18:00:00",
            "supplement": None,
            "official": None,
            "detail_show_flag": False,
            "dir_name": None,
        }
        item.update(overrides)
        return json.dumps([item])

    def test_uses_official_url_when_no_detail_page(self):
        events = parse_yurakucho(self._json(official="https://example.co.jp/"), "2026-09-07")

        self.assertEqual(events[0]["url"], "https://example.co.jp/")

    def test_uses_detail_page_when_flagged(self):
        events = parse_yurakucho(
            self._json(detail_show_flag=True, dir_name="example"), "2026-09-07"
        )

        self.assertEqual(events[0]["url"], "https://yurakuchomuseum.jp/exhibitions/example/")

    def test_falls_back_to_list_page_when_no_url(self):
        events = parse_yurakucho(self._json(), "2026-09-07")

        self.assertEqual(events[0]["url"], "https://yurakuchomuseum.jp/exhibitions/")

    def test_drops_ended_exhibitions(self):
        events = parse_yurakucho(self._json(end_date="2026-09-01 18:00:00"), "2026-09-07")

        self.assertEqual(events, [])

    def test_venue_gallery_and_closing_time(self):
        events = parse_yurakucho(self._json(), "2026-09-07")

        self.assertEqual(events[0]["venue"], "YURAKUCHO MUSEUM Gallery A&B")
        self.assertEqual(events[0]["end_time"], "18:00")
        self.assertEqual(events[0]["image"], "https://yurakuchomuseum.jp/cms-data/images/exhibitions/example/pc.png")


if __name__ == "__main__":
    unittest.main()
