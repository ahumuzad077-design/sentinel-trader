"""Dependency bootstrap for unit tests that exercise pure bot logic only."""

import importlib
import sys
import types


def _module(name, package=False):
    module = types.ModuleType(name)
    if package:
        module.__path__ = []
    sys.modules[name] = module
    return module


# Bot tests must never initialize an SDK client or contact a service.
_module("alpaca", package=True)
_module("alpaca.trading", package=True)
_module("alpaca.data", package=True)
trading_client = _module("alpaca.trading.client")
trading_client.TradingClient = type("TradingClient", (), {})
trading_requests = _module("alpaca.trading.requests")
trading_requests.MarketOrderRequest = type("MarketOrderRequest", (), {})
trading_enums = _module("alpaca.trading.enums")
trading_enums.OrderSide = types.SimpleNamespace(BUY="buy", SELL="sell")
trading_enums.TimeInForce = types.SimpleNamespace(DAY="day", GTC="gtc")
historical = _module("alpaca.data.historical")
historical.StockHistoricalDataClient = type("StockHistoricalDataClient", (), {})
historical.CryptoHistoricalDataClient = type("CryptoHistoricalDataClient", (), {})
data_requests = _module("alpaca.data.requests")
for class_name in ("StockBarsRequest", "StockLatestQuoteRequest",
                   "CryptoBarsRequest", "CryptoLatestQuoteRequest"):
    setattr(data_requests, class_name, type(class_name, (), {}))
timeframe = _module("alpaca.data.timeframe")
timeframe.TimeFrame = types.SimpleNamespace(Day="day")
dotenv = _module("dotenv")
dotenv.load_dotenv = lambda **kwargs: None

bot = importlib.import_module("bot")
