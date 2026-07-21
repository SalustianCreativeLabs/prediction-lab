"""Snapshot de preços da CLOB + marcação de PAPER_BETs.

Regra 1 (anti-ancoragem): RECUSA-SE a rodar (exit 1) se existir mercado do dia
— de cidade VALIDADA, data-alvo >= hoje — sem previsão registrada hoje no
ledger. O trigger do banco reforça a mesma regra por timestamp.

Regra 4 (paper trading honesto): o preço de "compra" é o ASK do token Sim;
para "venda" (comprar Não), o ASK do token Não — nunca mid, nunca last.

PAPER_BET quando: edge = prob − ask >= 8pp E 0.03 <= ask <= 0.90 E
spread <= 5pp. Stake: Kelly fracionado a 25% sobre bankroll fictício de
$1.000, cap $50 por mercado.

Fatos de API: /book?token_id=... (token por outcome, nunca condition_id);
melhor nível no FIM dos arrays — best bid/ask calculados por max/min.
O book do token Não é o ESPELHO EXATO do Sim (verificado ao vivo 2026-07-21:
ask_não == 1 − bid_sim e bid_não == 1 − ask_sim, igualdade exata) — o ask do
Não usado na regra 4 é derivado do book Sim, sem segunda chamada.
A CLOB aplica throttle de IP em rajadas: manter o espaçamento entre chamadas.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (CLOB_API, cache_dir_for, http_get_json, load_json,
                     load_rules, local_date, open_ledger, today_str, utc_now)

EDGE_MIN = 0.08
ASK_MIN, ASK_MAX = 0.03, 0.90
SPREAD_MAX = 0.05
KELLY_FRACTION = 0.25
BANKROLL = 1000.0
STAKE_CAP = 50.0


def best_levels(book: dict):
    """(best bid, best ask, spread, profundidade no melhor ask) — defensivo."""
    bids = [(float(l["price"]), float(l["size"])) for l in book.get("bids", [])]
    asks = [(float(l["price"]), float(l["size"])) for l in book.get("asks", [])]
    if not bids or not asks:
        return None, None, None, None
    bid = max(p for p, _ in bids)
    ask, depth = min(asks)
    return bid, ask, round(ask - bid, 6), depth


def qualifies(edge: float, ask: float | None, spread: float | None) -> bool:
    if ask is None or spread is None:
        return False
    return edge >= EDGE_MIN and ASK_MIN <= ask <= ASK_MAX and spread <= SPREAD_MAX


def kelly_stake(p_win: float, price: float) -> float:
    """Kelly fracionado: f = (p − a)/(1 − a); stake = 25% × f × bankroll, cap $50."""
    f = (p_win - price) / (1.0 - price)
    if f <= 0:
        return 0.0
    return round(min(KELLY_FRACTION * f * BANKROLL, STAKE_CAP), 2)


def local_day_ended(target_date: str, tz_name: str) -> bool:
    """True se o dia-alvo já terminou no fuso da estação (previsão impossível)."""
    return target_date < local_date(tz_name)


def target_day_begun(target_date: str, tz_name: str) -> bool:
    """True se o dia-alvo já COMEÇOU no fuso da estação.

    PAPER_BET só em horizonte de previsão real: se o dia já começou, o mercado
    tem informação intraday que o ensemble não tem — previsão e snapshot são
    gravados normalmente, mas nenhuma posição hipotética é criada.
    """
    return local_date(tz_name) >= target_date


def find_missing_predictions(events: list[dict], conn, today: str,
                             rules_lookup=load_rules) -> list[tuple]:
    """Buckets de mercados do dia (cidades validadas) sem previsão de hoje.

    Eventos cujo dia local da estação já terminou ficam fora da exigência:
    não há mais previsão possível, logo não há ancoragem a proteger — e eles
    tampouco recebem snapshot (não têm previsão no ledger).
    """
    predicted = {row[0] for row in conn.execute(
        "SELECT condition_id FROM predictions WHERE substr(created_at,1,10) = ?",
        (today,))}
    missing = []
    for ev in events:
        rules = rules_lookup(ev["city_slug"])
        if not rules or not rules["validated"]:
            continue
        if local_day_ended(ev["target_date"], rules["tz"]):
            continue
        for b in ev["buckets"]:
            if b["condition_id"] not in predicted:
                missing.append((b["condition_id"], ev["city_slug"],
                                ev["target_date"], b["bucket_label"]))
    return missing


def fetch_book(token_id: str) -> dict:
    return http_get_json(f"{CLOB_API}/book", params={"token_id": token_id})


def snapshot(conn, snapshot_events: list[dict], today: str) -> None:
    rows = conn.execute(
        "SELECT p.id, p.condition_id, p.token_id, p.bucket_label, p.prob, "
        "m.city, m.target_date FROM predictions p "
        "JOIN markets m USING (condition_id) "
        "WHERE substr(p.created_at,1,10) = ? "
        "AND p.id NOT IN (SELECT prediction_id FROM prices)", (today,)).fetchall()
    if not rows:
        print("Nada a fazer: todas as previsões de hoje já têm snapshot.")
        return

    n_bets = n_failed = 0
    for pid, cid, token_yes, label, prob, city, target in rows:
        time.sleep(0.5)  # a CLOB throttla rajadas de IP (visto ao vivo em 21/jul)
        try:
            book = fetch_book(token_yes)
        except Exception as exc:
            n_failed += 1
            print(f"AVISO: book falhou para {city} {target} {label}: {exc}; "
                  f"pulando (re-rodar completa)", file=sys.stderr)
            continue
        bid, ask, spread, depth = best_levels(book)
        snapped_at = utc_now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO prices (prediction_id, bid, ask, spread, depth, "
            "snapped_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, bid, ask, spread, depth, snapped_at))

        rules = load_rules(city)
        if target_day_begun(target, rules["tz"]):
            conn.commit()
            continue  # snapshot gravado; sem posição hipotética intraday

        bet = None
        if ask is not None and qualifies(prob - ask, ask, spread):
            bet = ("yes", ask, prob - ask, kelly_stake(prob, ask))
        elif bid is not None:
            ask_no = round(1 - bid, 6)  # espelho exato (ver docstring)
            if qualifies((1 - prob) - ask_no, ask_no, spread):
                bet = ("no", ask_no, (1 - prob) - ask_no,
                       kelly_stake(1 - prob, ask_no))
        if bet and bet[3] > 0:
            side, entry, edge, stake = bet
            conn.execute("INSERT INTO bets (prediction_id, side, stake, "
                         "entry_price) VALUES (?, ?, ?, ?)",
                         (pid, side, stake, entry))
            n_bets += 1
            print(f"PAPER_BET {city} {target} {label}: {side} @ {entry:.3f} "
                  f"(prob {prob:.1%}, edge {edge:+.1%}, stake ${stake:.2f})")
        conn.commit()
    done = len(rows) - n_failed
    print(f"\n{done} snapshots gravados, {n_bets} PAPER_BETs marcadas"
          + (f", {n_failed} falhas de book (re-rodar para completar)."
             if n_failed else "."))


def main() -> None:
    today = today_str()
    markets_path = cache_dir_for(today) / "markets.json"
    if not markets_path.exists():
        sys.exit(f"ERRO: {markets_path} não existe. Rode discover.py antes.")
    events = load_json(markets_path)

    conn = open_ledger()
    missing = find_missing_predictions(events, conn, today)
    if missing:
        print("RECUSADO (regra 1 — anti-ancoragem): mercados do dia sem previsão "
              "registrada. Rode fetch_forecast.py e predict.py ANTES do snapshot.",
              file=sys.stderr)
        for cid, city, target, label in missing[:10]:
            print(f"  sem previsão: {city} {target} {label} ({cid[:14]}...)",
                  file=sys.stderr)
        if len(missing) > 10:
            print(f"  ... e mais {len(missing) - 10}.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    snapshot(conn, events, today)
    conn.close()


if __name__ == "__main__":
    main()
