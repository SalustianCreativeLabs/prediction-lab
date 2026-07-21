"""Busca o ensemble Open-Meteo por cidade VALIDADA com mercado ativo hoje/amanhã.

Para cada cidade: uma chamada ao ensemble nas coordenadas DA ESTAÇÃO (regra 3),
na unidade e fuso da regra da cidade. Computa a máxima (ou mínima) diária por
membro na janela do dia-calendário local. Para cidades dos EUA (ICAO K...),
busca a previsão do NWS como sanidade e loga divergência > 1.5°F.

Saídas em data/cache/{hoje}/: ensemble_{cidade}.json (bruto) e
forecast_{cidade}.json (extremos por membro, por evento).

Fato de API (sondado 2026-07-21): com múltiplos modelos as séries vêm como
temperature_2m_{modelo} (controle) e temperature_2m_memberNN_{modelo}.
A API NÃO expõe o horário da rodada do modelo; registramos o horário do fetch.
"""

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (cache_dir_for, http_get_json, load_json, load_rules,
                     save_json, today_str, utc_now)

ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"
NWS_API = "https://api.weather.gov"
MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless"
NWS_DIVERGENCE_LIMIT_F = 1.5


def member_extremes(hourly: dict, target_date: str, side: str) -> dict[str, float]:
    """Extremo diário (max p/ highest, min p/ lowest) por série do ensemble.

    Os horários já vêm no fuso da estação (parâmetro timezone), então a janela
    do dia-calendário local é o prefixo da data no timestamp.
    """
    times = hourly["time"]
    idx = [i for i, t in enumerate(times) if t.startswith(target_date)]
    agg = max if side == "highest" else min
    extremes = {}
    for key, values in hourly.items():
        if not key.startswith("temperature_2m"):
            continue
        day_values = [values[i] for i in idx if values[i] is not None]
        if len(day_values) < 20:  # dia incompleto nessa série
            continue
        extremes[key] = agg(day_values)
    return extremes


def nws_daytime_high(lat: float, lon: float, target_date: str) -> float | None:
    """Máxima prevista pelo NWS para o dia-alvo (período diurno). None se falhar."""
    try:
        points = http_get_json(f"{NWS_API}/points/{lat},{lon}", retries=2)
        forecast = http_get_json(points["properties"]["forecast"], retries=2)
        for period in forecast["properties"]["periods"]:
            if period["isDaytime"] and period["startTime"][:10] == target_date:
                return float(period["temperature"])
    except Exception as exc:  # sanidade é best-effort, nunca fatal
        print(f"AVISO: NWS indisponível para {lat},{lon}: {exc}", file=sys.stderr)
    return None


def fetch_city(city_slug: str, events: list[dict], out_dir) -> None:
    rules = load_rules(city_slug)
    if rules is None:
        print(f"PULADO {city_slug}: sem arquivo de regras (regra 3).", file=sys.stderr)
        return
    if not rules["validated"]:
        print(f"PULADO {city_slug}: regras não VALIDADO (regra 3).", file=sys.stderr)
        return
    missing = [k for k in ("lat", "lon", "tz", "unit", "family") if not rules[k]]
    if missing:
        print(f"PULADO {city_slug}: regras VALIDADO mas incompletas ({missing}).",
              file=sys.stderr)
        return

    today = today_str()
    targets = sorted({(ev["side"], ev["target_date"]) for ev in events
                      if ev["target_date"] >= today})
    if not targets:
        print(f"PULADO {city_slug}: nenhum mercado com data-alvo >= hoje.")
        return

    raw = http_get_json(ENSEMBLE_API, params={
        "latitude": rules["lat"], "longitude": rules["lon"],
        "hourly": "temperature_2m", "models": MODELS,
        "timezone": rules["tz"],
        "temperature_unit": "fahrenheit" if rules["unit"] == "F" else "celsius",
        "forecast_days": 4,
    })
    save_json(out_dir / f"ensemble_{city_slug}.json.gz", raw)

    fetched_at = utc_now().isoformat(timespec="seconds")
    per_event = []
    for side, target_date in targets:
        extremes = member_extremes(raw["hourly"], target_date, side)
        if not extremes:
            print(f"AVISO {city_slug} {side} {target_date}: sem membros com dia "
                  f"completo.", file=sys.stderr)
            continue
        entry = {
            "side": side,
            "target_date": target_date,
            "member_extremes": extremes,
            "n_members": len(extremes),
        }
        # sanidade NWS: só EUA (ICAO K...), mercados de máxima em °F
        if (rules["icao"] or "").startswith("K") and rules["unit"] == "F" \
                and side == "highest":
            nws = nws_daytime_high(rules["lat"], rules["lon"], target_date)
            entry["nws_high_f"] = nws
            if nws is not None:
                median = statistics.median(extremes.values())
                if abs(median - nws) > NWS_DIVERGENCE_LIMIT_F:
                    print(f"AVISO {city_slug} {target_date}: mediana ensemble "
                          f"{median:.1f}°F vs NWS {nws:.0f}°F "
                          f"(divergência > {NWS_DIVERGENCE_LIMIT_F}°F)",
                          file=sys.stderr)
        per_event.append(entry)
        print(f"{city_slug} {side} {target_date}: {len(extremes)} membros, "
              f"mediana {statistics.median(extremes.values()):.1f}°{rules['unit']}")

    save_json(out_dir / f"forecast_{city_slug}.json", {
        "city_slug": city_slug,
        "fetched_at": fetched_at,
        "models": MODELS,
        "model_run_note": "Open-Meteo não expõe o horário da rodada; registrado o horário do fetch",
        "unit": rules["unit"],
        "tz": rules["tz"],
        "events": per_event,
    })


def main() -> None:
    out_dir = cache_dir_for(today_str())
    markets_path = out_dir / "markets.json"
    if not markets_path.exists():
        sys.exit(f"ERRO: {markets_path} não existe. Rode `python scripts/discover.py` antes.")
    snapshot = load_json(markets_path)

    by_city: dict[str, list] = {}
    for ev in snapshot:
        by_city.setdefault(ev["city_slug"], []).append(ev)

    wanted = sys.argv[1:] or sorted(by_city)
    had_error = False
    for city_slug in wanted:
        if city_slug not in by_city:
            print(f"PULADO {city_slug}: sem mercado ativo hoje.", file=sys.stderr)
            continue
        try:
            fetch_city(city_slug, by_city[city_slug], out_dir)
        except Exception as exc:  # uma cidade fora não derruba as outras
            had_error = True
            print(f"PIPELINE_ERROR fetch {city_slug}: {exc}", file=sys.stderr)
    if had_error:
        sys.exit(2)


if __name__ == "__main__":
    main()
