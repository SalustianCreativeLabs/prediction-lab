# prediction-lab — paper trading de mercados de clima da Polymarket

Sistema de paper trading para mercados diários de temperatura da Polymarket.
Objetivo: acumular 30 dias de previsões registradas com disciplina para medir
calibração e descobrir onde existe edge real. NÃO é para lucrar agora.

Python 3.11+, stdlib + requests + numpy, SQLite. Sem chaves de API — todas as
fontes são públicas.

## Regras invioláveis

1. **Anti-ancoragem**: a distribuição de probabilidade é gerada e COMMITADA no
   ledger ANTES de qualquer consulta a preço de mercado. Ordem obrigatória do
   fluxo diário: `discover` → `fetch_forecast` → `predict` (grava no ledger) →
   `snapshot_price` (grava preços). `snapshot_price.py` DEVE recusar-se a rodar
   (exit 1 com mensagem) se existir mercado do dia sem previsão registrada.
2. **Rastreabilidade**: toda previsão grava junto um resumo do raciocínio:
   quantos membros de ensemble, dispersão (desvio-padrão dos membros), quais
   fontes, horário da rodada de modelo usada.
3. **Regras antes de previsão**: nenhuma cidade entra no ciclo diário sem um
   arquivo `notes/market_rules/{cidade}.md` com `STATUS: VALIDADO`, contendo:
   estação de resolução exata, fonte oficial (URL), fuso horário, unidade,
   regra de arredondamento, horário de corte, coordenadas lat/lon DA ESTAÇÃO
   (não do centro da cidade). Cidades sem VALIDADO são puladas (com aviso).
4. **Paper trading honesto**: o preço registrado para "compra" hipotética é o
   ASK do book (não o mid, não o last). Para "venda" (comprar Não), o ask do
   token Não.
5. **Nada de dinheiro real**: este sistema não executa ordens. Nenhum código de
   autenticação, assinatura ou envio de ordem pode existir neste repo.

## Ciclo diário (AUTOMATIZADO)

O pipeline roda sozinho via `.github/workflows/daily.yml`, 3x ao dia (cron
05:00, 17:00 e 23:00 UTC) + `workflow_dispatch` manual. Cada run: discover →
fetch_forecast → predict → snapshot_price, commit de `ledger/predictions.db` +
`data/cache/` com mensagem `run: {data} {ciclo}`, e seção do ciclo na issue
diária (gerada por `scripts/report_run.py`). Só o ciclo 05z roda `resolve.py`
antes de tudo — `PENDENTE` não é erro (fontes atrasam; loga e segue).

Ordem manual equivalente (mesma sequência, regra 1 vale igual):

1. `python scripts/resolve.py` — fecha o dia anterior ANTES de qualquer coisa.
2. `python scripts/discover.py` — mercados de temperatura ativos.
3. `python scripts/fetch_forecast.py` — ensemble Open-Meteo por cidade validada.
4. `python scripts/predict.py` — distribuição por bucket → ledger.
5. `python scripts/snapshot_price.py` — book CLOB, edge, PAPER_BETs.

Semanal: `python scripts/calibrate.py` (segmenta por cidade, horizonte, lado
e `run_cycle`, e compara acurácia intraday vs pré-dia).

### Semântica do ledger

- `predictions.run_cycle`: `05z`/`17z`/`23z` (ou `manual`); idempotência por
  (mercado, dia, ciclo) — cada ciclo gera previsões e snapshots próprios.
- `bets.status`: `PAPER_BET` (posição válida, única por mercado-bucket),
  `SIGNAL_REPEAT` (mesmo sinal em ciclo posterior; fora do P&L),
  `BET_INVALID_INTRADAY` (criada quando o dia-alvo já tinha começado no fuso
  da estação; fora do P&L). PAPER_BET nova só nasce em horizonte real: se o
  dia-alvo já começou na estação, previsão e snapshot são gravados, posição não.

### Alarmes do workflow (sem supervisão manual)

- `RESOLUTION_MISMATCH` → issue própria com label `mismatch` (prioridade
  máxima: regra de cidade mal extraída — corrigir antes do próximo evento).
- Exceção no pipeline (≠ pendência) → run failed + issue `pipeline-error`.
  Falha parcial (uma cidade) não derruba as outras, mas o run sai exit 2.
