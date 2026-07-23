"""Helpers compartilhados: paths do repo, HTTP GET com retry+backoff, cache JSON."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache"
RULES_DIR = REPO_ROOT / "notes" / "market_rules"
LEDGER_DB = REPO_ROOT / "ledger" / "predictions.db"

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

USER_AGENT = "prediction-lab/0.1 (paper trading research; sem execucao de ordens)"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    """Data de hoje em UTC, formato YYYY-MM-DD (nome do diretório de cache)."""
    return utc_now().strftime("%Y-%m-%d")


def cache_dir_for(date_str: str) -> Path:
    d = CACHE_DIR / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def _http_get(url: str, params: dict | None, retries: int, backoff: float,
              timeout: float):
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            wait = backoff * (2 ** attempt)
            print(f"[retry {attempt + 1}/{retries}] {url} falhou: {exc}; "
                  f"aguardando {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    raise last_exc


def http_get_json(url: str, params: dict | None = None, retries: int = 3,
                  backoff: float = 2.0, timeout: float = 30.0):
    """GET JSON com retry + backoff exponencial."""
    return _http_get(url, params, retries, backoff, timeout).json()


def http_get_text(url: str, params: dict | None = None, retries: int = 3,
                  backoff: float = 2.0, timeout: float = 30.0) -> str:
    """GET texto (ex.: CSV da IEM) com retry + backoff exponencial."""
    return _http_get(url, params, retries, backoff, timeout).text


def parse_rules_text(text: str) -> dict:
    """Extrai campos machine-readable de um notes/market_rules/{cidade}.md."""
    import re

    def field(pattern):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    lat = field(r"\*\*Lat da estação\*\*:\s*(-?\d+\.?\d*)")
    lon = field(r"\*\*Lon da estação\*\*:\s*(-?\d+\.?\d*)")
    tz = field(r"\*\*Fuso horário da estação\*\*:\s*([A-Za-z_]+/[A-Za-z_]+)")
    unit = field(r"\*\*Unidade\*\*:\s*°([CF])")
    icao = field(r"\*\*Código ICAO\*\*:\s*([A-Z0-9]{4})\b")
    # Família só pode vir da linha do campo "Regra de arredondamento" — o
    # resto do arquivo pode citar as duas frases (ex.: hong-kong.md descreve
    # labels "graus inteiros" mas a regra é decimal).
    rounding = field(r"\*\*Regra de arredondamento\*\*:\s*([^\n]+)")
    if rounding and "uma casa decimal" in rounding:
        family = "decimal_floor"
    elif rounding and "graus inteiros" in rounding:
        family = "whole"
    else:
        family = None
    if "wunderground.com" in text:
        source = "wunderground"
    elif "weather.gov.hk" in text:
        source = "hko"
    elif "weather.gov/wrh/timeseries" in text:
        source = "nws_timeseries"
    else:
        source = None
    return {
        "validated": text.splitlines()[0].strip() == "STATUS: VALIDADO",
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "tz": tz,
        "unit": unit,
        "family": family,
        "source": source,
        "icao": icao,
    }


def load_rules(city_slug: str) -> dict | None:
    """Regras da cidade; None se o arquivo não existe. Campo 'validated' decide uso."""
    path = RULES_DIR / f"{city_slug}.md"
    if not path.exists():
        return None
    rules = parse_rules_text(path.read_text(encoding="utf-8"))
    rules["city_slug"] = city_slug
    return rules


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    title TEXT NOT NULL,
    close_time TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL REFERENCES markets(condition_id),
    token_id TEXT NOT NULL,
    bucket_label TEXT NOT NULL,
    prob REAL NOT NULL,
    n_members INTEGER NOT NULL,
    dispersion REAL NOT NULL,
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_cycle TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS prices (
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    bid REAL,
    ask REAL,
    spread REAL,
    depth REAL,
    snapped_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bets (
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    side TEXT NOT NULL,
    stake REAL NOT NULL,
    entry_price REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PAPER_BET'
);
CREATE TABLE IF NOT EXISTS resolutions (
    condition_id TEXT NOT NULL REFERENCES markets(condition_id),
    winning_bucket TEXT,
    official_value REAL,
    source TEXT,
    resolved_at TEXT NOT NULL,
    mismatch_flag INTEGER NOT NULL DEFAULT 0
);
-- Regra 1 (anti-ancoragem) reforçada no banco: preço nunca antes da previsão.
CREATE TRIGGER IF NOT EXISTS price_after_prediction
BEFORE INSERT ON prices
WHEN NEW.snapped_at <= (SELECT created_at FROM predictions
                        WHERE id = NEW.prediction_id)
BEGIN
    SELECT RAISE(ABORT, 'regra 1: snapshot de preço antes da previsão');
END;
"""


def open_ledger():
    """Conexão SQLite com o schema garantido."""
    import sqlite3
    LEDGER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LEDGER_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(LEDGER_SCHEMA)
    # migrações idempotentes para bancos criados antes das colunas (2026-07-21)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)")]
    if "status" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN status TEXT NOT NULL "
                     "DEFAULT 'PAPER_BET'")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
    if "run_cycle" not in cols:
        conn.execute("ALTER TABLE predictions ADD COLUMN run_cycle TEXT "
                     "NOT NULL DEFAULT 'manual'")
    conn.commit()
    return conn


def current_cycle() -> str:
    """Ciclo do run: RUN_CYCLE do ambiente ou derivado da hora UTC.

    Cron do workflow: 05:00, 17:00, 23:00 UTC (com tolerância a atraso do GH).
    """
    import os
    env = os.environ.get("RUN_CYCLE")
    if env in ("05z", "17z", "23z"):
        return env
    hour = utc_now().hour
    if hour < 11:
        return "05z"
    if hour < 20:
        return "17z"
    return "23z"


def local_date(tz_name: str) -> str:
    """Data atual (YYYY-MM-DD) no fuso dado."""
    from zoneinfo import ZoneInfo
    return utc_now().astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def save_json(path: Path, data) -> None:
    """Grava JSON; caminhos .json.gz saem comprimidos (respostas brutas de API)."""
    import gzip
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def load_json(path: Path):
    """Lê JSON, transparente a .json.gz."""
    import gzip
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))
