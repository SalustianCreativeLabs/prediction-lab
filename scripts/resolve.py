"""Fecha mercados de dias anteriores no ledger — roda ANTES de tudo no ciclo.

Critérios de finalidade (nunca fechar pelo relógio):
- Família wunderground: só fecha após existir >= 1 observação da estação com
  timestamp no dia LOCAL SEGUINTE ao dia-alvo (janela de revisão do mercado).
  Observação via arquivo ASOS da IEM (mesma rede METAR que alimenta o
  Wunderground). O °F/°C exato publicado pelo Wunderground não é machine-
  readable sem chave; a consistência é checada contra o bucket oficial da
  Polymarket e a margem até a borda do bucket é logada (risco fonte-proxy).
- Família hko: só fecha quando a linha do dia-alvo aparece no XML mensal do
  Daily Extract (valor final na primeira publicação; fonte idêntica à oficial).
- Família nws_timeseries (Istanbul/Moscou/Tel Aviv): FORA DO ESCOPO — pulada
  com aviso.

Além disso, o fechamento exige a resolução oficial da Polymarket (via Gamma):
sem ela não há como validar RESOLUTION_MISMATCH, então o mercado fica pendente
para a próxima rodada. Divergência nossa vs oficial = RESOLUTION_MISMATCH
(prioridade máxima: regra mal extraída).
"""

import csv
import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (GAMMA_API, http_get_json, http_get_text, load_rules,
                     open_ledger, today_str, utc_now)
from predict import official_int, parse_bucket_label

IEM_API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HKO_EXTRACT = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{ym}.xml"
HKO_MAX_IDX, HKO_MIN_IDX = 2, 4  # verificados vs buckets pagos (hong-kong.md)


def iem_station(icao: str) -> str:
    """IEM usa o identificador sem o K inicial para estações dos EUA."""
    return icao[1:] if icao.startswith("K") and len(icao) == 4 else icao


def iem_daily_extremes(csv_text: str, target_date: str, unit: str) -> dict | None:
    """Max/min do dia local + gate de finalidade (obs no dia seguinte)."""
    col = "tmpf" if unit == "F" else "tmpc"
    day_values, has_next_day = [], False
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get(col) in (None, "", "M"):
            continue
        stamp_date = row["valid"][:10]
        if stamp_date == target_date:
            day_values.append(float(row[col]))
        elif stamp_date > target_date:
            has_next_day = True
    if not day_values:
        return None
    return {"max": max(day_values), "min": min(day_values), "final": has_next_day}


def determine_winner(value: float, bucket_labels: list[str], family: str) -> str:
    v = official_int(value, family)
    for label in bucket_labels:
        lo, hi, _ = parse_bucket_label(label)
        if (lo is None or v >= lo) and (hi is None or v <= hi):
            return label
    raise ValueError(f"valor {value} não cai em nenhum bucket de {bucket_labels}")


def bet_pnl(side: str, stake: float, entry_price: float, won_bucket: bool) -> float:
    """P&L hipotético: 'yes' paga se o bucket vence; 'no' paga se NÃO vence."""
    won = won_bucket if side == "yes" else not won_bucket
    return stake * (1 - entry_price) / entry_price if won else -stake


def official_polymarket_winner(side: str, city_slug: str, target_date: str) -> str | None:
    """Bucket pago pela Polymarket (Gamma), ou None se ainda não resolvido."""
    d = date.fromisoformat(target_date)
    slug = (f"{side}-temperature-in-{city_slug}-on-"
            f"{d.strftime('%B').lower()}-{d.day}-{d.year}")
    events = http_get_json(f"{GAMMA_API}/events", params={"slug": slug})
    if not events:
        return None
    for mkt in events[0].get("markets", []):
        prices = json.loads(mkt.get("outcomePrices") or "[]")
        if prices and float(prices[0]) > 0.99:
            return mkt.get("groupItemTitle")
    return None


def observed_value(rules: dict, side: str, target_date: str):
    """(valor observado, fonte, final?) conforme a família; (None,...) se indisponível."""
    if rules["source"] == "wunderground":
        d = date.fromisoformat(target_date)
        nxt = d + timedelta(days=2)
        csv_text = http_get_text(IEM_API, params={
            "station": iem_station(rules["icao"]), "data": "tmpf,tmpc",
            "year1": d.year, "month1": d.month, "day1": d.day,
            "year2": nxt.year, "month2": nxt.month, "day2": nxt.day,
            "tz": rules["tz"], "format": "onlycomma", "latlon": "no",
            "missing": "M", "trace": "T"})
        ext = iem_daily_extremes(csv_text, target_date, rules["unit"])
        if ext is None:
            return None, "IEM ASOS: sem observações", False
        value = ext["max"] if side == "highest" else ext["min"]
        return value, f"IEM ASOS {rules['icao']} (proxy do Wunderground)", ext["final"]

    if rules["source"] == "hko":
        d = date.fromisoformat(target_date)
        data = http_get_json(HKO_EXTRACT.format(ym=f"{d.year}{d.month:02d}"))
        for row in data["stn"]["data"][0]["dayData"]:
            if row[0] == f"{d.day:02d}":
                idx = HKO_MAX_IDX if side == "highest" else HKO_MIN_IDX
                return float(row[idx]), "HKO Daily Extract (fonte oficial)", True
        return None, "HKO Daily Extract: dia ainda não publicado", False

    return None, f"família {rules['source']!r} fora do escopo", False


