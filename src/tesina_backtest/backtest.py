"""Cálculo de retornos, costos y métricas de desempeño a partir de las posiciones diarias."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import vectorbt as vbt

from tesina_backtest.config import BacktestConfig

POSITION_COLUMNS = {"strategy": "strategy_position", "momentum": "momentum_position"}


@dataclass
class BacktestResult:
    """Resultado de un backtest.

    Attributes:
        frame: Tabla diaria con precios, señales, posiciones y retorno del refugio.
        returns: Retornos diarios de `strategy` y `momentum`, netos o brutos según `apply_costs`.
        equity: Curvas de capital de cada estrategia, que arrancan en `initial_cash`.
        metrics: Métricas agregadas, una fila por estrategia.
    """

    frame: pd.DataFrame
    returns: pd.DataFrame
    equity: pd.DataFrame
    metrics: pd.DataFrame


def annualized_tbill_return(dtb3: pd.Series, annualization_days: int) -> pd.Series:
    """Convierte la tasa DTB3 en el retorno diario compuesto del activo de refugio.

    Args:
        dtb3: Rendimiento anual de la letra del Tesoro a 3 meses, en porcentaje.
            Las tasas negativas se recortan a cero.
        annualization_days: Días por año.

    Returns:
        Retorno diario equivalente, (1 + tasa anual) ** (1 / días) - 1.
    """
    annual_yield = dtb3.clip(lower=0.0) / 100.0
    return (1.0 + annual_yield) ** (1.0 / annualization_days) - 1.0


def net_of_costs(
    gross: pd.Series,
    position: pd.Series,
    funding_rate: pd.Series,
    spot_cost: float,
    perp_cost: float,
) -> pd.Series:
    """Descuenta los costos de operar y aplica el funding a los retornos brutos.

    Los largos se operan al contado y los cortos en el perpetuo, cada uno con su costo
    por lado sobre el monto operado. El primer día cuenta como la apertura de la
    posición desde cero. La pata corta cobra el funding cuando la tasa es positiva y lo
    paga cuando es negativa.

    Args:
        gross: Retornos diarios brutos.
        position: Posición diaria en el activo, entre -1 y 1.
        funding_rate: Tasa de funding diaria del perpetuo.
        spot_cost: Costo por lado al contado, como fracción (comisión más slippage).
        perp_cost: Costo por lado en el perpetuo, como fracción.

    Returns:
        Retornos diarios netos.
    """
    long_leg = position.clip(lower=0.0)
    short_leg = position.clip(upper=0.0)

    spot_traded = long_leg.diff().fillna(long_leg).abs()
    perp_traded = short_leg.diff().fillna(short_leg).abs()
    funding = -short_leg * funding_rate

    return gross - spot_cost * spot_traded - perp_cost * perp_traded + funding


def compute_metrics(
    returns: pd.DataFrame, risk_free: pd.Series, config: BacktestConfig
) -> pd.DataFrame:
    """Calcula las métricas agregadas de cada columna de retornos con vectorbt.

    El retorno, la volatilidad y el drawdown se calculan sobre los retornos. El Sharpe
    y el Sortino, sobre el exceso de retorno respecto de `risk_free`. Todo se anualiza
    con `config.annualization_days`.

    Args:
        returns: Retornos diarios, una columna por estrategia.
        risk_free: Retorno diario del activo de refugio.
        config: Parámetros del backtest.

    Returns:
        Una fila por estrategia con `total_return`, `annualized_return`,
        `annualized_volatility`, `sharpe`, `sortino` y `max_drawdown`.
    """
    rows: dict[str, dict[str, float]] = {}

    for name in returns.columns:
        accessor = vbt.returns.accessors.ReturnsSRAccessor(
            returns[name],
            freq="1D",
            year_freq=f"{config.annualization_days}D",
        )
        excess = vbt.returns.accessors.ReturnsSRAccessor(
            returns[name] - risk_free,
            freq="1D",
            year_freq=f"{config.annualization_days}D",
        )

        rows[name] = {
            "total_return": accessor.total(),
            "annualized_return": accessor.annualized(),
            "annualized_volatility": accessor.annualized_volatility(),
            "sharpe": excess.sharpe_ratio(),
            "sortino": excess.sortino_ratio(),
            "max_drawdown": accessor.max_drawdown(),
        }

    return pd.DataFrame(rows).T


def run_backtest(signal_frame: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
    """Corre el backtest de la estrategia con CFGI y del momentum puro.

    Recorta la tabla al período de evaluación y descarta los días sin datos
    suficientes. Cada día, la fracción expuesta al activo gana el retorno del activo y el
    resto queda en el activo de refugio (DTB3). Si `config.apply_costs` es True, los
    retornos son netos de costos (ver `net_of_costs`).

    Args:
        signal_frame: Tabla diaria de `build_signals`.
        config: Parámetros del backtest.

    Returns:
        Tabla diaria, retornos, curvas de capital y métricas de ambas estrategias.

    Raises:
        ValueError: Si no queda ningún día operable en el período.
    """
    period = (signal_frame.index >= pd.Timestamp(config.start_date)) & (
        signal_frame.index <= pd.Timestamp(config.end_date)
    )
    required = ["asset_return", "dtb3", "funding_rate", "momentum_position", "strategy_position"]
    frame = signal_frame.loc[period].dropna(subset=required).copy()
    if frame.empty:
        raise ValueError("No existen observaciones operables para el período solicitado")

    frame["refuge_return"] = annualized_tbill_return(frame["dtb3"], config.annualization_days)

    spot_cost = (config.spot_fee_bps + config.slippage_bps) / 10_000
    perp_cost = (config.perp_fee_bps + config.slippage_bps) / 10_000

    returns = pd.DataFrame(index=frame.index)
    for name, position_column in POSITION_COLUMNS.items():
        position = frame[position_column]
        refuge_weight = 1.0 - position.abs()
        returns[name] = position * frame["asset_return"] + refuge_weight * frame["refuge_return"]

        if config.apply_costs:
            returns[name] = net_of_costs(
                returns[name], position, frame["funding_rate"], spot_cost, perp_cost
            )

    equity = config.initial_cash * (1.0 + returns).cumprod()
    metrics = compute_metrics(returns, frame["refuge_return"], config)

    return BacktestResult(frame, returns, equity, metrics)
