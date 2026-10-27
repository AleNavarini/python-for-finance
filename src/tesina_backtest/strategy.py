"""Señales de la estrategia: momentum, volatilidad objetivo, régimen del CFGI y posiciones."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tesina_backtest.config import BacktestConfig

FILTER_MODES = ("both", "panic", "euphoria", "none")


def calculate_cfgi_risk_regime(
    cfgi: pd.Series,
    config: BacktestConfig,
) -> tuple[pd.Series, pd.DataFrame]:
    """Calcula el régimen de sentimiento diario a partir del CFGI.

    Los umbrales de cada día son percentiles expansivos del CFGI hasta el día anterior,
    con al menos `config.cfgi_warmup` observaciones. El régimen tiene histéresis: desde
    normal se entra en pánico con el CFGI en o debajo de `panic_enter` y en euforia con el
    CFGI en o encima de `euphoria_enter`, y se vuelve a normal recién al cruzar
    `panic_exit` o `euphoria_exit`. `config.filter_mode` define qué regímenes se usan.

    Args:
        cfgi: Valor diario del CFGI.
        config: Parámetros del backtest.

    Returns:
        El régimen diario (1 en normal, 0 en pánico o euforia, vacío durante el
        calentamiento) y una tabla con los cuatro umbrales diarios (`panic_enter`,
        `panic_exit`, `euphoria_exit` y `euphoria_enter`).

    Raises:
        ValueError: Si `config.filter_mode` no está en `FILTER_MODES`.
    """
    if config.filter_mode not in FILTER_MODES:
        raise ValueError(f"filter_mode debe ser uno de {FILTER_MODES}")

    uses_panic = config.filter_mode in ("both", "panic")
    uses_euphoria = config.filter_mode in ("both", "euphoria")

    history = cfgi.shift(1).expanding(min_periods=config.cfgi_warmup)
    thresholds = pd.DataFrame(
        {
            "panic_enter": history.quantile(config.panic_enter_quantile),
            "panic_exit": history.quantile(config.panic_exit_quantile),
            "euphoria_exit": history.quantile(config.euphoria_exit_quantile),
            "euphoria_enter": history.quantile(config.euphoria_enter_quantile),
        },
        index=cfgi.index,
    )

    regime = pd.Series(np.nan, index=cfgi.index, dtype=float, name="risk_regime")
    state = "normal"

    for date in cfgi.index:
        row = thresholds.loc[date]
        if row.isna().any():
            continue

        value = cfgi.loc[date]
        if state == "normal" and uses_panic and value <= row["panic_enter"]:
            state = "panic"
        elif state == "normal" and uses_euphoria and value >= row["euphoria_enter"]:
            state = "euphoria"
        elif (state == "panic" and value >= row["panic_exit"]) or (
            state == "euphoria" and value <= row["euphoria_exit"]
        ):
            state = "normal"

        regime.loc[date] = float(state == "normal")

    return regime, thresholds


def apply_no_trade_band(target: pd.Series, band: float) -> pd.Series:
    """Convierte la exposición objetivo en posiciones con una banda de no operación.

    La posición se actualiza al objetivo solo si el cambio es de al menos `band`, o si
    el objetivo es cero (salida a refugio). Si no, se mantiene la posición anterior. La
    posición inicial es cero.

    Args:
        target: Exposición objetivo diaria. Los días vacíos se ignoran.
        band: Cambio mínimo de exposición para rebalancear.

    Returns:
        Posición diaria, vacía en los días sin objetivo.
    """
    positions = pd.Series(np.nan, index=target.index, dtype=float, name="position")
    current = 0.0

    for date, desired in target.items():
        if pd.isna(desired):
            continue
        if desired == 0.0 or abs(desired - current) >= band:
            current = float(desired)
        positions.loc[date] = current

    return positions


def build_signals(dataset: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Calcula las señales y las posiciones diarias del momentum puro y de la estrategia con CFGI.

    La dirección es el signo de la suma de retornos logarítmicos de
    `config.momentum_window` días (largo si es mayor o igual a cero). La exposición es
    `config.target_volatility` sobre la volatilidad EWMA de RiskMetrics anualizada, con
    tope en 1. La estrategia con CFGI lleva la exposición a cero cuando el régimen está
    en pánico o euforia. Ambos objetivos pasan por la banda de no operación.

    La posición se decide a las 00:00 UTC de t+1 con el cierre de t y el CFGI de t+1, que
    Alternative.me publica en ese instante. Por eso `risk_regime` en la fila t es el
    régimen de t+1. La posición se ejecuta en ese momento y se mantiene durante t+1, así
    que `*_position` y `risk_regime_held` están corridas un día respecto de las señales.

    Args:
        dataset: Dataset diario de `load_dataset`.
        config: Parámetros del backtest.

    Returns:
        El dataset con retornos, momentum, volatilidad, umbrales, régimen, objetivos y
        posiciones de ambas estrategias.
    """
    frame = dataset.copy()

    frame["asset_return"] = frame["close"].pct_change()
    frame["asset_log_return"] = np.log(frame["close"]).diff()

    frame["momentum"] = frame["asset_log_return"].rolling(config.momentum_window).sum()
    frame["direction"] = np.where(frame["momentum"] >= 0, 1.0, -1.0)
    frame.loc[frame["momentum"].isna(), "direction"] = np.nan

    daily_variance = (
        frame["asset_log_return"]
        .pow(2)
        .ewm(
            alpha=1 - config.volatility_span_lambda,
            adjust=False,
            min_periods=config.volatility_min_periods,
        )
        .mean()
    )
    frame["annualized_volatility"] = np.sqrt(daily_variance) * np.sqrt(config.annualization_days)
    frame["scaled_exposure"] = (config.target_volatility / frame["annualized_volatility"]).clip(
        upper=1.0
    )

    regime, thresholds = calculate_cfgi_risk_regime(frame["cfgi"], config)
    frame = frame.join(thresholds)
    frame["risk_regime"] = regime.shift(-1)

    momentum_target = frame["direction"] * frame["scaled_exposure"]
    strategy_target = momentum_target * frame["risk_regime"]
    frame["momentum_target"] = momentum_target
    frame["strategy_target"] = strategy_target

    frame["momentum_position_signal"] = apply_no_trade_band(momentum_target, config.no_trade_band)
    frame["strategy_position_signal"] = apply_no_trade_band(strategy_target, config.no_trade_band)

    frame["momentum_position"] = frame["momentum_position_signal"].shift(1)
    frame["strategy_position"] = frame["strategy_position_signal"].shift(1)
    frame["risk_regime_held"] = frame["risk_regime"].shift(1)

    return frame
