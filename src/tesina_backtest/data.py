"""Descarga, caché local y alineación de precios del activo, CFGI, DTB3 y funding."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from pathlib import Path
from time import sleep

import pandas as pd
import requests

from tesina_backtest.config import DATA_PROCESSED_DIR, DATA_RAW_DIR

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
CFGI_URL = "https://api.alternative.me/fng/"
DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"

REQUEST_TIMEOUT_SECONDS = 30

Downloader = Callable[[Path], pd.DataFrame]


def _get_json(url: str, params: dict[str, str | int] | None = None) -> object:
    """Hace un GET y devuelve el cuerpo JSON. Lanza una excepción ante un error HTTP."""
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def download_usdt_daily(output_path: Path, asset: str) -> pd.DataFrame:
    """Descarga los cierres diarios de ASSET/USDT del mercado de contado de Binance.

    Pagina de a 1000 velas desde 2017 y descarta la vela del día en curso, que todavía
    no cerró. Cada fecha corresponde a la vela de 00:00 a 23:59:59 UTC.

    Args:
        output_path: CSV donde se guardan los precios.
        asset: Símbolo del activo, por ejemplo "BTC".

    Returns:
        Precio de cierre diario, indexado por fecha.

    Raises:
        ValueError: Si Binance no devuelve ninguna vela.
    """
    rows: list[list[object]] = []
    start_ms = int(pd.Timestamp("2017-01-01", tz="UTC").timestamp() * 1000)

    while True:
        payload = _get_json(
            BINANCE_KLINES_URL,
            params={
                "symbol": f"{asset}USDT",
                "interval": "1d",
                "startTime": start_ms,
                "limit": 1000,
            },
        )
        if not isinstance(payload, list) or not payload:
            break

        rows.extend(payload)
        next_start_ms = int(payload[-1][0]) + 86_400_000
        if next_start_ms <= start_ms or len(payload) < 1000:
            break

        start_ms = next_start_ms
        sleep(0.1)

    if not rows:
        raise ValueError(f"Binance no devolvió precios de {asset}/USDT")

    frame = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base_volume",
            "taker_quote_volume",
            "ignore",
        ],
    )
    frame["date"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")

    completed = frame.loc[frame["close_time"] <= pd.Timestamp.now(tz="UTC"), ["date", "close"]]
    result = completed.drop_duplicates("date").set_index("date").sort_index()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path)
    return result


def download_cfgi(output_path: Path) -> pd.DataFrame:
    """Descarga la historia completa del Crypto Fear & Greed Index de Alternative.me.

    Args:
        output_path: CSV donde se guarda el índice.

    Returns:
        Valor diario del CFGI (0 a 100), indexado por fecha.

    Raises:
        TypeError: Si la respuesta no tiene el formato esperado.
    """
    payload = _get_json(CFGI_URL, params={"limit": 0, "format": "json"})
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TypeError("Alternative.me devolvió un formato de CFGI inesperado")

    frame = pd.DataFrame(payload["data"])
    frame["date"] = pd.to_datetime(
        pd.to_numeric(frame["timestamp"]), unit="s", utc=True
    ).dt.tz_localize(None)
    frame["cfgi"] = pd.to_numeric(frame["value"], errors="raise")

    result = frame[["date", "cfgi"]].drop_duplicates("date").set_index("date").sort_index()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path)
    return result


def download_dtb3(output_path: Path) -> pd.DataFrame:
    """Descarga de FRED el rendimiento diario de la letra del Tesoro a 3 meses (DTB3).

    Los feriados, que FRED publica como ".", quedan vacíos.

    Args:
        output_path: CSV donde se guarda la tasa.

    Returns:
        Rendimiento anual en porcentaje, indexado por fecha.
    """
    response = requests.get(DTB3_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    frame = pd.read_csv(StringIO(response.text))
    date_column = "DATE" if "DATE" in frame.columns else "observation_date"
    frame["date"] = pd.to_datetime(frame[date_column])
    frame["dtb3"] = pd.to_numeric(frame["DTB3"], errors="coerce")

    result = frame[["date", "dtb3"]].drop_duplicates("date").set_index("date").sort_index()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path)
    return result


def download_usdt_funding(output_path: Path, asset: str) -> pd.DataFrame:
    """Descarga las tasas de funding del perpetuo ASSETUSDT de Binance y las suma por día.

    Binance cobra funding a las 00:00, 08:00 y 16:00 UTC. El pago de las 00:00 se asigna
    al día anterior, porque corresponde a la posición mantenida ese día, igual que el
    retorno de cierre a cierre.

    Args:
        output_path: CSV donde se guarda el funding diario.
        asset: Símbolo del activo, por ejemplo "BTC".

    Returns:
        Suma de las tasas de funding de cada día, indexada por fecha.

    Raises:
        ValueError: Si Binance no devuelve ningún pago.
    """
    rows: list[dict[str, object]] = []
    start_ms = int(pd.Timestamp("2019-01-01", tz="UTC").timestamp() * 1000)

    while True:
        payload = _get_json(
            BINANCE_FUNDING_URL,
            params={"symbol": f"{asset}USDT", "startTime": start_ms, "limit": 1000},
        )
        if not isinstance(payload, list) or not payload:
            break

        rows.extend(payload)
        if len(payload) < 1000:
            break

        start_ms = int(payload[-1]["fundingTime"]) + 1
        sleep(0.1)

    if not rows:
        raise ValueError(f"Binance no devolvió tasas de funding de {asset}USDT")

    frame = pd.DataFrame(rows)
    funding_time = pd.to_datetime(pd.to_numeric(frame["fundingTime"]), unit="ms", utc=True)
    frame["date"] = (funding_time - pd.Timedelta(milliseconds=1)).dt.floor("D").dt.tz_localize(None)
    frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="raise")

    result = (
        frame.drop_duplicates("fundingTime").groupby("date")[["funding_rate"]].sum().sort_index()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path)
    return result


def _load_or_download(path: Path, downloader: Downloader, refresh: bool) -> pd.DataFrame:
    """Lee `path` si existe. Si no existe o `refresh` es True, lo descarga con `downloader`."""
    if refresh or not path.exists():
        return downloader(path)
    return pd.read_csv(path, index_col="date", parse_dates=True)


def load_dataset(asset: str = "BTC", refresh: bool = False) -> pd.DataFrame:
    """Carga las cuatro fuentes y las alinea en un dataset diario.

    Solo quedan los días con precio y CFGI. La DTB3 se completa con el último valor
    publicado (fines de semana y feriados) y el funding es cero antes del lanzamiento del
    perpetuo. El resultado se guarda en `data/processed/daily_dataset.csv`.

    Args:
        asset: Símbolo del activo, por ejemplo "BTC" o "ETH".
        refresh: Si es True, vuelve a descargar todas las fuentes.

    Returns:
        Dataset diario con `close`, `cfgi`, `dtb3` y `funding_rate`.

    Raises:
        ValueError: Si después de alinear quedan huecos en precio, CFGI o DTB3.
    """
    name = asset.lower()
    prices = _load_or_download(
        DATA_RAW_DIR / f"{name}_usdt_daily.csv",
        lambda path: download_usdt_daily(path, asset),
        refresh,
    )
    cfgi = _load_or_download(DATA_RAW_DIR / "cfgi_daily.csv", download_cfgi, refresh)
    dtb3 = _load_or_download(DATA_RAW_DIR / "dtb3_daily.csv", download_dtb3, refresh)
    funding = _load_or_download(
        DATA_RAW_DIR / f"{name}_usdt_funding_daily.csv",
        lambda path: download_usdt_funding(path, asset),
        refresh,
    )

    dataset = prices.join(cfgi, how="inner").join(dtb3, how="left").join(funding, how="left")
    dataset["dtb3"] = dataset["dtb3"].ffill()
    dataset["funding_rate"] = dataset["funding_rate"].fillna(0.0)

    if dataset[["close", "cfgi", "dtb3"]].isna().any().any():
        missing = dataset[["close", "cfgi", "dtb3"]].isna().sum().to_dict()
        raise ValueError(f"El dataset alineado contiene valores faltantes: {missing}")

    DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(DATA_PROCESSED_DIR / f"{name}_daily_dataset.csv")
    return dataset
