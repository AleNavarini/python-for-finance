"""Grilla única de configuraciones: ventana de momentum, modo del filtro y percentiles."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pandas as pd

from tesina_backtest.backtest import BacktestResult, run_backtest
from tesina_backtest.config import RESULTS_DIR, BacktestConfig
from tesina_backtest.reporting import save_results
from tesina_backtest.strategy import FILTER_MODES, build_signals

MOMENTUM_WINDOWS = (30, 60, 90, 180, 365)

QUANTILE_SETS = (
    (0.05, 0.10, 0.90, 0.95),
    (0.10, 0.20, 0.80, 0.90),
    (0.15, 0.25, 0.75, 0.85),
    (0.20, 0.30, 0.70, 0.80),
)
"""Juegos de percentiles: (entrada pánico, salida pánico, salida euforia, entrada euforia)."""


def grid_configs(base: BacktestConfig) -> Iterator[BacktestConfig]:
    """Genera las configuraciones de la grilla a partir de una configuración base.

    Recorre cada ventana de `MOMENTUM_WINDOWS`, cada modo de `FILTER_MODES` y cada juego
    de `QUANTILE_SETS`. El modo "none" no usa percentiles, así que se corre una sola vez
    por ventana. El resto de los parámetros, como `apply_costs`, se toma de `base`.

    Args:
        base: Configuración de la que se copian los parámetros que no varían.

    Yields:
        Una configuración por combinación de ventana, modo y percentiles.
    """
    for window in MOMENTUM_WINDOWS:
        for mode in FILTER_MODES:
            quantile_sets = QUANTILE_SETS if mode != "none" else QUANTILE_SETS[1:2]
            for panic_enter, panic_exit, euphoria_exit, euphoria_enter in quantile_sets:
                yield replace(
                    base,
                    momentum_window=window,
                    filter_mode=mode,
                    panic_enter_quantile=panic_enter,
                    panic_exit_quantile=panic_exit,
                    euphoria_exit_quantile=euphoria_exit,
                    euphoria_enter_quantile=euphoria_enter,
                )


def _quantiles(config: BacktestConfig) -> list[float]:
    """Devuelve los cuatro percentiles de la configuración, en el orden de `QUANTILE_SETS`."""
    return [
        config.panic_enter_quantile,
        config.panic_exit_quantile,
        config.euphoria_exit_quantile,
        config.euphoria_enter_quantile,
    ]


def _run_name(config: BacktestConfig) -> str:
    """Devuelve el nombre de la carpeta de la corrida, por ejemplo "90_panic_0.20-0.30-0.70-0.80"."""
    if config.filter_mode == "none":
        return f"{config.momentum_window}_none"
    quantiles = "-".join(f"{q:.2f}" for q in _quantiles(config))
    return f"{config.momentum_window}_{config.filter_mode}_{quantiles}"


def _summary_row(result: BacktestResult, config: BacktestConfig) -> dict[str, object]:
    """Resume una corrida en una fila de `grilla.csv`.

    La fila identifica la configuración y, para la estrategia con filtro (`strategy_*`)
    y el momentum puro (`momentum_*`), incluye las métricas agregadas, la exposición
    absoluta promedio, la rotación anual (contando la apertura del primer día), el
    funding anual de la pata corta y la fracción de días en corto. El funding se informa
    aunque `apply_costs` sea False.

    También mide la actividad del filtro: días observados, días en refugio, fracción en
    refugio y `momentum_pnl_forgone`, el retorno bruto que obtuvo el momentum puro en los
    días en que el filtro estuvo fuera del activo.

    Args:
        result: Resultado de `run_backtest`.
        config: Configuración de la corrida.

    Returns:
        Diccionario con una entrada por columna de `grilla.csv`.
    """
    frame = result.frame
    years = len(frame) / config.annualization_days

    row: dict[str, object] = {
        "momentum_window": config.momentum_window,
        "filter_mode": config.filter_mode,
        "quantiles": "none"
        if config.filter_mode == "none"
        else "/".join(f"{q:.2f}" for q in _quantiles(config)),
    }

    for name in ("strategy", "momentum"):
        for metric, value in result.metrics.loc[name].items():
            row[f"{name}_{metric}"] = value

        position = frame[f"{name}_position"]
        row[f"{name}_avg_abs_exposure"] = float(position.abs().mean())
        row[f"{name}_turnover"] = float(position.diff().fillna(position.abs()).abs().sum() / years)
        row[f"{name}_funding_annual"] = float(
            (-position.clip(upper=0.0) * frame["funding_rate"]).sum() / years
        )
        row[f"{name}_short_days"] = float((position < 0).mean())

    out_of_market = frame["risk_regime_held"] == 0.0
    momentum_pnl_while_out = (
        frame.loc[out_of_market, "momentum_position"] * frame.loc[out_of_market, "asset_return"]
    )

    row["observations"] = float(len(frame))
    row["days_in_refuge"] = float(out_of_market.sum())
    row["share_in_refuge"] = float(out_of_market.mean())
    row["momentum_pnl_forgone"] = float((1.0 + momentum_pnl_while_out).prod() - 1.0)

    return row


def _comparison_table(summary: pd.DataFrame) -> pd.DataFrame:
    """Compara cada combinación de modo y percentiles contra el momentum puro.

    Para cada corrida con filtro calcula la diferencia contra el momentum puro de la
    misma ventana en Sharpe, retorno anualizado, máximo drawdown y volatilidad. Un valor
    positivo siempre favorece al filtro: en drawdown indica una caída menor y en
    volatilidad se resta al revés (momentum menos filtro).

    Args:
        summary: Tabla de `grilla.csv`.

    Returns:
        Una fila por combinación con la diferencia de Sharpe en cada ventana (`dSh_<ventana>`),
        la cantidad de ventanas en que el filtro gana en Sharpe (`wins_Sharpe`) y el
        promedio de cada diferencia entre ventanas, ordenada por `avg_dSharpe`.
    """
    filtered = summary.loc[summary["filter_mode"] != "none"].copy()
    filtered["label"] = filtered["filter_mode"] + "  " + filtered["quantiles"].str.slice(0, 9)
    filtered["dSharpe"] = filtered["strategy_sharpe"] - filtered["momentum_sharpe"]
    filtered["dRet"] = (
        filtered["strategy_annualized_return"] - filtered["momentum_annualized_return"]
    )
    filtered["dMaxDD"] = filtered["strategy_max_drawdown"] - filtered["momentum_max_drawdown"]
    filtered["dVol"] = (
        filtered["momentum_annualized_volatility"] - filtered["strategy_annualized_volatility"]
    )

    by_window = filtered.pivot(index="label", columns="momentum_window", values="dSharpe")
    by_window.columns = [f"dSh_{window}" for window in by_window.columns]

    grouped = filtered.groupby("label")
    table = by_window.assign(
        wins_Sharpe=grouped["dSharpe"].apply(lambda values: int((values > 0).sum())),
        avg_dSharpe=grouped["dSharpe"].mean(),
        avg_dRet=grouped["dRet"].mean(),
        avg_dMaxDD=grouped["dMaxDD"].mean(),
        avg_dVol=grouped["dVol"].mean(),
    )

    return table.sort_values("avg_dSharpe", ascending=False).reset_index()


def run_grid(dataset: pd.DataFrame, base: BacktestConfig) -> None:
    """Corre todas las configuraciones de la grilla y guarda los resultados.

    Los resultados van a `results/con_costos` o `results/sin_costos` según
    `base.apply_costs`. Cada corrida se guarda en `runs/<nombre>/`, el resumen en
    `grilla.csv` y la comparación contra el momentum puro en `comparacion.csv`, que
    además se imprime en la terminal.

    Args:
        dataset: Dataset diario de `load_dataset`.
        base: Configuración base de la grilla.
    """
    output_dir = (
        RESULTS_DIR / base.asset.lower() / ("con_costos" if base.apply_costs else "sin_costos")
    )
    rows: list[dict[str, object]] = []

    for config in grid_configs(base):
        result = run_backtest(build_signals(dataset, config), config)
        save_results(result, output_dir / "runs" / _run_name(config))
        rows.append(_summary_row(result, config))

    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "grilla.csv", index=False)

    comparison = _comparison_table(summary)
    comparison.to_csv(output_dir / "comparacion.csv", index=False)

    pd.set_option("display.width", 200)
    print(comparison.round(4).to_string(index=False))
