"""Gera a seção markdown da issue diária para o ciclo atual (stdout).

Uso: python scripts/report_run.py [--resolve-log caminho]

Tabela por (cidade, data-alvo, bucket): prob, ask, edge, status — status é o
da bet quando existe (PAPER_BET / SIGNAL_REPEAT / BET_INVALID_INTRADAY), senão
"—". Bets aparecem abertas; a tabela completa fica em <details> para a issue
não explodir. Com --resolve-log, inclui o resumo do resolve (fechados,
pendentes, mismatches, stale) a partir dos marcadores do próprio resolve.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import current_cycle, open_ledger, today_str, utc_now

RESOLVE_MARKERS = ("FECHADO", "PENDENTE", "RESOLUTION_MISMATCH",
                   "STALE_RESOLUTION", "PULADO", "OBS_SOURCE", "PIPELINE_ERROR")


def resolve_summary(log_path: str) -> list[str]:
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    marked = [l for l in text.splitlines()
              if l.startswith(RESOLVE_MARKERS)]
    counts = {m: sum(1 for l in marked if l.startswith(m))
              for m in RESOLVE_MARKERS}
    lines = ["### Resumo do resolve", "",
             f"fechados: {counts['FECHADO']} | pendentes: {counts['PENDENTE']} "
             f"| mismatches: {counts['RESOLUTION_MISMATCH']} "
             f"| stale: {counts['STALE_RESOLUTION']} "
             f"| erros: {counts['PIPELINE_ERROR']}", ""]
    if marked:
        lines += ["```", *marked, "```", ""]
    return lines


def main() -> None:
    cycle = current_cycle()
    today = today_str()
    conn = open_ledger()
    rows = conn.execute("""
        SELECT m.city, m.target_date, p.bucket_label, p.prob, pr.ask,
               COALESCE(b.status, '—') AS status
        FROM predictions p
        JOIN markets m USING (condition_id)
        LEFT JOIN prices pr ON pr.prediction_id = p.id
        LEFT JOIN bets b ON b.prediction_id = p.id
        WHERE substr(p.created_at, 1, 10) = ? AND p.run_cycle = ?
        ORDER BY m.city, m.target_date, p.id""", (today, cycle)).fetchall()
    conn.close()

    out = [f"## Ciclo {cycle} — {utc_now().strftime('%Y-%m-%d %H:%M UTC')}", ""]
    if "--resolve-log" in sys.argv:
        out += resolve_summary(sys.argv[sys.argv.index("--resolve-log") + 1])

    if not rows:
        out += ["Nenhuma previsão nova neste ciclo (previsões do dia já "
                "existiam ou nenhum mercado em janela).", ""]
        print("\n".join(out))
        return

    def fmt(r):
        city, target, label, prob, ask, status = r
        ask_s = f"{ask:.3f}" if ask is not None else "—"
        edge_s = f"{prob - ask:+.1%}" if ask is not None else "—"
        return f"| {city} | {target} | {label} | {prob:.1%} | {ask_s} | {edge_s} | {status} |"

    header = ["| cidade | data-alvo | bucket | prob | ask | edge | status |",
              "|---|---|---|---|---|---|---|"]
    bets = [r for r in rows if r[5] != "—"]
    out += [f"{len(rows)} previsões, {len(bets)} sinais de aposta.", ""]
    if bets:
        out += ["### Sinais", "", *header, *(fmt(r) for r in bets), ""]
    out += ["<details><summary>Tabela completa ({} linhas)</summary>".format(len(rows)),
            "", *header, *(fmt(r) for r in rows), "", "</details>", ""]
    print("\n".join(out))


if __name__ == "__main__":
    main()
