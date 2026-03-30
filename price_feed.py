"""
Получение цены BTC через Binance REST API (polling каждую секунду).
Хранит последние 15 минутных свечей для расчёта импульса, RSI и volume spike.
"""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

import httpx

logger = logging.getLogger(__name__)

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
POLL_INTERVAL     = 1.0   # секунд между запросами


@dataclass
class Candle:
    open:   float
    high:   float
    low:    float
    close:  float
    ticks:  int    # количество тиков за минуту (proxy для объёма)
    ts:     float  # unix timestamp открытия


class BTCPriceFeed:
    CANDLE_INTERVAL = 60  # секунд в одной свече

    def __init__(self, maxlen: int = 15):
        self.candles: deque[Candle] = deque(maxlen=maxlen)
        self.current_price: Optional[float] = None
        self._callbacks: List[Callable] = []
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None

        # Текущая формирующаяся свеча
        self._cur_open:   Optional[float] = None
        self._cur_high:   float = 0
        self._cur_low:    float = float("inf")
        self._cur_ticks:  int   = 0
        self._cur_start:  Optional[float] = None

    def on_price(self, callback: Callable):
        self._callbacks.append(callback)

    async def start(self):
        self._running = True
        self._client  = httpx.AsyncClient(timeout=5)
        logger.info("BTC price feed запущен (Binance REST polling)")
        try:
            while self._running:
                try:
                    await self._poll()
                except Exception as e:
                    logger.warning(f"BTC poll ошибка: {e}. Повтор через 2с...")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self._client.aclose()

    async def _poll(self):
        resp  = await self._client.get(BINANCE_PRICE_URL)
        resp.raise_for_status()
        price = float(resp.json()["price"])
        if price <= 0:
            return

        self.current_price = price
        self._update_candle(price)

        for cb in self._callbacks:
            if asyncio.iscoroutinefunction(cb):
                await cb(price)
            else:
                cb(price)

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
            self._cur_start  = bucket
            self._cur_open   = price
            self._cur_high   = price
            self._cur_low    = price
            self._cur_ticks  = 1
        else:
            if self._cur_open is None:
                self._cur_open = price
            self._cur_high  = max(self._cur_high, price)
            self._cur_low   = min(self._cur_low,  price)
            self._cur_ticks += 1

    def stop(self):
        self._running = False

    # ── Готовность ────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self.current_price is not None and len(self.candles) >= 3

    # ── Импульс ───────────────────────────────────────────────────────────────

    def get_momentum(self) -> Optional[float]:
        """Краткосрочный импульс: изменение за ~2 минуты (3 свечи)."""
        if len(self.candles) < 3:
            return None
        old = self.candles[-3].close
        return (self.current_price - old) / old

    def get_momentum_5m(self) -> Optional[float]:
        """Долгосрочный импульс: изменение за ~5 минут (6 свечей)."""
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

    # ── RSI ───────────────────────────────────────────────────────────────────

    def get_rsi(self, period: int = 7) -> Optional[float]:
        if len(self.candles) < period + 1:
            return None
        closes  = [c.close for c in list(self.candles)[-(period + 1):]]
        gains   = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
        losses  = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    # ── Volume spike ──────────────────────────────────────────────────────────

    def get_volume_spike(self, multiplier: float = 1.5) -> bool:
        if len(self.candles) < 5:
            return False
        last5     = list(self.candles)[-5:]
        avg_ticks = sum(c.ticks for c in last5) / len(last5)
        if avg_ticks == 0:
            return False
        return self._cur_ticks > avg_ticks * multiplier
