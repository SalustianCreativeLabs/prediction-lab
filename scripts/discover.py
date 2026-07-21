"""Descobre mercados diários de temperatura ativos na Polymarket (Gamma API).

Saída: data/cache/{YYYY-MM-DD}/markets.json (idempotente — sobrescreve o do dia)
e data/cache/{YYYY-MM-DD}/raw_gamma_events.json (resposta bruta, auditoria).
Imprime tabela-resumo no terminal.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import GAMMA_API, cache_dir_for, http_get_json, save_json, today_str

TITLE_RE = re.compile(r"^(Highest|Lowest) temperature in (.+) on (.+)\?$")


def city_to_slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-")


def parse_event_title(title: str, event_slug: str) -> dict | None:
    """Título → lado/cidade/data-alvo. Ano vem do slug do evento (o título não tem)."""
    m = TITLE_RE.match(title)
    if not m:
        return None
    side, city, date_phrase = m.group(1).lower(), m.group(2), m.group(3)
    year_m = re.search(r"(\d{4})$", event_slug)
    if not year_m:
        return None
    try:
        target = datetime.strptime(f"{date_phrase} {year_m.group(1)}", "%B %d %Y")
    except ValueError:
        return None
    return {
        "side": side,  # highest | lowest
        "city": city,
        "city_slug": city_to_slug(city),
        "target_date": target.strftime("%Y-%m-%d"),
    }


def fetch_weather_events() -> list[dict]:
    """Pagina os eventos ativos com tag weather até esgotar."""
    events, offset, limit = [], 0, 100
    while True:
        page = http_get_json(f"{GAMMA_API}/events", params={
            "tag_slug": "weather", "active": "true", "closed": "false",
            "limit": limit, "offset": offset,
            "order": "volume24hr", "ascending": "false",
        })
        events.extend(page)
        if len(page) < limit:
            return events
        offset += limit


def parse_bucket_markets(event: dict) -> list[dict]:
    """Um mercado Gamma por bucket. clobTokenIds/outcomes são strings JSON."""
    buckets = []
    for mkt in event.get("markets", []):
        try:
            token_ids = json.loads(mkt["clobTokenIds"])
            outcomes = json.loads(mkt["outcomes"])
        except (KeyError, TypeError, ValueError) as exc:
            print(f"AVISO: mercado {mkt.get('id')} sem tokens parseáveis "
                  f"({exc}); pulando bucket", file=sys.stderr)
            continue
        if outcomes != ["Yes", "No"] or len(token_ids) != 2:
            print(f"AVISO: outcomes inesperados em {mkt.get('question')!r}: "
                  f"{outcomes}; pulando bucket", file=sys.stderr)
            continue
        buckets.append({
            "condition_id": mkt["conditionId"],
            "question": mkt["question"],
            "bucket_label": mkt.get("groupItemTitle", ""),
            "token_id_yes": token_ids[0],
            "token_id_no": token_ids[1],
            "volume24hr": mkt.get("volume24hr", 0),
        })
    return buckets


def build_markets_snapshot(events: list[dict]) -> list[dict]:
    snapshot = []
    for ev in events:
        parsed = parse_event_title(ev.get("title", ""), ev.get("slug", ""))
        if not parsed:
            continue
        buckets = parse_bucket_markets(ev)
        if not buckets:
            continue
        first_mkt = ev["markets"][0]
        snapshot.append({
            **parsed,
            "event_slug": ev["slug"],
            "title": ev["title"],
            "close_time": ev.get("endDate"),
            "volume24hr": ev.get("volume24hr", 0),
            "description": first_mkt.get("description", ""),
            "buckets": buckets,
        })
    snapshot.sort(key=lambda e: e["volume24hr"], reverse=True)
    return snapshot


def print_summary(snapshot: list[dict]) -> None:
    print(f"\n{'cidade':<16} {'lado':<8} {'data-alvo':<11} {'buckets':>7} "
          f"{'vol 24h ($)':>13}")
    print("-" * 60)
    for ev in snapshot:
        print(f"{ev['city']:<16} {ev['side']:<8} {ev['target_date']:<11} "
              f"{len(ev['buckets']):>7} {ev['volume24hr']:>13,.0f}")
    print(f"\n{len(snapshot)} mercados de temperatura ativos.")


def main() -> None:
    date_str = today_str()
    out_dir = cache_dir_for(date_str)

    raw_events = fetch_weather_events()
    save_json(out_dir / "raw_gamma_events.json.gz", raw_events)

    snapshot = build_markets_snapshot(raw_events)
    save_json(out_dir / "markets.json", snapshot)

    print_summary(snapshot)
    print(f"Gravado: {out_dir / 'markets.json'}")


if __name__ == "__main__":
    main()
