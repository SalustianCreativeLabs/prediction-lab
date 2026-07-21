"""Testes do núcleo de calibrate.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from calibrate import brier, is_intraday, render


class TestBrier(unittest.TestCase):
    def test_perfect_and_worst(self):
        self.assertEqual(brier([(1.0, 1.0), (0.0, 0.0)]), 0.0)
        self.assertEqual(brier([(0.0, 1.0)]), 1.0)

    def test_known_value(self):
        # (0.7-1)^2=0.09, (0.3-0)^2=0.09 -> media 0.09
        self.assertEqual(brier([(0.7, 1.0), (0.3, 0.0)]), 0.09)

    def test_empty(self):
        self.assertIsNone(brier([]))


class TestIntraday(unittest.TestCase):
    def test_before_local_day_is_preday(self):
        # 23:00 UTC de 20/jul = 19:00 em NY -> dia 21 ainda não começou
        self.assertFalse(is_intraday("2026-07-20T23:00:00+00:00", "2026-07-21",
                                     "America/New_York"))

    def test_after_local_midnight_is_intraday(self):
        # 17:00 UTC de 21/jul = 01:00 de 22/jul em HK -> alvo 22 já começou
        self.assertTrue(is_intraday("2026-07-21T17:00:00+00:00", "2026-07-22",
                                    "Asia/Hong_Kong"))

    def test_same_utc_moment_differs_by_station(self):
        stamp = "2026-07-21T17:00:00+00:00"
        self.assertTrue(is_intraday(stamp, "2026-07-21", "Europe/London"))
        self.assertFalse(is_intraday(stamp, "2026-07-22", "Europe/London"))


class TestRender(unittest.TestCase):
    def test_empty_ledger_report(self):
        text = render([], [], "2026-07-21")
        self.assertIn("Nenhum mercado resolvido", text)

    def test_report_sections_present(self):
        resolved = [
            {"prediction_id": 1, "city": "nyc", "target_date": "2026-07-20",
             "bucket": "80-81°F", "prob": 0.30, "reasoning": "{}",
             "created_at": "2026-07-19T14:00:00+00:00",
             "resolved_at": "2026-07-21T09:00:00+00:00",
             "outcome": 1.0, "winner": "80-81°F", "mismatch": 0,
             "intraday": False, "hours_to_resolution": 43.0,
             "run_cycle": "05z"},
            {"prediction_id": 2, "city": "nyc", "target_date": "2026-07-20",
             "bucket": "82-83°F", "prob": 0.55, "reasoning": "{}",
             "created_at": "2026-07-20T14:00:00+00:00",
             "resolved_at": "2026-07-21T09:00:00+00:00",
             "outcome": 0.0, "winner": "80-81°F", "mismatch": 0,
             "intraday": True, "hours_to_resolution": 19.0,
             "run_cycle": "17z"},
        ]
        pnls = [{"city": "nyc", "side": "yes", "stake": 10.0, "pnl": 23.3,
                 "horizon": ">24h", "run_cycle": "05z"}]
        text = render(resolved, pnls, "2026-07-21")
        for section in ("## Brier score", "## Curva de calibração",
                        "## P&L hipotético", "## Acurácia intraday vs pré-dia",
                        "## Top 5 erros"):
            self.assertIn(section, text)
        self.assertIn("pré-dia", text)
        # pior erro primeiro: (0.30-1)^2=0.49 > (0.55-0)^2=0.30 — perder o
        # vencedor a 30% dói mais que superestimar um perdedor a 55%
        self.assertLess(text.index("80-81°F (prob 30.0%"),
                        text.index("82-83°F (prob 55.0%"))


if __name__ == "__main__":
    unittest.main()