- Evento pendente >48h do fim do dia local → issue `stale-resolution`.

### Finalidade da resolução (nunca pelo relógio)

- Família wunderground: fecha só com ≥1 observação da estação no dia local
  seguinte (obs via arquivo ASOS da IEM, proxy da mesma rede METAR; margem
  até a borda do bucket oficial é logada como `OBS_SOURCE`).
- Família hko: fecha só quando a linha do dia aparece no XML mensal do Daily
  Extract (fonte idêntica à oficial; sem janela de revisão).
- Família nws_timeseries (Istanbul/Moscou/Tel Aviv): FORA DO ESCOPO — pulada
  com aviso.
- O fechamento também exige a resolução oficial da Polymarket (Gamma), senão
  o mismatch não seria validável.

## Cronograma do experimento (22/07 → 19/08/2026)

Lógica permanente: expansão sempre atrás de gate (nunca por calendário),
hipóteses baratas primeiro, parâmetro só com 2 semanas de dados e prova
out-of-sample, semana final sem mudanças para veredito limpo. Bug se corrige
NO DIA via issue de alarme; parâmetro só nas sessões de quarta (~9h BRT,
sempre: `git pull` → rodar o dia → `calibrate.py` → analisar relatório).

- **Semana 1 (22–28/07)**: rodar intocado com 3 cidades; só acumular
  (~42 eventos independentes; ~130 pares evento-ciclo). Único trabalho
  ativo: anotar padrões repetidos (suspeito nº 1: viés quente do ensemble
  em Londres, visto dia 21, n=1).
- **Sessão 1 (qua 29/07)**: (1) qualquer RESOLUTION_MISMATCH trava expansão;
  (2) diagnóstico de dispersão: vencedor fora do top-3 de buckets em >30%
  dos eventos = ensemble subdisperso — anotar, NÃO corrigir ainda;
  (3) viés de Londres: 4+ dias pro mesmo lado = investigar como bug
  (janela/fuso/estação) antes de assumir parâmetro; (4) NÃO vs SIM e edge
  por ciclo, só leitura. **Gate 1** (zero mismatch + pipeline estável):
  +4 cidades "pegadinha" — Denver-Buckley, Paris-Le Bourget, Seul-Incheon,
  São Paulo-Guarulhos (`extract_rules.py` + validação manual de cada).
  Antes do VALIDADO de cada uma: resolve seco contra um dia passado (a
  estação reporta na IEM? arredondamento bate com o Wunderground?) —
  VALIDADO errado numa pegadinha vira mismatch na semana 2 e trava o Gate 2.
- **Semana 2 (29/07–04/08)**: 7 cidades, coleta comparativa — pegadinhas
  se comportam diferente das líquidas (spread, edge bruto, reprecificação)?
- **Sessão 2 (qua 05/08, dia ~14)**: primeira sessão onde parâmetro pode
  mudar. (1) Se subdispersão confirmada: correção de dispersão no
  `predict.py` (inflar variância por fator ajustado nas 2 semanas) — a
  mudança de parâmetro mais importante do projeto. A correção nasce
  segmentada por tipo de evento (máx vs mín) no mínimo: os dados do dia 21
  já mostram mínimas calibradas e máximas subdispersas — fator global
  estragaria as mínimas para consertar as máximas; (2) threshold de 8pp:
  se 8-12pp perde e 15pp+ ganha, sobe para 12 (nunca desce antes do dia 30);
  (3) primeiro veredito pegadinhas vs líquidas. **Gate 2** (calibração
  pós-correção sã): +6-8 líquidas de volume (Xangai, Shenzhen, Pequim,
  Milão, Amsterdã, Madri, Munique) → ~13-15 cidades.
- **Semana 3 (05–11/08)**: a correção de dispersão melhorou o Brier
  out-of-sample? Correção que só funciona nos dados que a geraram é
  overfit. O julgamento usa SÓ as 7 cidades pré-Sessão 2 — as do Gate 2
  entram no Brier geral, não no veredito da correção (senão o teste mistura
  efeito da correção com composição de frota nova).
