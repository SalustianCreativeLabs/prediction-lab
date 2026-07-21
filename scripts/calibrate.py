"""Relatório semanal de calibração em notes/calibration/{data}.md.

Usa apenas mercados RESOLVIDOS no ledger. Seções:
- Brier score geral e por cidade
- curva de calibração em bins de 10% (previsto vs observado, contagem)
- P&L hipotético (só status PAPER_BET) por cidade, por horizonte
  (previsão feita >24h vs <24h da resolução) e por lado (yes vs no)
- acurácia intraday vs pré-dia (adendo): Brier das previsões feitas após o
  início do dia-alvo no fuso da estação vs antes — quantifica o viés de
  informação intraday que motivou o gate de PAPER_BET
- top 5 erros da semana com o raciocínio registrado na época
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import REPO_ROOT, load_rules, open_ledger, today_str
from resolve import bet_pnl

OUT_DIR = REPO_ROOT / "notes" / "calibration"


def brier(rows) -> float | None:
    """rows: [(prob, outcome01)]"""
    return round(sum((p - o) ** 2 for p, o in rows) / len(rows), 4) if rows else None


def is_intraday(created_at: str, target_date: str, tz_name: str) -> bool:
    """Previsão feita depois do início do dia-alvo no fuso da estação."""
    local = datetime.fromisoformat(created_at).astimezone(ZoneInfo(tz_name))
    return local.strftime("%Y-%m-%d") >= target_date


def load_resolved(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT p.id, m.city, m.target_date, p.bucket_label, p.prob, p.reasoning,
               p.created_at, r.winning_bucket, r.resolved_at, r.mismatch_flag
        FROM predictions p
        JOIN markets m USING (condition_id)
        JOIN resolutions r ON r.condition_id = p.condition_id""").fetchall()
    out = []
    for pid, city, target, label, prob, reasoning, created, winner, resolved, mm in rows:
        out.append({
            "prediction_id": pid, "city": city, "target_date": target,
            "bucket": label, "prob": prob, "reasoning": reasoning,
            "created_at": created, "resolved_at": resolved,
            "outcome": 1.0 if label == winner else 0.0,
            "winner": winner, "mismatch": mm,
            "intraday": is_intraday(created, target, load_rules(city)["tz"]),
            "hours_to_resolution": (datetime.fromisoformat(resolved)
                                    - datetime.fromisoformat(created))
                                   / timedelta(hours=1),
        })
    return out