def edge_margin(value: float, winner_label: str) -> float | None:
    """Distância do valor observado às bordas do bucket vencedor (risco proxy)."""
    lo, hi, _ = parse_bucket_label(winner_label)
    dists = []
    if lo is not None:
        dists.append(value - (lo - 0.5))
    if hi is not None:
        dists.append((hi + 0.5) - value)
    return round(min(dists), 2) if dists else None


def hours_since_day_end(target_date: str, tz_name: str) -> float:
    """Horas desde a meia-noite local que encerrou o dia-alvo."""
    end = datetime.fromisoformat(target_date + "T00:00:00").replace(
        tzinfo=ZoneInfo(tz_name)) + timedelta(days=1)
    return (utc_now() - end) / timedelta(hours=1)


def warn_if_stale(tag: str, target_date: str, tz_name: str) -> None:
    hours = hours_since_day_end(target_date, tz_name)
    if hours > 48:
        print(f"STALE_RESOLUTION {tag}: pendente há {hours:.0f}h após o fim "
              f"do dia local (limite 48h) — investigar fonte de observação.",
              file=sys.stderr)


def resolve_event(conn, city: str, target_date: str, side: str,
                  buckets: list[tuple]) -> None:
    """buckets: [(condition_id, bucket_label)] do evento no ledger."""
    tag = f"{city} {side} {target_date}"
    rules = load_rules(city)
    if not rules or not rules["validated"]:
        print(f"PULADO {tag}: regras não VALIDADO.", file=sys.stderr)
        return
    if rules["source"] not in ("wunderground", "hko"):
        print(f"PULADO {tag}: família {rules['source']!r} fora do escopo "
              f"(nws_timeseries não implementada).", file=sys.stderr)
        return

    value, source, final = observed_value(rules, side, target_date)
    if value is None or not final:
        why = "aguardando corte de finalidade" if value is not None else source
        print(f"PENDENTE {tag}: {why} — não fecho pelo relógio.")
        warn_if_stale(tag, target_date, rules["tz"])
        return

    labels = [label for _, label in buckets]
    ours = determine_winner(value, labels, rules["family"])
    official = official_polymarket_winner(side, city, target_date)
    if official is None:
        print(f"PENDENTE {tag}: Polymarket ainda não resolveu — aguardo para "
              f"validar mismatch.")
        warn_if_stale(tag, target_date, rules["tz"])
        return

    mismatch = int(ours != official)
    if mismatch:
        print(f"RESOLUTION_MISMATCH {tag}: nosso={ours} oficial={official} "
              f"(valor observado {value}) — PRIORIDADE MÁXIMA: revisar regra "
              f"em notes/market_rules/{city}.md", file=sys.stderr)
    elif rules["source"] == "wunderground":
        margin = edge_margin(value, official)
        print(f"OBS_SOURCE {tag}: proxy IEM {value}°{rules['unit']} dentro do "
              f"bucket oficial {official} (margem {margin}° até a borda)")

    resolved_at = utc_now().isoformat(timespec="seconds")
    winner = official  # o oficial é a verdade para outcome; mismatch fica no flag
    pnl_total, n_bets = 0.0, 0
    for cid, label in buckets:
        conn.execute(
            "INSERT INTO resolutions (condition_id, winning_bucket, "
            "official_value, source, resolved_at, mismatch_flag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, winner, value, source, resolved_at, mismatch))
        for stake, entry, bside in conn.execute(
                "SELECT b.stake, b.entry_price, b.side FROM bets b "
                "JOIN predictions p ON p.id = b.prediction_id "
                "WHERE p.condition_id = ? AND b.status = 'PAPER_BET'", (cid,)):
            pnl_total += bet_pnl(bside, stake, entry, label == winner)
            n_bets += 1
    conn.commit()
    print(f"FECHADO {tag}: vencedor {winner}, valor {value}, "
          f"{n_bets} PAPER_BETs, P&L ${pnl_total:+.2f}")


def main() -> None:
    today = today_str()
    conn = open_ledger()
    pending = conn.execute("""
        SELECT m.city, m.target_date,
               CASE WHEN m.title LIKE '%highest%' THEN 'highest' ELSE 'lowest' END,
               GROUP_CONCAT(m.condition_id || '|' || p.bucket_label, ';')
        FROM markets m JOIN (
            SELECT condition_id, bucket_label, MAX(id) FROM predictions
            GROUP BY condition_id) p USING (condition_id)
        WHERE m.target_date <= ?
          AND m.condition_id NOT IN (SELECT condition_id FROM resolutions)
        GROUP BY m.city, m.target_date, 3""", (today,)).fetchall()
    if not pending:
        print("Nenhum mercado pendente de resolução no ledger.")
        return
    had_error = False
    for city, target_date, side, packed in pending:
        buckets = [tuple(item.split("|", 1)) for item in packed.split(";")]
        try:
            resolve_event(conn, city, target_date, side, buckets)
        except Exception as exc:  # exceção != pendência: uma cidade não derruba
            had_error = True     # as outras, mas o run TEM que falhar (alarme)
            print(f"PIPELINE_ERROR resolve {city} {side} {target_date}: {exc}",
                  file=sys.stderr)
    conn.close()
    if had_error:
        sys.exit(2)


if __name__ == "__main__":
    main()