- **Sessão 3 (qua 12/08)**: parar de expandir, começar a podar: ranking de
  cidades por P&L e Brier; desativar as piores (= sair do snapshot de
  apostas, mantendo previsão para calibração). **Gate 3 (opcional)**:
  previsão sem apostas nas cidades restantes, só para mapa de calibração.
- **Semana 4 (12–18/08)**: formato final, sem mudanças; última coleta.
- **Sessão final (qua 19/08, dia 29)**: veredito pelo critério fixado desde
  o início — existe segmento (cidade × horizonte × lado × ciclo) com edge
  positivo consistente desde a inclusão da cidade (mínimo 2 semanas) E
  calibração dentro de ±5pp? Guardas contra falso positivo de comparações
  múltiplas: segmento só concorre com n ≥ 20 apostas resolvidas E história
  mecânica plausível (ex.: divergência estação-vs-cidade) — número verde
  sem mecanismo não leva dinheiro real.
  Sim → só esse segmento leva dinheiro real no mês 2 (bankroll pequeno,
  Kelly 12.5%, paper continua em paralelo como controle).
  Não → pipeline certificado e método provado migram para os módulos de
  entretenimento, onde a tese sempre foi mais forte.

## Fatos verificados das APIs (sondagem ao vivo, 2026-07-21)

Não inventar campos: se a resposta real divergir, adaptar e avisar o usuário.

- **Descoberta**: `GET https://gamma-api.polymarket.com/events?tag_slug=weather&active=true&closed=false&limit=100&offset=N`.
  Filtrar título por `^(Highest|Lowest) temperature in (.+) on (.+)\?$`.
  Cada evento tem ~11 mercados-bucket.
- **Mercado (Gamma)**: `conditionId`, `question`, `description` (regras de
  resolução), `outcomes` e `clobTokenIds` são STRINGS JSON (fazer `json.loads`;
  `clobTokenIds` = [token Yes, token Não]), `groupItemTitle` = label do bucket
  (ex.: `78-79°F`, `19°C or below`, `84°F or above`), `endDate`, `volume24hr`.
  `outcomePrices` da Gamma atrasa — NUNCA usar para snapshot; usar o book CLOB.
- **Resolução é Wunderground, não NWS**: cada mercado resolve pela página de
  histórico do Wunderground de UMA estação exata (ex.: NYC → KLGA LaGuardia,
  Londres → EGLC London City Airport), em graus INTEIROS (°F para EUA, °C para
  o resto), dia-calendário local da estação, revisões aceitas até o primeiro
  datapoint do dia seguinte.
- **CLOB**: `GET https://clob.polymarket.com/book?token_id=...` (token_id por
  outcome, NUNCA condition_id). Níveis `{price, size}` como strings; o melhor
  nível fica no FIM dos arrays — calcular defensivamente best bid = max(bids),
  best ask = min(asks). O book do token Não é ESPELHO EXATO do Sim
  (ask_não == 1−bid_sim, verificado ao vivo) — não buscar o segundo book.
  A CLOB aplica throttle de IP em rajadas: manter espaçamento ≥0.5s entre
  chamadas. A IEM também devolve 429 sob rajada (retry+backoff resolve).
- **Open-Meteo ensemble**: `https://ensemble-api.open-meteo.com/v1/ensemble`.
  Membros vêm como `hourly.temperature_2m_memberNN` + controle `temperature_2m`
  (com múltiplos modelos o nome inclui o modelo) — parsear dinamicamente toda
  chave iniciada em `temperature_2m`. Usar `temperature_unit=fahrenheit` quando
  a regra da cidade for °F.

## Convenções

- **Toda sessão de trabalho neste repo começa com `git pull`.** O cron do
  GitHub Actions commita `ledger/predictions.db` (SQLite, binário) a cada
  ciclo — conflito em binário não tem merge; trabalhar sobre cópia velha
  perde runs.

- Tudo em UTC internamente; converter para o fuso da estação só na lógica de
  janela diária.
- Erros de rede: retry + backoff (ver `scripts/_common.py`).
- Respostas brutas de API do dia vão para `data/cache/{YYYY-MM-DD}/` (auditoria),
  comprimidas como `.json.gz` (`save_json`/`load_json` são transparentes ao
  sufixo); `markets.json` e `forecast_*.json` ficam legíveis, sem compressão.
- Código simples e legível vence código esperto.
