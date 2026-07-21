"""Gera rascunho de regras de resolução por cidade a partir da description da Gamma.

Uso:
    python scripts/extract_rules.py <cidade-slug> [<cidade-slug> ...]
    python scripts/extract_rules.py --all-new

Requer data/cache/{hoje}/markets.json (rodar discover.py antes).
O rascunho nasce com `STATUS: RASCUNHO — validar manualmente`; o pipeline só
usa cidades cujo arquivo foi editado para `STATUS: VALIDADO`.
Nunca sobrescreve um arquivo VALIDADO.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RULES_DIR, cache_dir_for, load_json, today_str, utc_now

# "recorded at the LaGuardia Airport Station" (Wunderground) ou
# "recorded by the Hong Kong Observatory" (fonte oficial local) — ambos existem.
STATION_RE = re.compile(r"recorded (?:at|by) the (.+?) in degrees (Fahrenheit|Celsius)")
SOURCE_URL_RE = re.compile(r"available here:\s*(https?://\S+?)[\s,)]*(?:\s|$)")
PRECISION_WHOLE_RE = re.compile(r"measures temperatures to whole degrees (Fahrenheit|Celsius)")
PRECISION_DECIMAL_RE = re.compile(r"measures temperatures in (Fahrenheit|Celsius) to one decimal place")
REVISION_UNTIL_RE = re.compile(r"[Rr]evisions to temperatures.+?will be considered until the first datapoint", re.S)
REVISION_NONE_RE = re.compile(r"[Rr]evisions to temperatures.+?will not be considered", re.S)

# Coordenadas/fuso de estações confirmadas manualmente. Só entradas de alta
# confiança; qualquer outra estação sai como PREENCHER no rascunho.
KNOWN_STATIONS = {
    "KLGA": {"lat": 40.7772, "lon": -73.8726, "tz": "America/New_York",
             "note": "LaGuardia Airport, NYC"},
    "EGLC": {"lat": 51.5053, "lon": 0.0553, "tz": "Europe/London",
             "note": "London City Airport"},
}


def parse_description(description: str) -> dict:
    """Extrai os campos de regra da description do mercado. Campos ausentes = None."""
    station = unit = source_url = icao = precision = None

    m = STATION_RE.search(description)
    if m:
        station, unit = m.group(1), m.group(2)

    m = SOURCE_URL_RE.search(description + " ")
    if m:
        source_url = m.group(1).rstrip(".,)")
        if "wunderground.com" in source_url:
            candidate = source_url.rstrip("/").rsplit("/", 1)[-1].upper()
            if re.fullmatch(r"[A-Z0-9]{4}", candidate):
                icao = candidate

    m = PRECISION_WHOLE_RE.search(description)
    if m:
        precision = f"graus inteiros ({m.group(1)})"
    else:
        m = PRECISION_DECIMAL_RE.search(description)
        if m:
            precision = f"uma casa decimal ({m.group(1)})"

    if REVISION_UNTIL_RE.search(description):
        revision_policy = "until_next_datapoint"
    elif REVISION_NONE_RE.search(description):
        revision_policy = "none"
    else:
        revision_policy = None

    return {
        "station": station,
        "unit": unit,
        "source_url": source_url,
        "icao": icao,
        "precision": precision,
        "whole_day_window": "all times on this day" in description,
        "revision_policy": revision_policy,
    }


def render_rules_md(city_slug: str, event: dict, rules: dict) -> str:
    coords = KNOWN_STATIONS.get(rules["icao"] or "", None)
    lat = f"{coords['lat']}" if coords else "PREENCHER"
    lon = f"{coords['lon']}" if coords else "PREENCHER"
    tz = coords["tz"] if coords else "PREENCHER"
    if coords:
        coord_note = f"pré-preenchido ({coords['note']}) — conferir"
    elif rules["icao"]:
        coord_note = ("obter da estação (ex.: https://aviationweather.gov/data/metar/?id="
                      f"{rules['icao']}) — NÃO usar o centro da cidade")
    else:
        coord_note = ("fonte não é estação de aeroporto (sem ICAO) — localizar a "
                      "estação oficial da fonte e usar as coordenadas DELA, "
                      "não o centro da cidade")

    window = ("dia-calendário local da estação (descrição: \"all times on this day\")"
              if rules["whole_day_window"]
              else "PREENCHER — janela não explícita na descrição; conferir na fonte oficial")
    revision = {
        "until_next_datapoint": "revisões aceitas até o primeiro datapoint do dia seguinte na fonte",
        "none": "revisões após a publicação inicial NÃO são consideradas",
    }.get(rules["revision_policy"], "PREENCHER — cláusula de revisão não encontrada")

    def field(v, fallback="PREENCHER — não extraído da descrição"):
        return v if v else fallback

    return f"""STATUS: RASCUNHO — validar manualmente

