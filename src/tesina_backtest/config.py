"""Parámetros del backtest y rutas del proyecto."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BacktestConfig:
    """Parámetros de una corrida del backtest.

    Es inmutable: para variar un parámetro se crea una copia con `dataclasses.replace`.

    Attributes:
        asset: Activo operado, "BTC" o "ETH". El CFGI es el mismo para ambos.
        start_date: Inicio del período de evaluación, alineado con el lanzamiento del
            perpetuo BTCUSDT (10/09/2019); para otros activos el funding es cero hasta el lanzamiento de su perpetuo. Los días previos usan funding cero.
        end_date: Fin fijo del período, para que los resultados no cambien al descargar datos nuevos.
        momentum_window: Días de retornos logarítmicos que suma la señal de momentum.
        volatility_span_lambda: Factor de decaimiento de la volatilidad EWMA de RiskMetrics.
        volatility_min_periods: Días mínimos de datos antes de que la volatilidad EWMA tenga valor.
        target_volatility: Volatilidad anual objetivo. La exposición es objetivo sobre
            volatilidad estimada, con tope en 1.
        cfgi_warmup: Observaciones previas del CFGI necesarias para calcular percentiles.
        panic_enter_quantile: Percentil del CFGI en o debajo del cual se entra en pánico.
        panic_exit_quantile: Percentil del CFGI en o encima del cual se sale del pánico.
        euphoria_exit_quantile: Percentil del CFGI en o debajo del cual se sale de la euforia.
        euphoria_enter_quantile: Percentil del CFGI en o encima del cual se entra en euforia.
        filter_mode: Regímenes que activa el filtro: "both", "panic", "euphoria" o "none".
        no_trade_band: Cambio mínimo de exposición para rebalancear.
        apply_costs: Si es True, los retornos descuentan comisiones y slippage y suman o
            restan el funding. Si es False, son brutos.
        spot_fee_bps: Comisión por lado al contado, en puntos básicos (tomador regular de Binance).
        perp_fee_bps: Comisión por lado en el perpetuo, en puntos básicos.
        slippage_bps: Slippage por lado, en puntos básicos, en ambos mercados.
        annualization_days: Días por año para anualizar. El activo cotiza todos los días.
        initial_cash: Capital inicial de las curvas de capital.
    """

    asset: str = "BTC"
    start_date: str = "2019-09-01"
    end_date: str = "2026-09-19"
    momentum_window: int = 90
    volatility_span_lambda: float = 0.94
    volatility_min_periods: int = 60
    target_volatility: float = 0.40
    cfgi_warmup: int = 365
    panic_enter_quantile: float = 0.10
    panic_exit_quantile: float = 0.20
    euphoria_exit_quantile: float = 0.80
    euphoria_enter_quantile: float = 0.90
    filter_mode: str = "both"
    no_trade_band: float = 0.10
    apply_costs: bool = True
    spot_fee_bps: float = 10.0
    perp_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    annualization_days: int = 365
    initial_cash: float = 100.0


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"
RESULTS_DIR = ROOT_DIR / "results"
