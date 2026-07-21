STATUS: VALIDADO

# Regras de resolução — NYC

Gerado por extract_rules.py em 2026-07-21 16:29 UTC
a partir do evento `highest-temperature-in-nyc-on-july-21-2026`.

- **Estação de resolução exata**: LaGuardia Airport Station
- **Código ICAO**: KLGA
- **Fonte oficial (URL)**: https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA
- **Fuso horário da estação**: America/New_York
- **Unidade**: °F (Fahrenheit)
- **Regra de arredondamento**: graus inteiros (Fahrenheit) — a fonte reporta nesse nível de precisão
- **Janela diária**: dia-calendário local da estação (descrição: "all times on this day")
- **Horário de corte (fechamento de trading)**: 2026-07-21T12:00:00Z
- **Revisões**: revisões aceitas até o primeiro datapoint do dia seguinte na fonte
- **Lat da estação**: 40.7772
- **Lon da estação**: -73.8726
- **Observação sobre coordenadas**: pré-preenchido (LaGuardia Airport, NYC) — conferir

## Conversão °C→°F e fronteiras dos buckets (CRÍTICO para predict.py)

Método empírico (2026-07-21): 5 dias já resolvidos (16–20/jul), comparando o
METAR bruto da KLGA (arquivo ASOS da IEM, °C com décimos do grupo T) com o °F
inteiro publicado pelo Wunderground e o bucket pago pela Polymarket:

| dia | max °C (METAR) | °C×1.8+32 | max °F (ASOS) | bucket pago |
|-----|----------------|-----------|---------------|-------------|
| 16/jul | 31.1 | 87.98 | 88.0 | 88-89°F |
| 17/jul | 30.6 | 87.01 | 87.0 | 86-87°F |
| 18/jul | 26.7 | 80.01 | 80.0 | 80-81°F |
| 19/jul | 27.2 | 80.96 | 81.0 | 80-81°F |
| 20/jul | 27.8 | 82.04 | 82.0 | 82-83°F |

Conclusões:

1. **O sensor da KLGA (ASOS) mede nativamente °F inteiro** — a máxima diária em
   °F foi exatamente inteira nos 5 dias (88.0, 87.0, ...). O °C decimal do
   METAR é DERIVADO do °F (ex.: 88°F → 31.1°C). O Wunderground publica esse °F
   inteiro nativo, e o bucket pago o contém em todos os 5 dias.
2. **Regra de conversão que o predict.py deve replicar**: valor °C decimal →
   `°F_int = round_half_up(°C × 1.8 + 32)`. O round recupera o °F nativo nos
   5 dias; `floor` FALHA em dois (31.1→87.98→87≠88; 27.2→80.96→80≠81).
   Implementar half-up determinístico (`floor(x + 0.5)`), não o `round()` do
   Python (banker's rounding).
3. **Membros de ensemble**: pedir Open-Meteo com `temperature_unit=fahrenheit`
   e arredondar o membro (°F decimal) para inteiro half-up; equivalente a
   converter de °C e arredondar — evita dupla conversão.
4. **Fronteiras dos buckets °F**: faixas de 2 graus INTEIROS — "78-79°F" ⇔
   °F_int ∈ {78, 79}; "71°F or below" ⇔ ≤71; "90°F or above" ⇔ ≥90.

## Trecho da descrição original (auditoria)

> This market will resolve to the temperature range that contains the highest temperature recorded at the LaGuardia Airport Station in degrees Fahrenheit on 21 Jul '26.
> 
> The resolution source for this market will be information from Wunderground, specifically the highest temperature recorded for all times on this day for the LaGuardia Airport Station, available here: https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA.
> 
> To toggle between Fahrenheit and Celsius, click the gear icon next to the search bar and switch the Temperature setting between °F and °C.
> 
> This market can not re
