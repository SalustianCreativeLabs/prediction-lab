"""Testes de parsing de discover.py e extract_rules.py.

Fixtures copiadas de respostas reais da Gamma API (2026-07-21).
Rodar: python -m unittest discover -s tests  (ou: python -m unittest tests.test_parsing)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from discover import parse_bucket_markets, parse_event_title
from extract_rules import parse_description

NYC_DESCRIPTION = (
    "This market will resolve to the temperature range that contains the highest "
    "temperature recorded at the LaGuardia Airport Station in degrees Fahrenheit "
    "on 21 Jul '26.\n\nThe resolution source for this market will be information "
    "from Wunderground, specifically the highest temperature recorded for all "
    "times on this day for the LaGuardia Airport Station, available here: "
    "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA.\n\n"
    "The resolution source for this market measures temperatures to whole degrees "
    "Fahrenheit (eg, 21°F). Thus, this is the level of precision that will be "
    "used when resolving the market.\n\nRevisions to temperatures recorded within "
    "this market's timeframe will be considered until the first datapoint for the "
    "following date has been published, after which any alterations will not be "
    "considered."
)

LONDON_DESCRIPTION = (
    "This market will resolve to the temperature range that contains the highest "
    "temperature recorded at the London City Airport Station in degrees Celsius "
    "on 21 Jul '26.\n\nThe resolution source for this market will be information "
    "from Wunderground, specifically the highest temperature recorded for all "
    "times on this day for the London City Airport Station, available here: "
    "https://www.wunderground.com/history/daily/gb/london/EGLC.\n\n"
    "The resolution source for this market measures temperatures to whole degrees "
    "Celsius (eg, 9°C)."
)

HONG_KONG_DESCRIPTION = (
    "This market will resolve to the temperature range that contains the highest "
    "temperature recorded by the Hong Kong Observatory in degrees Celsius on "
    "21 Jul '26.\n\nThe resolution source for this market will be information from "
    "the Hong Kong Observatory, specifically the \"Absolute Daily Max (deg. C)\" "
    "the specified date once information is finalized in the relevant \"Daily "
    "Extract\", available here: https://www.weather.gov.hk/en/cis/climat.htm\n\n"
    "The resolution source for this market measures temperatures in Celsius to one "
    "decimal place (eg, 9.1°C).\n\nAny revisions to temperatures recorded after "
    "data is initially published for this market's timeframe will not be "
    "considered for this market's resolution."
)


class TestJsonGzipRoundtrip(unittest.TestCase):
    def test_gz_and_plain_roundtrip(self):
        import tempfile
        from _common import load_json, save_json
        data = {"cidade": "nyc", "membros": [80.1, 81.5], "ok": True}
        with tempfile.TemporaryDirectory() as td:
            for name in ("plain.json", "raw.json.gz"):
                path = Path(td) / name
                save_json(path, data)
                self.assertEqual(load_json(path), data)
            self.assertTrue((Path(td) / "raw.json.gz").read_bytes()
                            .startswith(b"\x1f\x8b"))  # magic gzip


class TestParseEventTitle(unittest.TestCase):
    def test_highest_nyc(self):
        parsed = parse_event_title("Highest temperature in NYC on July 21?",
                                   "highest-temperature-in-nyc-on-july-21-2026")
        self.assertEqual(parsed, {"side": "highest", "city": "NYC",
                                  "city_slug": "nyc", "target_date": "2026-07-21"})

    def test_lowest_multiword_city(self):
        parsed = parse_event_title("Lowest temperature in Hong Kong on July 22?",
                                   "lowest-temperature-in-hong-kong-on-july-22-2026")
        self.assertEqual(parsed["side"], "lowest")
        self.assertEqual(parsed["city_slug"], "hong-kong")
        self.assertEqual(parsed["target_date"], "2026-07-22")

    def test_non_temperature_event_ignored(self):
        parsed = parse_event_title("Where will 2026 rank among the hottest years on record?",
                                   "where-will-2026-rank-among-the-hottest-years-on-record")
        self.assertIsNone(parsed)


class TestParseBucketMarkets(unittest.TestCase):
    def test_real_bucket_shape(self):
        event = {"markets": [{
            "conditionId": "0xddda289587f6db8b36dc5463c25ad86ae91d3b3a62806465aeca150098bec2c3",
            "question": "Will the highest temperature in New York City be between 78-79°F on July 21?",
            "groupItemTitle": "78-79°F",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["886163949276325541450652813220419531181645644293329058019391'
                            '20340647059067301", "696262898381368232145431056096040670054696'
                            '87969487078271192507746095737901028"]',
            "volume24hr": 1234.5,
        }]}
        buckets = parse_bucket_markets(event)
        self.assertEqual(len(buckets), 1)
        b = buckets[0]
        self.assertEqual(b["bucket_label"], "78-79°F")
        self.assertTrue(b["token_id_yes"].startswith("886163"))
        self.assertTrue(b["token_id_no"].startswith("696262"))

    def test_malformed_market_skipped(self):
        event = {"markets": [{"conditionId": "0xabc", "question": "?",
                              "outcomes": '["Yes", "No"]', "clobTokenIds": "not-json"}]}
        self.assertEqual(parse_bucket_markets(event), [])


class TestParseDescription(unittest.TestCase):
    def test_nyc_fahrenheit(self):
        r = parse_description(NYC_DESCRIPTION)
        self.assertEqual(r["station"], "LaGuardia Airport Station")
        self.assertEqual(r["unit"], "Fahrenheit")
        self.assertEqual(r["icao"], "KLGA")
        self.assertEqual(r["source_url"],
                         "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA")
        self.assertEqual(r["precision"], "graus inteiros (Fahrenheit)")
        self.assertTrue(r["whole_day_window"])
        self.assertEqual(r["revision_policy"], "until_next_datapoint")

    def test_london_celsius(self):
        r = parse_description(LONDON_DESCRIPTION)
        self.assertEqual(r["unit"], "Celsius")
        self.assertEqual(r["icao"], "EGLC")

    def test_hong_kong_non_wunderground(self):
        """HK resolve pelo Hong Kong Observatory: 1 casa decimal, sem revisão, sem ICAO."""
        r = parse_description(HONG_KONG_DESCRIPTION)
        self.assertEqual(r["station"], "Hong Kong Observatory")
        self.assertEqual(r["unit"], "Celsius")
        self.assertIsNone(r["icao"])
        self.assertEqual(r["source_url"], "https://www.weather.gov.hk/en/cis/climat.htm")
        self.assertEqual(r["precision"], "uma casa decimal (Celsius)")
        self.assertEqual(r["revision_policy"], "none")
        self.assertFalse(r["whole_day_window"])

    def test_unknown_description_yields_none_fields(self):
        r = parse_description("Totally different market rules.")
        self.assertIsNone(r["station"])
        self.assertIsNone(r["icao"])
        self.assertIsNone(r["unit"])
        self.assertIsNone(r["revision_policy"])


if __name__ == "__main__":
    unittest.main()
