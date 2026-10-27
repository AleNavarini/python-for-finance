"""Punto de entrada: python -m tesina_backtest [--asset BTC|ETH] [--sin-costos] [--refresh]."""

import argparse

from tesina_backtest.config import BacktestConfig
from tesina_backtest.data import load_dataset
from tesina_backtest.grid import run_grid


def main() -> None:
    """Lee los argumentos de la línea de comandos, carga los datos y corre la grilla completa."""
    parser = argparse.ArgumentParser(
        prog="python -m tesina_backtest", description="Backtests de la tesina de momentum y CFGI"
    )
    parser.add_argument("--sin-costos", action="store_true", help="Corre la grilla sin costos")
    parser.add_argument("--asset", default="BTC", choices=["BTC", "ETH"], help="Activo operado")
    parser.add_argument("--refresh", action="store_true", help="Vuelve a descargar los datos")
    args = parser.parse_args()

    dataset = load_dataset(asset=args.asset, refresh=args.refresh)
    run_grid(dataset, BacktestConfig(asset=args.asset, apply_costs=not args.sin_costos))


if __name__ == "__main__":
    main()
