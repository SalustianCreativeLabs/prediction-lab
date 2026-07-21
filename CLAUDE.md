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

## Ciclo diário

1. `python scripts/resolve.py` — fecha o dia anterior ANTES de qualquer coisa.
2. `python scripts/discover.py` — mercados de temperatura ativos.
3. `python scripts/fetch_forecast.py` — ensemble Open-Meteo por cidade validada.
4. `python scripts/predict.py` — distribuição por bucket → ledger.
5. `python scripts/snapshot_price.py` — book CLOB, edge, PAPER_BETs.

Semanal: `python scripts/calibrate.py`.

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
  best ask = min(asks).
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
