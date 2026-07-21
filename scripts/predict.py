"""Converte extremos de ensemble em distribuição por bucket e grava no ledger.

NÃO acessa nenhuma API da Polymarket (regra 1 — roda ANTES do snapshot de preço).
Consome data/cache/{hoje}/markets.json (descoberta) e forecast_{cidade}.json.

Famílias de arredondamento (evidência empírica em notes/market_rules/):
- "whole" (Wunderground/NWS, graus inteiros): round half-up do valor na unidade
  do mercado — floor falharia (ver nyc.md, 5 dias resolvidos).
- "decimal_floor" (HKO, uma casa decimal): arredonda a 1 decimal (replica a
  fonte) e trunca para o inteiro do bucket (ver hong-kong.md, 2 dias resolvidos).
"""

import json
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (cache_dir_for, load_json, load_rules, open_ledger,
                     today_str, utc_now)

BUCKET_RANGE_RE = re.compile(r"^(-?\d+)-(-?\d+)°([CF])$")
BUCKET_BELOW_RE = re.compile(r"^(-?\d+)°([CF]) or (?:below|lower|less)$")
BUCKET_ABOVE_RE = re.compile(r"^(-?\d+)°([CF]) or (?:above|higher|more)$")
BUCKET_SINGLE_RE = re.compile(r"^(-?\d+)°([CF])$")

PROB_FLOOR = 0.005


def parse_bucket_label(label: str) -> tuple[int | None, int | None, str]:
    """Label do mercado -> (lo, hi, unidade); None = extremidade aberta."""
    if m := BUCKET_RANGE_RE.match(label):
        return int(m.group(1)), int(m.group(2)), m.group(3)
    if m := BUCKET_BELOW_RE.match(label):
        return None, int(m.group(1)), m.group(2)
    if m := BUCKET_ABOVE_RE.match(label):
        return int(m.group(1)), None, m.group(2)
    if m := BUCKET_SINGLE_RE.match(label):
        return int(m.group(1)), int(m.group(1)), m.group(2)
    raise ValueError(f"bucket label não reconhecido: {label!r}")


def round_half_up(x: float) -> int:
    """Half-up determinístico — round() do Python é banker's rounding."""
    return math.floor(x + 0.5)


def official_int(value: float, family: str) -> int:
    """Inteiro que a fonte oficial publicaria para este valor."""
    if family == "whole":
        return round_half_up(value)
    if family == "decimal_floor":
        return math.floor(round(value, 1))
    raise ValueError(f"família desconhecida: {family!r}")


def bucket_distribution(members: list[float], bucket_labels: list[str],
                        family: str) -> dict[str, float]:
    """Fração de membros por bucket, piso de 0.5% e renormalização (soma = 1)."""
    parsed = [(label, *parse_bucket_label(label)[:2]) for label in bucket_labels]
    counts = dict.fromkeys(bucket_labels, 0)
    for value in members:
        v = official_int(value, family)
        for label, lo, hi in parsed:
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                counts[label] += 1
                break
    n = len(members)
    probs = {label: max(c / n, PROB_FLOOR) for label, c in counts.items()}
    total = sum(probs.values())
    return {label: p / total for label, p in probs.items()}


def predict_event(event: dict, forecast_entry: dict, forecast_meta: dict,
                  conn) -> None:
    labels = [b["bucket_label"] for b in event["buckets"]]
    members = list(forecast_entry["member_extremes"].values())
    rules = load_rules(event["city_slug"])
    dist = bucket_distribution(members, labels, rules["family"])

    dispersion = statistics.stdev(members) if len(members) > 1 else 0.0
    created_at = utc_now().isoformat(timespec="seconds")
    reasoning = json.dumps({
        "n_members": len(members),
        "dispersion": round(dispersion, 3),
        "sources": [f"open-meteo ensemble ({forecast_meta['models']})"],
        "fetched_at": forecast_meta["fetched_at"],
        "model_run_note": forecast_meta["model_run_note"],
        "nws_high_f": forecast_entry.get("nws_high_f"),
        "family": rules["family"],
        "unit": rules["unit"],
    }, ensure_ascii=False)

    cur = conn.cursor()
    inserted = 0
    for bucket in event["buckets"]:
        cid = bucket["condition_id"]
        already = cur.execute(
            "SELECT 1 FROM predictions WHERE condition_id = ? "
            "AND substr(created_at, 1, 10) = ?", (cid, created_at[:10])).fetchone()
        if already:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO markets (condition_id, city, target_date, "
            "title, close_time) VALUES (?, ?, ?, ?, ?)",
            (cid, event["city_slug"], event["target_date"], bucket["question"],
             event["close_time"]))
        cur.execute(
            "INSERT INTO predictions (condition_id, token_id, bucket_label, "
            "prob, n_members, dispersion, reasoning, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, bucket["token_id_yes"], bucket["bucket_label"],
             dist[bucket["bucket_label"]], len(members), dispersion, reasoning,
             created_at))
        inserted += 1
    conn.commit()

    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_txt = ", ".join(f"{l}={p:.1%}" for l, p in top)
    skipped = "" if inserted else " [já previsto hoje — nada gravado]"
    print(f"{event['city_slug']} {event['side']} {event['target_date']}: "
          f"soma={sum(dist.values()):.6f}, n={len(members)}, sd={dispersion:.2f} "
          f"| top: {top_txt}{skipped}")


def main() -> None:
    cache = cache_dir_for(today_str())
    markets_path = cache / "markets.json"
    if not markets_path.exists():
        sys.exit(f"ERRO: {markets_path} não existe. Rode discover.py antes.")
    snapshot = load_json(markets_path)

    conn = open_ledger()
    predicted_any = False
    for event in snapshot:
        forecast_path = cache / f"forecast_{event['city_slug']}.json"
        if not forecast_path.exists():
            continue
        forecast = load_json(forecast_path)
        entry = next((e for e in forecast["events"]
                      if e["side"] == event["side"]
                      and e["target_date"] == event["target_date"]), None)
        if entry is None:
            continue
        predict_event(event, entry, forecast, conn)
        predicted_any = True
    conn.close()

    if not predicted_any:
        sys.exit("ERRO: nenhuma previsão gerada — rode fetch_forecast.py antes "
                 "e confira as cidades VALIDADO.")


if __name__ == "__main__":
    main()
