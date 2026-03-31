"""
Анализ order flow с Polymarket CLOB API.

Смотрим на реальные сделки за последние 60 секунд:
  - Сколько $ пришло в UP-токен vs DOWN-токен
  - Были ли крупные ставки (>$200)
  - Вычисляем дисбаланс и направление "умных денег"
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

CLOB_HOST = "https://clob.polymarket.com"
FLOW_WINDOW_SECS = 60    # смотрим последние 60 секунд
LARGE_TRADE_USD  = 200   # ставка считается "крупной"
NET_THRESHOLD    = 50    # минимальный дисбаланс чтобы определить направление

logger = logging.getLogger(__name__)


@dataclass
class OrderFlow:
    direction:        Optional[str]  # "UP", "DOWN", или None (неопределённо)
    net_usd:          float          # > 0 → деньги идут в UP
    up_usd:           float          # всего $ в UP за окно
    down_usd:         float          # всего $ в DOWN за окно
    large_trade:      bool           # крупная ставка в окне
    confidence_boost: float          # сколько добавить к уверенности сигнала


class CLOBAnalyzer:
    def __init__(self):
        self._client   = httpx.AsyncClient(timeout=5)
        self._cache:   dict[str, tuple[float, OrderFlow]] = {}
        self._cache_ttl = 3.0  # кешируем 3 секунды

    async def analyze(
        self,
        up_token_id:   str,
        down_token_id: str,
    ) -> Optional[OrderFlow]:
        """Возвращает OrderFlow или None если API недоступен."""
        key = f"{up_token_id[:8]}_{down_token_id[:8]}"
        now = time.monotonic()

        if key in self._cache:
            ts, flow = self._cache[key]
            if now - ts < self._cache_ttl:
                return flow

        flow = await self._compute_flow(up_token_id, down_token_id)
        if flow:
            self._cache[key] = (now, flow)
        return flow

    async def _compute_flow(
        self,
        up_token_id:   str,
        down_token_id: str,
    ) -> Optional[OrderFlow]:
        try:
            up_trades, down_trades = await asyncio.gather(
                self._fetch_trades(up_token_id),
                self._fetch_trades(down_token_id),
            )
        except Exception as e:
            logger.debug(f"CLOB fetch failed: {e}")
            return None

        now_ts = time.time()

        def bought_usd(trades: list[dict]) -> tuple[float, bool]:
            """Считаем сколько $ потрачено на покупки за последнее окно."""
            total = 0.0
            large = False
            for t in trades:
                if now_ts - t["ts"] > FLOW_WINDOW_SECS:
                    continue
                if t["side"] != "BUY":
                    continue
                usd = t["size"] * t["price"]
                total += usd
                if usd >= LARGE_TRADE_USD:
                    large = True
            return total, large

        up_usd,   up_large   = bought_usd(up_trades)
        down_usd, down_large = bought_usd(down_trades)
        net_usd = up_usd - down_usd
        large   = up_large or down_large

        # Направление по потоку денег
        if net_usd > NET_THRESHOLD:
            direction = "UP"
        elif net_usd < -NET_THRESHOLD:
            direction = "DOWN"
        else:
            direction = None

        # Бонус к уверенности: чем больше перекос и крупнее ставки — тем выше
        boost = 0.0
        if abs(net_usd) > 300:
            boost = 0.06
        elif abs(net_usd) > 100:
            boost = 0.03
        if large:
            boost += 0.03
        boost = min(boost, 0.08)

        logger.info(
            f"CLOB flow | UP=${up_usd:.0f} DOWN=${down_usd:.0f} "
            f"net=${net_usd:+.0f} | large={large} | boost={boost:.0%} | dir={direction}"
        )
        return OrderFlow(
            direction        = direction,
            net_usd          = net_usd,
            up_usd           = up_usd,
            down_usd         = down_usd,
            large_trade      = large,
            confidence_boost = boost,
        )

    async def _fetch_trades(self, token_id: str) -> list[dict]:
        from config import POLY_API_KEY
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        if POLY_API_KEY:
            headers["x-poly-api-key"] = POLY_API_KEY
            headers["x-api-key"] = POLY_API_KEY

        try:
            resp = await self._client.get(
                f"{CLOB_HOST}/trades",
                params={"token_id": token_id, "limit": 50},
                headers=headers
            )
            resp.raise_for_status()
            raw = resp.json().get("data", [])

            result = []
            for t in raw:
                try:
                    result.append({
                        "ts":    self._parse_ts(t.get("match_time", 0)),
                        "size":  float(t.get("size",  0)),
                        "price": float(t.get("price", 0)),
                        "side":  t.get("side", ""),
                    })
                except (KeyError, ValueError, TypeError):
                    continue
            return result
        except Exception as e:
            logger.debug(f"CLOB /trades error ({token_id[:12]}): {e}")
            return []

    @staticmethod
    def _parse_ts(raw) -> float:
        """Парсим match_time — может быть unix float или ISO строка."""
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0

    async def close(self):
        await self._client.aclose()
