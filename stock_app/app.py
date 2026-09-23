import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from .prediction.schemas import PredictionError
from .prediction.service import predict
from .stock_service import (
    find_ticker,
    get_history,
    get_stock_info,
    list_symbols,
    search_symbols,
)


BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "stocks_dashboard"


# This stateless, local research API has no authenticated browser session.
# Unsafe requests are still checked for a same-origin Origin/Referer below.
app = Flask(  # NOSONAR python:S4502 -- no session-authenticated state is exposed
    __name__,
    static_folder=str(STATIC_DIR),
    static_url_path="",
)

app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32),
)

# Disable static caching during development.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.before_request
def _enforce_same_origin_for_mutations():
    """Reject browser-originated cross-site mutation requests.

    API clients without Origin/Referer headers remain supported because this
    application does not use cookie-authenticated sessions. Browsers send one
    of these headers for the POST endpoint, which prevents cross-site form and
    fetch requests from triggering background training.
    """
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return None

    parsed = urlparse(source)
    if parsed.scheme != request.scheme or parsed.netloc != request.host:
        return jsonify(
            {
                "error": "Cross-origin mutation blocked",
                "code": "CSRF_BLOCKED",
            }
        ), 403

    return None


RANGE_MAP = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "ytd": "ytd",
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
    "all": 4000,
}


def _json_safe_records(frame: pd.DataFrame) -> list[dict]:
    """
    Convert a DataFrame into JSON-safe records.

    NaN values are converted to None so Flask can serialize them
    correctly as JSON null values.
    """
    safe = frame.astype(object).where(
        pd.notna(frame),
        None,
    )

    return safe.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


@app.get("/")
def serve_index():
    return send_from_directory(
        STATIC_DIR,
        "index.html",
    )


# ---------------------------------------------------------------------------
# Stock information
# ---------------------------------------------------------------------------


@app.get("/api/info")
def api_info():
    ticker_input = request.args.get(
        "ticker",
        "",
    ).strip()

    if not ticker_input:
        return jsonify(
            {
                "error": "ticker is required",
            }
        ), 400

    try:
        ticker = find_ticker(ticker_input)
        info = get_stock_info(ticker)

        return jsonify(info)

    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "error": str(exc),
            }
        ), 400


# ---------------------------------------------------------------------------
# Historical price data
# ---------------------------------------------------------------------------


@app.get("/api/history")
def api_history():
    ticker_input = request.args.get(
        "ticker",
        "",
    ).strip()

    range_key = request.args.get(
        "range",
        "1m",
    )

    interval = (
        request.args.get(
            "interval",
            "",
        )
        .strip()
        .lower()
        or None
    )

    if not ticker_input:
        return jsonify(
            {
                "error": "ticker is required",
            }
        ), 400

    days_value = RANGE_MAP.get(
        range_key,
        RANGE_MAP["1m"],
    )

    if days_value == "ytd":
        today = datetime.now().date()

        year_start = datetime(
            year=today.year,
            month=1,
            day=1,
        ).date()

        days = max(
            (today - year_start).days + 1,
            1,
        )

    else:
        days = int(days_value)

    try:
        ticker = find_ticker(ticker_input)

        df, effective_interval = get_history(
            ticker,
            days,
            range_key=range_key,
            interval=interval,
        )

        return jsonify(
            {
                "symbol": ticker,
                "range": range_key,
                "interval": effective_interval,
                "prices": _json_safe_records(df),
            }
        )

    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "error": str(exc),
            }
        ), 400


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


