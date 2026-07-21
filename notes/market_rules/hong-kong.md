STATUS: VALIDADO

# Regras de resolução — Hong Kong

Gerado por extract_rules.py em 2026-07-21 16:29 UTC; campos completados e
verificados manualmente (browser + APIs oficiais) em 2026-07-21.

- **Estação de resolução exata**: Hong Kong Observatory — posto SEDE (headquarters,
  Tsim Sha Tsui, Observatory Hill). É o posto cujos dados alimentam o "Daily
  Extract"; a descrição do mercado diz "recorded by the Hong Kong Observatory"
  e o Daily Extract publica uma única estação (a sede), não as dezenas de
  estações automáticas da rede HKO.
- **Código ICAO**: não se aplica — não é estação de aeroporto (VHHH é o
  aeroporto, NÃO usar).
- **Fonte oficial (URL)**: https://www.weather.gov.hk/en/cis/climat.htm
  ("Daily Extract", campo "Absolute Daily Max (deg. C)")
- **Fonte machine-readable (para resolve.py)**:
  `https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{YYYYMM}.xml`
  (JSON apesar da extensão; `stn.data[0].dayData[dia-1]`, índice 2 = Absolute
  Daily Max — verificado contra 19/jul=30.6 e 20/jul=29.7; índice 4 = Absolute
  Daily Min — verificado contra buckets pagos dos mercados lowest: 19/jul
  min 28.4 → "28°C", 20/jul min 25.7 → "25°C")
- **Fuso horário da estação**: Asia/Hong_Kong (UTC+8, sem horário de verão)
- **Unidade**: °C (Celsius)
- **Regra de arredondamento**: uma casa decimal (Celsius) — SEM arredondamento
  para inteiro. Ver "Fronteiras dos buckets" abaixo.
- **Janela diária**: dia-calendário local HKT (00:00–24:00 Asia/Hong_Kong),
  janela do Daily Extract.
- **Horário de corte (fechamento de trading)**: endDate do evento (12:00Z do
  dia-alvo nos metadados da Gamma).
- **Revisões**: revisões após a publicação inicial NÃO são consideradas — o
  valor é FINAL na primeira publicação do dia no Daily Extract. O mercado não
  resolve antes dessa publicação ("can not resolve until data for this date
  has been published"). resolve.py deve esperar a linha do dia aparecer no
  XML mensal (às 00:40 HKT do dia seguinte o dia 21 ainda não estava publicado).
- **Lat da estação**: 22.3019
- **Lon da estação**: 114.1743
- **Observação sobre coordenadas**: sede do HKO em Observatory Hill, Tsim Sha
  Tsui (OSM: 天文台山 Observatory Hill 22.3020, 114.1743) — NÃO usar o centro
  da cidade nem o aeroporto.

## Fronteiras dos buckets (CRÍTICO para predict.py)

Labels do mercado são graus inteiros ("26°C" ... "34°C") com bordas
"25°C or below" e "35°C or higher", mas o dado oficial tem UMA casa decimal.
A semântica é TRUNCAMENTO (parte inteira), não arredondamento:

- bucket "N°C" ⇔ máxima oficial em [N.0, N.9]
- "25°C or below" ⇔ máxima ≤ 25.9
- "35°C or higher" ⇔ máxima ≥ 35.0

Evidência empírica (mercados já resolvidos, verificados via Gamma em 21/jul):

- 19/jul/2026: Absolute Daily Max = 30.6°C → resolveu "30°C"
  (arredondamento teria dado "31°C")
- 20/jul/2026: Absolute Daily Max = 29.7°C → resolveu "29°C"
  (arredondamento teria dado "30°C")

predict.py para Hong Kong: NÃO arredondar membros do ensemble para inteiro;
aplicar floor() ao valor com 1 decimal e mapear para o bucket do inteiro
resultante.

## Trecho da descrição original (auditoria)

> This market will resolve to the temperature range that contains the highest
> temperature recorded by the Hong Kong Observatory in degrees Celsius on 21 Jul '26.
>
> The resolution source for this market will be information from the Hong Kong
> Observatory, specifically the "Absolute Daily Max (deg. C)" the specified date
> once information is finalized in the relevant "Daily Extract", available here:
> https://www.weather.gov.hk/en/cis/climat.htm
>
> This market can not resolve until data for this date has been published.
>
> The resolution source for this market measures temperatures in Celsius to one
> decimal place (eg, 9.1°C). Thus, this is the level of precision that will be
> used when resolving the market.
>
> Any revisions to temperatures recorded after data is initially published for
> this market's timeframe will not be considered for this market's resolution.
