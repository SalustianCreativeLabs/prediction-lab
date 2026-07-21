"""Testes da Etapa 3: snapshot_price (recusa, Kelly, filtros) e resolve
(vencedor por família, finalidade, P&L), incluindo um ciclo simulado ponta a
ponta no ledger em memória."""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import LEDGER_SCHEMA
from snapshot_price import (best_levels, find_missing_predictions,
                            kelly_stake, qualifies)
from resolve import bet_pnl, determine_winner, iem_daily_extremes, iem_station


def memory_ledger():
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEDGER_SCHEMA)
    return conn


def stub_rules(city_slug):
    return {"validated": True, "tz": "UTC"}


class TestSnapshotPreconditions(unittest.TestCase):
    # data futura fixa: o dia local nunca "terminou" durante o teste
    EVENTS = [{
        "city_slug": "nyc", "side": "highest", "target_date": "2999-01-01",
        "buckets": [{"condition_id": "0xaaa", "bucket_label": "80-81°F"},
                    {"condition_id": "0xbbb", "bucket_label": "82-83°F"}],
    }]

    def test_missing_prediction_detected(self):
        conn = memory_ledger()
        conn.execute("INSERT INTO markets VALUES ('0xaaa','nyc','2999-01-01','q','c')")
        conn.execute("INSERT INTO predictions (condition_id, token_id, bucket_label,"
                     " prob, n_members, dispersion, reasoning, created_at) "
                     "VALUES ('0xaaa','t1','80-81°F',0.3,122,1.0,'{}',"
                     "'2999-01-01T15:00:00+00:00')")
        missing = find_missing_predictions(self.EVENTS, conn, "2999-01-01",
                                           rules_lookup=stub_rules)
        self.assertEqual([m[0] for m in missing], ["0xbbb"])

    def test_all_predicted_passes(self):
        conn = memory_ledger()
        for cid in ("0xaaa", "0xbbb"):
            conn.execute("INSERT INTO markets VALUES (?,'nyc','2999-01-01','q','c')",
                         (cid,))
            conn.execute("INSERT INTO predictions (condition_id, token_id, "
                         "bucket_label, prob, n_members, dispersion, reasoning, "
                         "created_at) VALUES (?,'t','b',0.3,122,1.0,'{}',"
                         "'2999-01-01T15:00:00+00:00')", (cid,))
        self.assertEqual(find_missing_predictions(self.EVENTS, conn, "2999-01-01",
                                                  rules_lookup=stub_rules), [])

    def test_ended_local_day_excluded(self):
        """Mercado cujo dia local já acabou não trava o snapshot (HK 21/jul)."""
        ended = [{"city_slug": "hong-kong", "side": "highest",
                  "target_date": "2000-01-01",
                  "buckets": [{"condition_id": "0xold", "bucket_label": "31°C"}]}]
        conn = memory_ledger()
        self.assertEqual(find_missing_predictions(ended, conn, "2000-01-01",
                                                  rules_lookup=stub_rules), [])

    def test_unvalidated_city_excluded(self):
        conn = memory_ledger()
        missing = find_missing_predictions(
            self.EVENTS, conn, "2999-01-01",
            rules_lookup=lambda c: {"validated": False, "tz": "UTC"})
        self.assertEqual(missing, [])


class TestBookAndBets(unittest.TestCase):
    def test_best_levels_defensive_ordering(self):
        # fato de API: melhor nível no FIM dos arrays; calcular max/min mesmo assim
        book = {"bids": [{"price": "0.24", "size": "8"}, {"price": "0.26", "size": "70"}],
                "asks": [{"price": "0.29", "size": "20"}, {"price": "0.27", "size": "15"}]}
        bid, ask, spread, depth = best_levels(book)
        self.assertEqual((bid, ask), (0.26, 0.27))
        self.assertAlmostEqual(spread, 0.01)
        self.assertEqual(depth, 15.0)  # tamanho no melhor ask

    def test_best_levels_empty_book(self):
        self.assertEqual(best_levels({"bids": [], "asks": []}),
                         (None, None, None, None))

    def test_qualifies_thresholds(self):
        self.assertTrue(qualifies(edge=0.08, ask=0.03, spread=0.05))
        self.assertFalse(qualifies(edge=0.079, ask=0.10, spread=0.02))  # edge < 8pp
        self.assertFalse(qualifies(edge=0.20, ask=0.02, spread=0.02))   # ask < 0.03
        self.assertFalse(qualifies(edge=0.20, ask=0.91, spread=0.02))   # ask > 0.90
        self.assertFalse(qualifies(edge=0.20, ask=0.10, spread=0.06))   # spread > 5pp
        self.assertFalse(qualifies(edge=0.20, ask=None, spread=None))   # book vazio

    def test_kelly_stake_fraction_and_cap(self):
        # p=0.15, ask=0.10: f = 0.05/0.90 = 0.0556 -> 25% * f * 1000 = 13.89
        self.assertAlmostEqual(kelly_stake(0.15, 0.10), 13.89, places=2)
        # edge grande estoura o cap de $50 por mercado
        self.assertEqual(kelly_stake(0.50, 0.10), 50.0)
        self.assertEqual(kelly_stake(0.05, 0.10), 0.0)  # edge negativo: sem stake


