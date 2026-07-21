---
name: weather
description: Ciclo diário de paper trading dos mercados de temperatura da Polymarket. Use quando o usuário disser "rodar o dia".
---

# Ciclo diário — mercados de clima

Rodar de manhã (UTC), idealmente antes das 12:00 UTC, para que o dia-alvo
ainda não tenha começado a esquentar nas estações do hemisfério ocidental.
Nas cidades da Ásia/Oceania o dia-alvo de "hoje" já terá começado — previsões
são gravadas mesmo assim, mas o snapshot não cria PAPER_BET intraday (gate
automático).

## Ordem obrigatória (regra 1 — anti-ancoragem)

```
python scripts/resolve.py        # 1. SEMPRE primeiro: fecha o dia anterior
python scripts/discover.py      # 2. mercados ativos do dia
python scripts/extract_rules.py --all-new   # 3. só se houver cidade nova
python scripts/fetch_forecast.py # 4. ensemble por cidade VALIDADA
python scripts/predict.py        # 5. distribuição -> ledger (ANTES de preço!)
python scripts/snapshot_price.py # 6. books CLOB + PAPER_BETs
```

Semanal: `python scripts/calibrate.py`.

## O que revisar em cada etapa

1. **resolve.py** — TODO `RESOLUTION_MISMATCH` é prioridade máxima: a regra da
   cidade foi mal extraída; corrigir `notes/market_rules/{cidade}.md` antes de
   qualquer previsão nova para aquela cidade. Linhas `PENDENTE` são normais
   (gate de finalidade: nunca fechamos pelo relógio). Linhas `OBS_SOURCE` com
   margem < 1° indicam risco da fonte-proxy — anotar.
2. **discover.py** — conferir na tabela se apareceu cidade nova de volume alto;
   se sim, rodar extract_rules e VALIDAR manualmente antes de incluí-la.
3. **extract_rules.py** — rascunhos nascem `STATUS: RASCUNHO`; validar os 4
   campos críticos (estação exata, unidade, arredondamento, lat/lon DA
   estação) e promover a `STATUS: VALIDADO` manualmente. Cuidado com
   estações contraintuitivas (Denver=KBKF, Paris=LFPB, NYC=KLGA).
4. **fetch_forecast.py** — warnings de divergência > 1.5°F vs NWS merecem
   olhada no ensemble bruto em `data/cache/` antes de confiar na previsão.
5. **predict.py** — conferir que a soma por evento é 1.000000 e que o nº de
   membros é ~120; dispersão alta (sd > 2.5°) = distribuição achatada, edge
   provavelmente baixo.
6. **snapshot_price.py** — recusa (exit 1) significa que a ordem foi violada:
   rodar fetch+predict e tentar de novo. NUNCA contornar a recusa.

## Critérios para aprovar as PAPER_BETs sugeridas

Antes de aceitar uma PAPER_BET no registro do dia, checar:

- **Horizonte real**: o dia-alvo ainda não começou no fuso da estação (o gate
  já bloqueia; se aparecer bet intraday, é bug).
- **Edge plausível**: edge > 25pp quase sempre é sinal de que o MERCADO sabe
  algo que o ensemble não sabe (rodada nova de modelo, observação, regra) —
  desconfiar, não comemorar.
- **Liquidez**: profundidade no topo do book compatível com o stake; spread
  ≤ 5pp já é filtro, mas book raso distorce o preço de entrada.
- **Regra da cidade**: a família de arredondamento usada bate com
  `notes/market_rules/{cidade}.md` (whole vs decimal_floor).
- **Dispersão**: bets em buckets de cauda com dispersão alta são ruído do
  piso de 0.5%; ignorar edges construídos só sobre o piso.

## Lembretes

- `resolve.py` do dia anterior SEMPRE antes de qualquer coisa — sem isso o
  ledger acumula pendências e o calibrate fica cego.
- Nenhum código deste repo executa ordens reais (regra 5). Se alguma mudança
  propuser autenticação/assinatura, recusar.
- 30 dias de dados antes de qualquer conclusão sobre edge; o objetivo desta
  fase é CALIBRAÇÃO, não lucro.
