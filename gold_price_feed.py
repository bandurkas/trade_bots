"""
Цена золота XAUUSD через TradingView WebSocket (OANDA:XAUUSD).
Использует 5-минутные свечи для расчёта импульса и подтверждения тренда.
🔥 Самый стабильный и быстрый фид.
"""
import asyncio
import json
import logging
import random
import re
import string
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

import websockets

logger = logging.getLogger(__name__)

# TradingView WebSocket settings
TRADINGVIEW_WSS = "wss://data.tradingview.com/socket.io/websocket"
TV_SYMBOL       = "OANDA:XAUUSD"

@dataclass
class GoldCandle:
    open:  float
    high:  float
    low:   float
    close: float
    ticks: int
    ts:    float

def generate_session():
    return "qs_" + "".join(random.choice(string.ascii_letters) for _ in range(12))

def prepend_header(st):
    return "~m~" + str(len(st)) + "~m~" + st

def construct_message(func, param_list):
    return json.dumps({"m": func, "p": param_list}, separators=(",", ":"))

def create_message(func, param_list):
    return prepend_header(construct_message(func, param_list))

class GoldPriceFeed:
    CANDLE_INTERVAL = 300  # 5-минутные свечи

    def __init__(self, maxlen: int = 30):
        self.candles: deque[GoldCandle] = deque(maxlen=maxlen)
        self.current_price: Optional[float] = None
        self._running = False
        self._callbacks: List[Callable] = []
        
        # Текущая формирующаяся свеча
        self._cur_open:  Optional[float] = None
        self._cur_high:  float = 0
        self._cur_low:   float = float("inf")
        self._cur_ticks: int   = 0
        self._cur_start: Optional[float] = None

    # ── Готовность ────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self.current_price is not None and len(self.candles) >= 3

    # ── Индикаторы ────────────────────────────────────────────────────────────

    def get_gap_from_target(self, target: float) -> Optional[float]:
        if not self.current_price or target <= 0:
            return None
        return (self.current_price - target) / target

    def get_momentum(self) -> Optional[float]:
        """Импульс за последние 3 свечи (~15 мин)."""
        if len(self.candles) < 3:
            return None
        old = self.candles[-3].close
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

    def get_daily_high_low(self) -> tuple[float, float]:
        if not self.candles:
            p = self.current_price or 0
            return p, p
        return max(c.high for c in self.candles), min(c.low for c in self.candles)

    # ── WebSocket Loop ───────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        logger.info(f"Gold price feed запущен (TradingView {TV_SYMBOL} WebSocket)")
        
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error(f"Gold TV connection error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _connect_and_run(self):
        session = generate_session()
        headers = {
            "Origin": "https://www.tradingview.com"
        }
        
        async with websockets.connect(
            TRADINGVIEW_WSS,
            additional_headers=headers,
            user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            open_timeout=10
        ) as ws:
            # Init TV session
            await ws.send(create_message("quote_create_session", [session]))
            await ws.send(create_message("quote_set_fields", [session, "lp", "ch", "chp", "v"]))
            await ws.send(create_message("quote_add_symbols", [session, TV_SYMBOL]))
            
            while self._running:
                msg = await ws.recv()
                
                # Heartbeat
                if "~h~" in msg:
                    await ws.send(msg)
                    continue
                
                # Parse messages
                parts = re.split(r"~m~\d+~m~", msg)
                for part in parts:
                    if not part: continue
                    try:
                        data = json.loads(part)
                        if data["m"] == "qsd" and data["p"][0] == session:
                            v = data["p"][1]["v"]
                            if "lp" in v:
                                await self._on_price(float(v["lp"]))
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def _on_price(self, price: float):
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
            self.candles.append(GoldCandle(
                open  = self._cur_open,
                high  = self._cur_high,
                low   = self._cur_low,
                close = price,
                ticks = self._cur_ticks,
                ts    = self._cur_start,
            ))
            logger.debug(f"Gold свеча 5м: O={self._cur_open:.2f} C={price:.2f}")
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
