import unittest

from sources import OPERACITY_UPCOMING_URL, parse_operacity


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


if __name__ == "__main__":
    unittest.main()
