# prediction-lab — fase clima

Paper trading disciplinado dos mercados diários de temperatura da Polymarket
("Highest/Lowest temperature in {cidade} on {data}?"). O objetivo **não é
lucrar agora**: é acumular 30 dias de previsões registradas com disciplina
para medir calibração e descobrir onde existe edge real.

Nenhum código deste repositório executa ordens. Não há autenticação,
assinatura nem envio de ordem — só leitura de APIs públicas e um ledger local.

## A tese

O edge nesses mercados nasce das **regras de resolução**, não da meteorologia.
Cada mercado resolve contra UMA estação específica, com fuso, unidade e
arredondamento próprios — modelar a estação errada é perder estando certo.
Exemplos reais mapeados aqui:

- NYC resolve na **KLGA (LaGuardia)**, não em Manhattan; Denver na **KBKF
  (Buckley)**, não em KDEN; Paris em **LFPB (Le Bourget)**, não em CDG.
- 45 cidades resolvem pelo Wunderground em **graus inteiros**; Hong Kong
  resolve pelo Observatório local em **°C com 1 decimal e truncamento**
  (30.6°C → bucket "30°C", provado contra mercados pagos); Istanbul, Moscou e
  Tel Aviv resolvem por uma página do NWS (fora do escopo por ora).
- O sensor da KLGA mede nativamente °F inteiro: a conversão correta é
  `round_half_up(°C × 1.8 + 32)` — `floor` erraria 2 de 5 dias verificados.

Cada regra dessas está documentada com a evidência em
[`notes/market_rules/`](notes/market_rules/), e o pipeline **se recusa a
operar** cidades sem regra validada.

## Como funciona

```
resolve.py          fecha o dia anterior (SEMPRE primeiro)
discover.py         lista mercados ativos (Gamma API)
extract_rules.py    rascunho de regras por cidade nova (validação é manual)
fetch_forecast.py   ensemble Open-Meteo (~120 membros GFS+ECMWF+ICON)
                    nas coordenadas DA ESTAÇÃO, no fuso e unidade da regra
predict.py          fração de membros por bucket -> ledger (SEM ver preço)
snapshot_price.py   book da CLOB, edge = prob - ask, PAPER_BETs hipotéticas
calibrate.py        semanal: Brier, curva de calibração, P&L segmentado
```

A previsão é uma distribuição sobre buckets (fração de membros do ensemble em
cada faixa, piso de 0.5%, renormalizada), gravada com raciocínio auditável:
nº de membros, dispersão, fontes e horário do fetch.

### Regras invioláveis (ver [CLAUDE.md](CLAUDE.md))

1. **Anti-ancoragem** — a previsão é commitada no ledger ANTES de qualquer
   consulta a preço. `snapshot_price.py` recusa rodar (exit 1) se faltar
   previsão do dia, e um trigger SQLite rejeita preço com timestamp anterior
   à previsão.
2. **Rastreabilidade** — toda previsão grava o raciocínio da época.
3. **Regras antes de previsão** — cidade sem `STATUS: VALIDADO` é pulada.
4. **Paper trading honesto** — preço de entrada é o ASK do book (nunca mid,
   nunca last); comprar Não usa o ask do token Não (espelho exato do book Sim,
   verificado ao vivo).
5. **Nada de dinheiro real.**

Além disso: PAPER_BET só nasce em **horizonte de previsão real** — se o
dia-alvo já começou no fuso da estação, o mercado tem informação intraday que
o ensemble não tem; a previsão e o snapshot são gravados, a posição não.

### Resolução — nunca pelo relógio

- Família Wunderground: só fecha após existir ≥1 observação da estação no dia
  local seguinte (janela de revisão do mercado). Observação via arquivo ASOS
  da IEM (mesma rede METAR do Wunderground); a margem do valor até a borda do
  bucket oficial é logada como medida do risco de fonte-proxy.
- Família HKO: só fecha quando a linha do dia aparece no XML mensal do Daily
  Extract (valor final na primeira publicação).
- Divergência entre nosso bucket e o pago pela Polymarket vira
  `RESOLUTION_MISMATCH` — prioridade máxima, significa regra mal extraída.

## Rodando

Requisitos: Python 3.11+, `pip install requests numpy`. Sem chaves de API.

```bash
python scripts/discover.py            # tabela de mercados do dia
python scripts/extract_rules.py nyc   # rascunho de regras (validar na mão!)
# editar notes/market_rules/nyc.md -> STATUS: VALIDADO
python scripts/fetch_forecast.py
python scripts/predict.py
python scripts/snapshot_price.py
# no dia seguinte:
python scripts/resolve.py
python scripts/calibrate.py           # relatório em notes/calibration/
```

O ciclo completo está descrito em
[`skills/weather/SKILL.md`](skills/weather/SKILL.md), incluindo o que revisar
em cada etapa e os critérios para aprovar PAPER_BETs (spoiler: edge > 25pp
quase sempre significa que o mercado sabe algo que o ensemble não sabe).

```bash
python -m unittest discover -s tests   # 49 testes
```

## Estrutura

```
scripts/            pipeline (7 scripts + _common.py)
notes/market_rules/ regras de resolução por cidade, com evidência empírica
notes/calibration/  relatórios semanais
ledger/             predictions.db (SQLite, não versionado)
data/cache/         respostas brutas de API do dia (não versionado)
skills/weather/     SKILL.md do ciclo diário
tests/              parsing, famílias de arredondamento, ciclo simulado
```

## Estado

Fase clima em coleta de dados (iniciada 2026-07-21, 3 cidades validadas:
NYC, Londres, Hong Kong). Nenhuma conclusão sobre edge antes de 30 dias de
calibração.