@app.get("/api/predict")
def api_predict():
    ticker_input = request.args.get(
        "ticker",
        "",
    ).strip()

    if not ticker_input:
        return jsonify(
            {
                "error": "ticker is required",
                "code": "INVALID_REQUEST",
            }
        ), 400

    interval = request.args.get(
        "interval",
        "1d",
    )

    price_field = request.args.get(
        "price_field",
        "close",
    )

    model = request.args.get(
        "model",
        "xgboost",
    )

    # Prediction currently supports daily Close-return
    # classification only.
    if interval not in {"1d", "auto"}:
        return jsonify(
            {
                "error": (
                    "Prediction supports daily "
                    "Close-return events only"
                ),
                "code": "UNSUPPORTED_TASK",
            }
        ), 400

    if price_field != "close":
        return jsonify(
            {
                "error": (
                    "Prediction supports daily "
                    "Close-return events only"
                ),
                "code": "UNSUPPORTED_TASK",
            }
        ), 400

    try:
        ticker = find_ticker(ticker_input)

        from .research.inference import prediction_response
        try:
            result = prediction_response(ticker) if model == 'xgboost' else None
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise PredictionError('PREDICTION_UNAVAILABLE', str(exc)) from exc
        if result is None:
            result = predict(ticker, model=model)

        return jsonify(result)

    except PredictionError as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": exc.code,
            }
        ), exc.status

    except ValueError as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": "INVALID_REQUEST",
            }
        ), 400


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------


@app.get("/api/prediction-chart")
def api_prediction_chart():
    from .prediction.chart import prediction_chart
    ticker_input = request.args.get("ticker", "").strip()
    if not ticker_input:
        return jsonify({"error": "ticker is required", "code": "INVALID_REQUEST"}), 400
    try:
        ticker = find_ticker(ticker_input)
        from .research.inference import chart_response, active_prediction
        try:
            result = prediction_chart(ticker)
        except PredictionError:
            if active_prediction(ticker) is None and active_prediction(ticker, 'regression') is None:
                raise
            result = None
        return jsonify(chart_response(ticker, result))
    except PredictionError as exc:
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'PREDICTION_UNAVAILABLE'}), 503


@app.post("/api/train")
def api_train():
    """
    Request model training.

    Expected JSON:

    {
        "ticker": "AAPL"
    }
    """
    from .prediction.training_jobs import request_training

    data = request.get_json(
        silent=True,
    )

    if not isinstance(data, dict):
        return jsonify(
            {
                "error": "JSON request body is required",
                "code": "INVALID_REQUEST",
            }
        ), 400

    ticker_input = str(
        data.get(
            "ticker",
            "",
        )
    ).strip()

    if not ticker_input:
        return jsonify(
            {
                "error": "ticker is required",
                "code": "INVALID_REQUEST",
            }
        ), 400

    try:
        ticker = find_ticker(ticker_input)

        task = data.get('task', 'binary')
        result = request_training(ticker) if task == 'binary' else request_training(ticker, task)

        if result.get("status") == "training":
            status_code = 202
        else:
            status_code = 200

        return jsonify(result), status_code

    except PredictionError as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": exc.code,
            }
        ), exc.status

    except ValueError as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": "INVALID_REQUEST",
            }
        ), 400


# ---------------------------------------------------------------------------
# Available symbols
# ---------------------------------------------------------------------------


@app.get("/api/symbols")
def api_symbols():
    try:
        symbols = list_symbols()

        return jsonify(symbols)

    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "error": str(exc),
            }
        ), 500


# ---------------------------------------------------------------------------
# Search symbols
# ---------------------------------------------------------------------------


@app.get("/api/search")
def api_search():
    query = request.args.get(
        "q",
        "",
    ).strip()

    if not query:
        return jsonify([])

    try:
        results = search_symbols(query)

        return jsonify(results)

    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "error": str(exc),
            }
        ), 400


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


@app.after_request
def add_response_headers(response):
    # Prevent browser caching while developing.
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    # Basic security headers.
    response.headers["X-Content-Type-Options"] = "nosniff"

    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    response.headers["Referrer-Policy"] = (
        "strict-origin-when-cross-origin"
    )

    return response


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------


from .research.api import api as research_api
app.register_blueprint(research_api)


@app.get('/research')
def research_page():
    return send_from_directory(STATIC_DIR, 'research.html')


if __name__ == "__main__":
    port = int(
        os.getenv(
            "PORT",
            "49152",
        )
    )

    # Secure default:
    #
    # 127.0.0.1 means only this computer can connect.
    #
    # If LAN access is intentionally required:
    #
    # HOST=0.0.0.0 python -m stock_app.app
    #
    host = os.getenv(
        "HOST",
        "127.0.0.1",
    )

    app.run(
        host=host,
        port=port,
        debug=False,
    )
