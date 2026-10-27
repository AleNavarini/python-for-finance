# python-for-finance

Backtests en Python para finanzas cuantitativas. El primer proyecto evalúa si salir del mercado durante episodios extremos del Crypto Fear and Greed Index (CFGI) mejora una estrategia de momentum de series de tiempo sobre BTC/USDT o ETH/USDT.

Todo el código vive en `src/tesina_backtest/`. El período de evaluación tiene fecha de fin fija, de modo que descargar datos nuevos no cambia los resultados.

## Instalación

Requiere Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync --python 3.12
```

## Ejecución

```bash
uv run python -m tesina_backtest                 # grilla completa con costos (~15 segundos)
uv run python -m tesina_backtest --sin-costos    # la misma grilla con retornos brutos
uv run python -m tesina_backtest --asset ETH     # la misma grilla sobre ETH/USDT (por defecto BTC)
uv run python -m tesina_backtest --refresh       # vuelve a descargar los datos
```

Cada ejecución corre una sola grilla de configuraciones (`grid.py`): 5 ventanas de momentum (30, 60, 90, 180 y 365 días) × 3 modos del filtro (ambos, pánico, euforia) × 4 juegos de percentiles, más el momentum puro de cada ventana. Son 65 backtests. La regla original es la configuración de 90 días, modo `both` y percentiles 10/20/80/90.

`apply_costs` en `BacktestConfig` prende o apaga los costos. Con costos, los retornos descuentan comisiones y slippage y suman o restan el funding. Sin costos, son brutos. La salida va a `results/<activo>/con_costos/` o a `results/<activo>/sin_costos/`, por ejemplo `results/eth/con_costos/`:

| Archivo | Contenido |
|---|---|
| `grilla.csv` | Una fila por configuración, con las métricas de la estrategia filtrada y del momentum puro, días en refugio, exposición promedio, rotación, funding y días en corto |
| `comparacion.csv` | Diferencias contra el momentum puro de cada combinación de modo y percentiles, por ventana y en promedio |
| `runs/<ventana>_<modo>_<percentiles>/` | Detalle de cada configuración, por ejemplo `runs/90_both_0.10-0.20-0.80-0.90/` |

Las carpetas `data/` y `results/` no están versionadas. La primera ejecución descarga las cuatro series de las APIs públicas y las guarda en `data/raw/`. Las siguientes usan esos CSV, salvo que se pase `--refresh`. Como el período está cortado en `end_date`, los resultados solo cambian si alguna fuente revisa datos históricos.

## Estructura

```
src/tesina_backtest/
├── __main__.py        # CLI: corre la grilla con o sin costos
├── config.py          # BacktestConfig con todos los parámetros, y rutas
├── data.py            # descarga, cache y alineación de las cuatro series
├── strategy.py        # señal de momentum, volatilidad, régimen del CFGI y posiciones
├── backtest.py        # retornos con o sin costos, y métricas
├── grid.py            # grilla de configuraciones, tabla resumen y comparación
└── reporting.py       # CSV de un backtest
```

## Datos

| Serie | Fuente | Archivo |
|---|---|---|
| Precio de cierre diario de BTC/USDT o ETH/USDT | API pública de Binance (`/api/v3/klines`) | `btc_usdt_daily.csv`, `eth_usdt_daily.csv` |
| CFGI diario | API de Alternative.me (`/fng/`) | `cfgi_daily.csv` |
| Tasa de letras del Tesoro a 3 meses (DTB3) | FRED | `dtb3_daily.csv` |
| Funding del perpetuo BTCUSDT o ETHUSDT | API pública de Binance Futures (`/fapi/v1/fundingRate`) | `btc_usdt_funding_daily.csv`, `eth_usdt_funding_daily.csv` |

`data.load_dataset` une las series por fecha. El precio y el CFGI se unen por intersección. DTB3 se completa hacia adelante en fines de semana y feriados. El funding se agrega por día sumando los tres pagos, y el pago de las 00:00 UTC se asigna al día anterior porque corresponde a la posición mantenida durante ese día. Antes del lanzamiento del perpetuo (10/09/2019 para BTC, 27/11/2019 para ETH) el funding es cero. El CFGI es el mismo para ambos activos.

El período evaluado va del 2019-09-01 al 2026-09-19 y tiene 2.575 observaciones operables.

## Metodología

### Señal de momentum

La señal es el signo de la suma de los retornos logarítmicos de los últimos `momentum_window` días. Mayor o igual a cero es largo, negativo es corto.

### Tamaño de la posición

La volatilidad es la EWMA de RiskMetrics sobre retornos logarítmicos con media cero, λ = 0,94 y 60 observaciones mínimas, anualizada con 365 días. La exposición es `target_volatility / volatilidad`, con tope en 1, es decir sin apalancamiento. El objetivo es 40% anual.

La posición objetivo es `signo × exposición`. Una banda de no operación evita rebalanceos menores a 0,10 de exposición, salvo cuando el objetivo es cero, que siempre se ejecuta.

### Régimen del CFGI

Los umbrales son percentiles de toda la historia del CFGI hasta el día anterior (ventana expansiva sobre el CFGI desplazado un día), con un mínimo de 365 observaciones. Así ningún umbral usa información del día en que se aplica.

El régimen tiene histéresis. Con la configuración original:

- Se entra en pánico cuando el CFGI cae a su percentil 10 o menos, y se sale cuando vuelve a su percentil 20 o más.
- Se entra en euforia cuando el CFGI llega a su percentil 90 o más, y se sale cuando vuelve a su percentil 80 o menos.

Fuera del régimen normal la estrategia filtrada no tiene exposición a BTC. `filter_mode` define qué extremos se usan: `both`, `panic`, `euphoria` o `none`. Las columnas `panic_enter`, `panic_exit`, `euphoria_exit` y `euphoria_enter` de los resultados guardan los umbrales de cada día.

### Ejecución y retornos

La posición del día t+1 se decide a las 00:00 UTC de t+1 con el cierre del día t y el CFGI del día t+1, que Alternative.me publica en ese mismo instante. Se ejecuta en ese momento y se mantiene durante todo el día t+1, así que captura el retorno entre el cierre de t y el cierre de t+1. Por eso `risk_regime` en la fila t es el régimen de t+1, `strategy_position` y `momentum_position` son la señal desplazada un día, y `risk_regime_held` es el régimen vigente durante el día.

El retorno diario es `posición × retorno simple de BTC + (1 − |posición|) × retorno del refugio`. El refugio rinde DTB3 convertida a tasa diaria compuesta, `(1 + DTB3/100)^(1/365) − 1`, con piso en cero.

### Costos

Los largos se operan al contado y los cortos en el perpetuo. Cada pata paga su costo sobre el valor absoluto de su cambio de posición, incluida la apertura del primer día.

| Concepto | Contado | Perpetuo |
|---|---|---|
| Comisión de tomador, usuario regular sin descuento | 10 pb | 5 pb |
| Slippage supuesto | 2 pb | 2 pb |
| Total por lado | 12 pb | 7 pb |

La pata corta cobra el funding cuando la tasa es positiva y lo paga cuando es negativa: `funding = −min(posición, 0) × tasa`.

El slippage de 2 pb se fijó por encima de una medición puntual del libro de órdenes de Binance en octubre de 2026, que dio menos de 0,3 pb para órdenes de 100.000 dólares y cerca de 2 pb para un millón. El script de esa medición no forma parte del repositorio.

La rotación anual es la suma de `|posición_t − posición_t−1|` dividida por los años del período.

### Métricas

`backtest.compute_metrics` usa el accessor de retornos de VectorBT con frecuencia diaria y años de 365 días:

- Retorno total, retorno anualizado, volatilidad anualizada y máximo drawdown, sobre los retornos de la estrategia.
- Sharpe y Sortino, sobre el exceso de retorno respecto del refugio.

## Conclusiones

Promedio sobre las cinco ventanas de momentum, con costos, de filtro menos momentum puro (`avg_dSharpe`):

| Filtro | BTC | ETH |
|---|---|---|
| Solo pánico, percentiles 15/25 | +0,20 | +0,13 |
| Solo pánico, percentiles 20/30 | +0,16 | +0,14 |
| Regla original (90 días, ambos modos, 10/20) | −0,34 | −0,40 |
| Solo euforia | −0,16 a −0,50 | −0,15 a −0,61 |

Solo el filtro de pánico con percentiles amplios mejora el Sharpe, y lo hace en ambos activos. Salir del mercado en euforia empeora el resultado en todas las ventanas. El CFGI mide el sentimiento de Bitcoin, así que en ETH la señal está menos alineada con el activo.

## Parámetros

Todos los parámetros están en `BacktestConfig` (`config.py`). `grid.grid_configs` crea cada configuración de la grilla con `dataclasses.replace`.

| Parámetro | Valor | Descripción |
|---|---|---|
| `start_date`, `end_date` | 2019-09-01, 2026-09-19 | Período evaluado |
| `asset` | `BTC` | Activo operado: `BTC` o `ETH` |
| `momentum_window` | 90 | Días de la señal |
| `volatility_span_lambda` | 0,94 | λ de la EWMA |
| `volatility_min_periods` | 60 | Mínimo de observaciones para la volatilidad |
| `target_volatility` | 0,40 | Volatilidad anual objetivo |
| `cfgi_warmup` | 365 | Historia mínima del CFGI para calcular percentiles |
| `panic_enter_quantile`, `panic_exit_quantile` | 0,10, 0,20 | Umbrales de pánico |
| `euphoria_exit_quantile`, `euphoria_enter_quantile` | 0,80, 0,90 | Umbrales de euforia |
| `filter_mode` | `both` | Extremos que activan el refugio |
| `no_trade_band` | 0,10 | Cambio mínimo de exposición para rebalancear |
| `apply_costs` | `True` | Descuenta costos y funding de los retornos |
| `spot_fee_bps`, `perp_fee_bps`, `slippage_bps` | 10, 5, 2 | Costos por lado |

Los juegos de percentiles de la grilla están en `grid.QUANTILE_SETS`, en el orden (entrada pánico, salida pánico, salida euforia, entrada euforia):

| Juego | Pánico | Euforia |
|---|---|---|
| 05/10 | 0,05 / 0,10 | 0,95 / 0,90 |
| 10/20 (original) | 0,10 / 0,20 | 0,90 / 0,80 |
| 15/25 | 0,15 / 0,25 | 0,85 / 0,75 |
| 20/30 | 0,20 / 0,30 | 0,80 / 0,70 |

## Resultados

Cada configuración guarda en su carpeta de `runs/` los archivos `metrics.csv`, `daily_returns.csv`, `backtest_daily.csv`, `annual_returns.csv` y `annual_volatility.csv`. Las columnas `strategy` y `momentum` son netas o brutas según la carpeta (`con_costos` o `sin_costos`).

En `comparacion.csv` cada columna es una diferencia contra el momentum puro de la misma ventana. `dSh_*`, `avg_dSharpe` y `avg_dRet` son filtro menos momentum. `avg_dMaxDD` también es filtro menos momentum, y como el drawdown es negativo, un valor positivo indica una caída menor del filtro. `avg_dVol` es momentum menos filtro, así que un valor positivo indica menor volatilidad del filtro. `wins_Sharpe` cuenta en cuántas de las cinco ventanas el filtro supera al momentum puro en Sharpe.
