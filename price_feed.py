"""
Цена BTC из трёх источников:
  1. Binance  — быстрый REST (~1 сек задержка), первичный сигнал
  2. Coinbase — подтверждение, второй по скорости
  3. Chainlink — финальный эталон (тот же оракул, которым Polymarket разрешает рынки)

Итоговая цена = медиана доступных источников.
Если Chainlink значительно расходится с биржами — логируем предупреждение.

Chainlink BTC/USD: 0xc907E116054Ad103354f2D350FD2514433D57F6f (Polygon)
RPC: https://1rpc.io/matic
"""
import asyncio
import json
import logging
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Источники ─────────────────────────────────────────────────────────────────
BINANCE_URL        = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
COINBASE_URL       = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
CHAINLINK_CONTRACT = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
POLYGON_RPC        = "https://1rpc.io/matic"
LATEST_ROUND_DATA  = "0xfeaf968c"

POLL_INTERVAL      = 2.0   # секунд между опросами всех трёх источников
DIVERGE_THRESHOLD  = 0.003  # 0.3% расхождение между источниками → предупреждение


@dataclass
class Candle:
    open:   float
    high:   float
    low:    float
    close:  float
    ticks:  int    # количество обновлений за минуту
    ts:     float  # unix timestamp открытия


class BTCPriceFeed:
    CANDLE_INTERVAL = 60  # секунд в одной свече

    def __init__(self, maxlen: int = 15):
        self.candles:       deque[Candle]    = deque(maxlen=maxlen)
        self.current_price: Optional[float]  = None
        self._callbacks:    List[Callable]   = []
        self._running = False

        # Цены по источникам (для мониторинга расхождений)
        self.prices: Dict[str, float] = {}

        # Текущая формирующаяся свеча
        self._cur_open:   Optional[float] = None
        self._cur_high:   float = 0
        self._cur_low:    float = float("inf")
        self._cur_ticks:  int   = 0
        self._cur_start:  Optional[float] = None

    def on_price(self, callback: Callable):
        self._callbacks.append(callback)

    # ── Polling ───────────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        loop = asyncio.get_event_loop()
        logger.info("BTC price feed запущен (Binance + Coinbase + Chainlink)")
        while self._running:
            try:
                price = await loop.run_in_executor(None, self._fetch_all)
                if price and price > 0:
                    self.current_price = price
                    self._update_candle(price)
                    for cb in self._callbacks:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(price)
                        else:
                            cb(price)
            except Exception as e:
                logger.warning(f"Price feed ошибка: {e}. Повтор через 3с...")
            await asyncio.sleep(POLL_INTERVAL)

    def _fetch_all(self) -> float:
        """Опрашивает все три источника, возвращает медиану доступных."""
        results: Dict[str, float] = {}

        # 1. Binance (самый быстрый)
        try:
            req = urllib.request.Request(
                BINANCE_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read())
                results["binance"] = float(data["price"])
        except Exception as e:
            logger.debug(f"Binance недоступен: {e}")

        # 2. Coinbase (подтверждение)
        try:
            req = urllib.request.Request(
                COINBASE_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read())
                results["coinbase"] = float(data["data"]["amount"])
        except Exception as e:
            logger.debug(f"Coinbase недоступен: {e}")

        # 3. Chainlink (эталон Polymarket)
        try:
            payload = json.dumps({
                "jsonrpc": "2.0",
                "method":  "eth_call",
                "params":  [{"to": CHAINLINK_CONTRACT, "data": LATEST_ROUND_DATA}, "latest"],
                "id":      1,
            }).encode()
            req = urllib.request.Request(
                POLYGON_RPC,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent":   "Mozilla/5.0",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                data   = json.loads(r.read())
                result = data.get("result", "")
                if result and len(result) >= 130:
                    answer_hex = result[2 + 64: 2 + 128]
                    results["chainlink"] = int(answer_hex, 16) / 1e8
        except Exception as e:
            logger.debug(f"Chainlink недоступен: {e}")

        if not results:
            return 0.0

        # Сохраняем для мониторинга (все источники)
        self.prices = results

        # ── Торговая цена: только биржи (Binance + Coinbase) ──────────────────
        # Chainlink обновляется медленно и на публичных RPC может быть устаревшим.
        # Используем его только для информации / сравнения.
        exchange_prices = {k: v for k, v in results.items() if k in ("binance", "coinbase")}
        trade_prices = exchange_prices if exchange_prices else results

        values = sorted(trade_prices.values())
        n = len(values)
        if n == 1:
            price = values[0]
        elif n == 2:
            # Если биржи расходятся >2% — предпочитаем Binance как более быстрый
            spread = (values[-1] - values[0]) / values[0]
            if spread > 0.02 and "binance" in trade_prices:
                price = trade_prices["binance"]
                logger.warning(f"Binance/Coinbase расхождение {spread:.2%}, используем Binance=${price:,.2f}")
            else:
                price = (values[0] + values[1]) / 2
        else:
            price = values[n // 2]

        # Логируем все источники
        all_str = " | ".join(f"{k}=${v:,.2f}" for k, v in sorted(results.items()))
        logger.debug(f"BTC цены: {all_str} → торговая=${price:,.2f}")

        # Предупреждение если Chainlink сильно расходится с биржами
        if "chainlink" in results and exchange_prices:
            cl = results["chainlink"]
            ex_avg = sum(exchange_prices.values()) / len(exchange_prices)
            cl_spread = abs(cl - ex_avg) / ex_avg
            if cl_spread > DIVERGE_THRESHOLD:
                logger.warning(
                    f"Chainlink ${cl:,.2f} отличается от бирж ${ex_avg:,.2f} "
                    f"на {cl_spread:.1%} — игнорируем для цены"
                )

        return price

    def stop(self):
        self._running = False

    # ── Chainlink-цена для точного сравнения с таргетом ──────────────────────

    @property
    def chainlink_price(self) -> Optional[float]:
        """Цена по Chainlink — тот же оракул, что использует Polymarket."""
        return self.prices.get("chainlink")

    # ── Свечи ─────────────────────────────────────────────────────────────────

    def _update_candle(self, price: float):
        now    = time.time()
        bucket = now - (now % self.CANDLE_INTERVAL)

        if self._cur_start is None:
            self._cur_start = bucket

        if bucket > self._cur_start and self._cur_open is not None:
            self.candles.append(Candle(
                open  = self._cur_open,
                high  = self._cur_high,
                low   = self._cur_low,
                close = price,
                ticks = self._cur_ticks,
                ts    = self._cur_start,
            ))
            logger.debug(
                f"BTC свеча: O={self._cur_open:.2f} H={self._cur_high:.2f} "
                f"L={self._cur_low:.2f} C={price:.2f}"
            )
            self._cur_start = bucket
            self._cur_open  = price
            self._cur_high  = price
            self._cur_low   = price
            self._cur_ticks = 1
        else:
            if self._cur_open is None:
                self._cur_open = price
            self._cur_high  = max(self._cur_high, price)
            self._cur_low   = min(self._cur_low,  price)
            self._cur_ticks += 1

    # ── Готовность ────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self.current_price is not None and len(self.candles) >= 3

    # ── Индикаторы ────────────────────────────────────────────────────────────

    def get_momentum(self) -> Optional[float]:
        if len(self.candles) < 3:
            return None
        old = self.candles[-3].close
        return (self.current_price - old) / old

    def get_momentum_5m(self) -> Optional[float]:
        if len(self.candles) < 6:
            return None
        old = self.candles[-6].close
        return (self.current_price - old) / old

    def last_candles_direction(self, n: int = 2) -> Optional[str]:
        if len(self.candles) < n:
            return None
        last = list(self.candles)[-n:]
        if all(c.close > c.open for c in last):
            return "up"
        if all(c.close < c.open for c in last):
            return "down"
        return None

    def get_rsi(self, period: int = 7) -> Optional[float]:
        if len(self.candles) < period + 1:
            return None
        closes   = [c.close for c in list(self.candles)[-(period + 1):]]
        gains    = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
        losses   = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    def get_volume_spike(self, multiplier: float = 1.5) -> bool:
        if len(self.candles) < 5:
            return False
        last5     = list(self.candles)[-5:]
        avg_ticks = sum(c.ticks for c in last5) / len(last5)
        if avg_ticks == 0:
            return False
        return self._cur_ticks > avg_ticks * multiplier