# Regras de resolução — {event['city']}

Gerado por extract_rules.py em {utc_now().strftime('%Y-%m-%d %H:%M UTC')}
a partir do evento `{event['event_slug']}`.

- **Estação de resolução exata**: {field(rules['station'])}
- **Código ICAO**: {field(rules['icao'])}
- **Fonte oficial (URL)**: {field(rules['source_url'])}
- **Fuso horário da estação**: {tz}
- **Unidade**: {field('°F (Fahrenheit)' if rules['unit'] == 'Fahrenheit' else '°C (Celsius)' if rules['unit'] == 'Celsius' else None)}
- **Regra de arredondamento**: {field(rules['precision'])} — a fonte reporta nesse nível de precisão
- **Janela diária**: {window}
- **Horário de corte (fechamento de trading)**: {event.get('close_time', 'PREENCHER')}
- **Revisões**: {revision}
- **Lat da estação**: {lat}
- **Lon da estação**: {lon}
- **Observação sobre coordenadas**: {coord_note}

## Trecho da descrição original (auditoria)

> {event['description'][:600].replace(chr(10), chr(10) + '> ')}
"""


def load_todays_markets() -> list[dict]:
    path = cache_dir_for(today_str()) / "markets.json"
    if not path.exists():
        sys.exit(f"ERRO: {path} não existe. Rode `python scripts/discover.py` antes.")
    return load_json(path)


def event_for_city(snapshot: list[dict], city_slug: str) -> dict | None:
    for ev in snapshot:  # snapshot já vem ordenado por volume desc
        if ev["city_slug"] == city_slug and ev["description"]:
            return ev
    return None


def write_rules_for(city_slug: str, snapshot: list[dict]) -> bool:
    event = event_for_city(snapshot, city_slug)
    if not event:
        print(f"AVISO: nenhum mercado ativo encontrado para '{city_slug}'",
              file=sys.stderr)
        return False

    out_path = RULES_DIR / f"{city_slug}.md"
    if out_path.exists():
        first_line = out_path.read_text(encoding="utf-8").splitlines()[0]
        if "VALIDADO" in first_line:
            print(f"PULADO: {out_path.name} já está VALIDADO — não sobrescrevo.")
            return False
        print(f"AVISO: {out_path.name} era rascunho; regenerando.", file=sys.stderr)

    rules = parse_description(event["description"])
    out_path.write_text(render_rules_md(city_slug, event, rules), encoding="utf-8")
    missing = [k for k in ("station", "icao", "source_url", "unit", "precision")
               if not rules[k]]
    status = f" (campos não extraídos: {', '.join(missing)})" if missing else ""
    print(f"Gerado: {out_path}{status}")
    return True


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    snapshot = load_todays_markets()

    if sys.argv[1] == "--all-new":
        slugs = sorted({ev["city_slug"] for ev in snapshot
                        if not (RULES_DIR / f"{ev['city_slug']}.md").exists()})
    else:
        slugs = sys.argv[1:]

    for slug in slugs:
        write_rules_for(slug, snapshot)


if __name__ == "__main__":
    main()