class TestResolveCore(unittest.TestCase):
    F_BUCKETS = ["71°F or below", "72-73°F", "74-75°F", "76-77°F", "78-79°F",
                 "80-81°F", "82-83°F", "84-85°F", "86-87°F", "88-89°F",
                 "90°F or above"]
    C_BUCKETS = ["25°C or below", "26°C", "27°C", "28°C", "29°C", "30°C",
                 "31°C", "32°C", "33°C", "34°C", "35°C or higher"]

    def test_winner_whole_family_empirical(self):
        # KLGA 19/jul: max METAR 27.2°C = 80.96°F -> 81 -> 80-81 (pago)
        self.assertEqual(determine_winner(80.96, self.F_BUCKETS, "whole"), "80-81°F")
        self.assertEqual(determine_winner(87.98, self.F_BUCKETS, "whole"), "88-89°F")

    def test_winner_decimal_family_empirical(self):
        # HKO: 30.6 -> 30°C; 25.7 -> 25°C or below (min de 20/jul)
        self.assertEqual(determine_winner(30.6, self.C_BUCKETS, "decimal_floor"), "30°C")
        self.assertEqual(determine_winner(25.7, self.C_BUCKETS, "decimal_floor"),
                         "25°C or below")

    def test_iem_station_strips_us_prefix_only(self):
        self.assertEqual(iem_station("KLGA"), "LGA")
        self.assertEqual(iem_station("EGLC"), "EGLC")

    IEM_CSV = """station,valid,tmpf,tmpc
LGA,2026-07-19 00:51,72.0,22.22
LGA,2026-07-19 14:51,81.0,27.20
LGA,2026-07-19 23:51,74.0,23.33
LGA,2026-07-20 00:51,72.0,22.22
"""
    IEM_CSV_NO_NEXT_DAY = """station,valid,tmpf,tmpc
LGA,2026-07-19 14:51,81.0,27.20
"""

    def test_iem_daily_extremes_and_finality(self):
        ext = iem_daily_extremes(self.IEM_CSV, "2026-07-19", unit="F")
        self.assertEqual(ext["max"], 81.0)
        self.assertEqual(ext["min"], 72.0)
        self.assertTrue(ext["final"])  # existe observação de 20/jul

    def test_iem_finality_gate_blocks(self):
        ext = iem_daily_extremes(self.IEM_CSV_NO_NEXT_DAY, "2026-07-19", unit="F")
        self.assertFalse(ext["final"])  # sem datapoint do dia seguinte: NÃO fechar

    def test_bet_pnl(self):
        # yes vencedor: stake 10 a 0.20 -> 50 shares -> lucro 40
        self.assertAlmostEqual(bet_pnl("yes", 10.0, 0.20, won_bucket=True), 40.0)
        self.assertAlmostEqual(bet_pnl("yes", 10.0, 0.20, won_bucket=False), -10.0)
        # no: ganha quando o bucket NÃO vence
        self.assertAlmostEqual(bet_pnl("no", 9.0, 0.90, won_bucket=False), 1.0)
        self.assertAlmostEqual(bet_pnl("no", 9.0, 0.90, won_bucket=True), -9.0)


class TestSimulatedFullCycle(unittest.TestCase):
    """Ciclo ponta a ponta no ledger: mercado -> previsão -> preço -> aposta ->
    resolução -> P&L, com o trigger da regra 1 ativo."""

    def test_cycle(self):
        conn = memory_ledger()
        conn.execute("INSERT INTO markets VALUES ('0xsim','nyc','2026-07-19',"
                     "'Will ... highest ... 80-81°F ...','2026-07-19T12:00:00Z')")
        conn.execute("INSERT INTO predictions (condition_id, token_id, bucket_label,"
                     " prob, n_members, dispersion, reasoning, created_at) VALUES "
                     "('0xsim','tok-yes','80-81°F',0.30,122,1.9,'{}',"
                     "'2026-07-19T14:00:00+00:00')")
        pid = conn.execute("SELECT id FROM predictions").fetchone()[0]
        # regra 1: preço ANTES da previsão deve abortar
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO prices VALUES (?,0.1,0.12,0.02,50,"
                         "'2026-07-19T13:59:59+00:00')", (pid,))
        # preço depois da previsão passa; edge 30% - 12% = 18pp -> PAPER_BET
        conn.execute("INSERT INTO prices VALUES (?,0.10,0.12,0.02,50,"
                     "'2026-07-19T15:00:00+00:00')", (pid,))
        stake = kelly_stake(0.30, 0.12)
        conn.execute("INSERT INTO bets (prediction_id, side, stake, entry_price)"
                     " VALUES (?,'yes',?,0.12)", (pid, stake))
        # resolução: 80.96°F -> 81 -> bucket 80-81°F vence
        winner = determine_winner(80.96, ["78-79°F", "80-81°F", "82-83°F"], "whole")
        conn.execute("INSERT INTO resolutions VALUES ('0xsim',?,80.96,"
                     "'IEM ASOS (teste)','2026-07-20T09:00:00+00:00',0)", (winner,))
        label, side, st, entry = conn.execute(
            "SELECT p.bucket_label, b.side, b.stake, b.entry_price FROM bets b "
            "JOIN predictions p ON p.id = b.prediction_id").fetchone()
        pnl = bet_pnl(side, st, entry, won_bucket=(label == winner))
        self.assertEqual(winner, "80-81°F")
        self.assertGreater(pnl, 0)
        self.assertAlmostEqual(pnl, st * (1 - entry) / entry, places=6)


if __name__ == "__main__":
    unittest.main()
