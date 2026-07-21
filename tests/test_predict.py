"""Testes do núcleo de predict.py: buckets, arredondamento por família, distribuição.

As regras de arredondamento vêm de evidência empírica documentada em
notes/market_rules/nyc.md (round half-up, 5 dias) e hong-kong.md (truncamento,
2 dias resolvidos).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from predict import (bucket_distribution, official_int, parse_bucket_label,
                     round_half_up)
from _common import parse_rules_text


class TestParseBucketLabel(unittest.TestCase):
    def test_range_fahrenheit(self):
        self.assertEqual(parse_bucket_label("78-79°F"), (78, 79, "F"))

    def test_open_below(self):
        self.assertEqual(parse_bucket_label("71°F or below"), (None, 71, "F"))
        self.assertEqual(parse_bucket_label("25°C or below"), (None, 25, "C"))

    def test_open_above(self):
        self.assertEqual(parse_bucket_label("90°F or above"), (90, None, "F"))
        self.assertEqual(parse_bucket_label("35°C or higher"), (35, None, "C"))

    def test_single_degree_celsius(self):
        self.assertEqual(parse_bucket_label("31°C"), (31, 31, "C"))

    def test_negative_range(self):
        self.assertEqual(parse_bucket_label("-3--2°C"), (-3, -2, "C"))

    def test_unknown_label_raises(self):
        with self.assertRaises(ValueError):
            parse_bucket_label("muito quente")


class TestRounding(unittest.TestCase):
    def test_round_half_up_empirical_cases(self):
        # casos reais KLGA 16-20/jul (ver nyc.md): floor falharia em 87.98 e 80.96
        self.assertEqual(round_half_up(87.98), 88)
        self.assertEqual(round_half_up(80.96), 81)
        self.assertEqual(round_half_up(82.04), 82)

    def test_round_half_up_is_not_bankers(self):
        self.assertEqual(round_half_up(86.5), 87)  # round() do Python daria 86
        self.assertEqual(round_half_up(87.5), 88)

    def test_family_whole(self):
        self.assertEqual(official_int(87.98, "whole"), 88)
        self.assertEqual(official_int(80.96, "whole"), 81)

    def test_family_decimal_floor_empirical_cases(self):
        # casos reais HKO (ver hong-kong.md): 30.6 -> bucket 30, 29.7 -> bucket 29
        self.assertEqual(official_int(30.6, "decimal_floor"), 30)
        self.assertEqual(official_int(29.7, "decimal_floor"), 29)

    def test_family_decimal_floor_replicates_one_decimal_source(self):
        # a fonte publica 1 decimal: 30.96 vira 31.0 na fonte -> bucket 31
        self.assertEqual(official_int(30.96, "decimal_floor"), 31)
        self.assertEqual(official_int(30.94, "decimal_floor"), 30)


class TestBucketDistribution(unittest.TestCase):
    BUCKETS = ["71°F or below", "72-73°F", "74-75°F", "76-77°F", "78-79°F",
               "80-81°F", "82-83°F", "84-85°F", "86-87°F", "88-89°F",
               "90°F or above"]

    def test_sums_to_one_and_no_zero(self):
        members = [78.2, 79.4, 80.1, 80.9, 82.6, 81.4, 80.2, 79.9]
        dist = bucket_distribution(members, self.BUCKETS, "whole")
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=9)
        for label, p in dist.items():
            self.assertGreater(p, 0.004, f"bucket {label} ficou ~zero")

    def test_mass_lands_in_right_buckets(self):
        # 78.2->78, 79.4->79 (78-79); 80.1->80, 80.9->81, 80.2->80, 79.9->80 (80-81)
        members = [78.2, 79.4, 80.1, 80.9, 80.2, 79.9]
        dist = bucket_distribution(members, self.BUCKETS, "whole")
        self.assertGreater(dist["80-81°F"], dist["78-79°F"])
        self.assertGreater(dist["78-79°F"], dist["90°F or above"])

    def test_open_edges_capture_extremes(self):
        members = [60.0, 95.0]
        dist = bucket_distribution(members, self.BUCKETS, "whole")
        self.assertAlmostEqual(dist["71°F or below"], dist["90°F or above"])

    def test_decimal_floor_family(self):
        buckets = ["25°C or below", "26°C", "27°C", "28°C", "29°C", "30°C",
                   "31°C", "32°C", "33°C", "34°C", "35°C or higher"]
        # 30.6 e 30.9 -> 30; 31.0 -> 31
        dist = bucket_distribution([30.6, 30.9, 31.0], buckets, "decimal_floor")
        self.assertAlmostEqual(dist["30°C"] / dist["31°C"], 2.0, places=6)


class TestParseRulesText(unittest.TestCase):
    FIXTURE = """STATUS: VALIDADO

# Regras de resolução — Teste

- **Estação de resolução exata**: Estação Teste
- **Fuso horário da estação**: America/New_York
- **Unidade**: °F (Fahrenheit)
- **Regra de arredondamento**: graus inteiros (Fahrenheit) — a fonte reporta nesse nível
- **Lat da estação**: 40.7772
- **Lon da estação**: -73.8726
- **Código ICAO**: KLGA
"""

    def test_parses_validated_rules(self):
        r = parse_rules_text(self.FIXTURE)
        self.assertTrue(r["validated"])
        self.assertEqual(r["tz"], "America/New_York")
        self.assertEqual(r["unit"], "F")
        self.assertEqual(r["family"], "whole")
        self.assertAlmostEqual(r["lat"], 40.7772)
        self.assertAlmostEqual(r["lon"], -73.8726)
        self.assertEqual(r["icao"], "KLGA")

    def test_decimal_family_and_draft_status(self):
        text = self.FIXTURE.replace("STATUS: VALIDADO", "STATUS: RASCUNHO — validar") \
                           .replace("graus inteiros (Fahrenheit) — a fonte reporta nesse nível",
                                    "uma casa decimal (Celsius) — SEM arredondamento")
        r = parse_rules_text(text)
        self.assertFalse(r["validated"])
        self.assertEqual(r["family"], "decimal_floor")


if __name__ == "__main__":
    unittest.main()