def load_bet_pnls(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT m.city, b.side, b.stake, b.entry_price, p.bucket_label,
               r.winning_bucket, p.created_at, r.resolved_at
        FROM bets b
        JOIN predictions p ON p.id = b.prediction_id
        JOIN markets m USING (condition_id)
        JOIN resolutions r ON r.condition_id = p.condition_id
        WHERE b.status = 'PAPER_BET'""").fetchall()
    out = []
    for city, side, stake, entry, label, winner, created, resolved in rows:
        hours = (datetime.fromisoformat(resolved)
                 - datetime.fromisoformat(created)) / timedelta(hours=1)
        out.append({
            "city": city, "side": side, "stake": stake,
            "pnl": bet_pnl(side, stake, entry, label == winner),
            "horizon": ">24h" if hours > 24 else "<24h",
        })
    return out


def group_sum(items, key, value):
    acc = {}
    for it in items:
        acc.setdefault(it[key], []).append(it[value])
    return {k: sum(v) for k, v in sorted(acc.items())}


def render(resolved: list[dict], pnls: list[dict], today: str) -> str:
    lines = [f"# Calibração — {today}", ""]
    if not resolved:
        lines += ["Nenhum mercado resolvido no ledger ainda. Rode o ciclo "
                  "diário por pelo menos 2 dias (resolve.py fecha o dia "
                  "anterior) e gere o relatório de novo.", ""]
        return "\n".join(lines)

    n_events = len({(r["city"], r["target_date"], r["winner"]) for r in resolved})
    lines += [f"{len(resolved)} previsões-bucket resolvidas, {n_events} eventos.",
              ""]

    all_pairs = [(r["prob"], r["outcome"]) for r in resolved]
    lines += ["## Brier score", "",
              f"- **Geral**: {brier(all_pairs)}", ""]
    lines += ["| cidade | Brier | n |", "|---|---|---|"]
    by_city = {}
    for r in resolved:
        by_city.setdefault(r["city"], []).append((r["prob"], r["outcome"]))
    for city, pairs in sorted(by_city.items()):
        lines.append(f"| {city} | {brier(pairs)} | {len(pairs)} |")
    lines.append("")

    lines += ["## Curva de calibração (bins de 10%)", "",
              "| bin | previsto médio | observado | n |", "|---|---|---|---|"]
    for lo10 in range(10):
        lo, hi = lo10 / 10, (lo10 + 1) / 10
        bin_rows = [r for r in resolved
                    if lo <= r["prob"] < hi or (lo10 == 9 and r["prob"] == 1.0)]
        if not bin_rows:
            continue
        mean_p = sum(r["prob"] for r in bin_rows) / len(bin_rows)
        obs = sum(r["outcome"] for r in bin_rows) / len(bin_rows)
        lines.append(f"| {lo:.0%}–{hi:.0%} | {mean_p:.1%} | {obs:.1%} "
                     f"| {len(bin_rows)} |")
    lines.append("")

    lines += ["## P&L hipotético (PAPER_BETs válidas)", ""]
    if not pnls:
        lines += ["Nenhuma PAPER_BET resolvida ainda.", ""]
    else:
        total = sum(b["pnl"] for b in pnls)
        lines += [f"- **Total**: ${total:+.2f} em {len(pnls)} apostas", ""]
        for title, key in (("por cidade", "city"), ("por horizonte", "horizon"),
                           ("por lado", "side")):
            lines += [f"### {title}", "", "| segmento | P&L | n |", "|---|---|---|"]
            groups = {}
            for b in pnls:
                groups.setdefault(b[key], []).append(b["pnl"])
            for seg, vals in sorted(groups.items()):
                lines.append(f"| {seg} | ${sum(vals):+.2f} | {len(vals)} |")
            lines.append("")

    lines += ["## Acurácia intraday vs pré-dia (viés de informação)", "",
              "Previsões feitas APÓS o início do dia-alvo no fuso da estação "
              "competem contra um mercado que já viu as observações do dia — "
              "este gap quantifica o viés que motivou o gate de PAPER_BET.", "",
              "| classe | Brier | n |", "|---|---|---|"]
    for name, flag in (("pré-dia (horizonte real)", False), ("intraday", True)):
        pairs = [(r["prob"], r["outcome"]) for r in resolved
                 if r["intraday"] == flag]
        lines.append(f"| {name} | {brier(pairs) if pairs else '—'} "
                     f"| {len(pairs)} |")
    lines.append("")

    lines += ["## Top 5 erros da semana", ""]
    worst = sorted(resolved, key=lambda r: (r["prob"] - r["outcome"]) ** 2,
                   reverse=True)[:5]
    for r in worst:
        kind = ("bucket vencedor subestimado" if r["outcome"]
                else "bucket perdedor superestimado")
        lines += [f"### {r['city']} {r['target_date']} — {r['bucket']} "
                  f"(prob {r['prob']:.1%}, resultado {r['outcome']:.0f}) — {kind}",
                  "", f"Vencedor real: {r['winner']}"
                  + (" — **RESOLUTION_MISMATCH registrado!**" if r["mismatch"] else ""),
                  "", f"Raciocínio da época: `{r['reasoning']}`", ""]

    mismatches = [r for r in resolved if r["mismatch"]]
    if mismatches:
        lines += ["## RESOLUTION_MISMATCH pendentes (prioridade máxima)", ""]
        for r in {(m["city"], m["target_date"]) for m in mismatches}:
            lines.append(f"- {r[0]} {r[1]} — revisar notes/market_rules/{r[0]}.md")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    today = today_str()
    conn = open_ledger()
    resolved = load_resolved(conn)
    pnls = load_bet_pnls(conn)
    conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{today}.md"
    out_path.write_text(render(resolved, pnls, today), encoding="utf-8")
    print(f"Relatório: {out_path} ({len(resolved)} previsões resolvidas, "
          f"{len(pnls)} PAPER_BETs)")


if __name__ == "__main__":
    main()
