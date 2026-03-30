"""
Сканер дневных Gold (XAUUSD) Up/Down рынков на Polymarket.
Ищет рынки на ближайшие 24 часа, выбирает с наибольшим объёмом.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from config import GAMMA_HOST

logger = logging.getLogger(__name__)


@dataclass
class GoldMarket:
    condition_id:   str
    question:       str
    end_date:       str    # ISO строка закрытия рынка
    price_to_beat:  float  # XAU цена на старте (фиксируем при первом появлении)
    up_token_id:    str
    down_token_id:  str
    up_price:       float  # вероятность UP (0..1)
    down_price:     float
    volume_usd:     float
    hours_to_close: float  # сколько часов до закрытия


class GoldScanner:
    def __init__(self):
        self._client   = httpx.AsyncClient(timeout=10)
        self._cache:    Optional[GoldMarket] = None
        self._cache_ts: float = 0
        self._cache_ttl: float = 30.0  # кешируем 30 сек (дневной рынок не меняется часто)
        self._start_prices: dict[str, float] = {}  # condition_id → gold цена при старте

    async def get_gold_market(
        self, current_gold_price: Optional[float] = None
    ) -> Optional[GoldMarket]:
        now_mono = time.monotonic()

        if self._cache and (now_mono - self._cache_ts) < self._cache_ttl:
            hours = self._calc_hours_left(self._cache.end_date)
            self._cache.hours_to_close = hours
            return self._cache if hours > 0 else None

        market = await self._fetch_market(current_gold_price)
        if market:
            self._cache    = market
            self._cache_ts = now_mono
        return market

    async def _fetch_market(
        self, current_gold_price: Optional[float]
    ) -> Optional[GoldMarket]:
        try:
            now     = datetime.now(timezone.utc)
            end_max = now + timedelta(hours=26)  # ищем рынки на ближайшие ~сутки
            resp = await self._client.get(
                f"{GAMMA_HOST}/markets",
                params={
                    "active":        "true",
                    "closed":        "false",
                    "limit":         100,
                    "end_date_min":  now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max":  end_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
            resp.raise_for_status()
            markets = resp.json()
        except Exception as e:
            logger.error(f"Gold Gamma API ошибка: {e}")
            return None

        gold_markets = [
            m for m in markets
            if self._is_gold_updown(m.get("question", ""))
        ]
        if not gold_markets:
            logger.debug("Gold Up/Down рынков не найдено")
            return None

        # Берём рынок с наибольшим объёмом (основной дневной)
        gold_markets.sort(
            key=lambda m: float(m.get("volumeNum") or 0),
            reverse=True,
        )
        m = gold_markets[0]

        question   = m.get("question", "")
        end_date   = m.get("endDate", "")
        hours_left = self._calc_hours_left(end_date)

        if hours_left <= 0:
            return None

        volume = float(m.get("volumeNum") or m.get("volume") or 0)

        clob_ids = m.get("clobTokenIds", [])
        if not clob_ids or len(clob_ids) < 2:
            logger.warning("Нет clobTokenIds для Gold рынка")
            return None

        outcome_prices = m.get("outcomePrices", ["0.5", "0.5"])
        try:
            up_price   = float(outcome_prices[0])
            down_price = float(outcome_prices[1])
        except (IndexError, ValueError, TypeError):
            up_price, down_price = 0.5, 0.5

        condition_id  = m.get("conditionId", "")
        price_to_beat = self._get_price_to_beat(condition_id, current_gold_price)

        market = GoldMarket(
            condition_id  = condition_id,
            question      = question,
            end_date      = end_date,
            price_to_beat = price_to_beat or 0.0,
            up_token_id   = clob_ids[0],
            down_token_id = clob_ids[1],
            up_price      = up_price,
            down_price    = down_price,
            volume_usd    = volume,
            hours_to_close = hours_left,
        )
        logger.info(
            f"Gold рынок: {question} | "
            f"осталось={hours_left:.1f}ч | "
            f"UP={up_price:.0%} DOWN={down_price:.0%} | "
            f"объём=${volume:.0f} | "
            f"таргет={f'${price_to_beat:.2f}' if price_to_beat else 'неизвестен'}"
        )
        return market

    def _get_price_to_beat(
        self,
        condition_id: str,
        current_price: Optional[float],
    ) -> Optional[float]:
        if condition_id in self._start_prices:
            return self._start_prices[condition_id]
        if current_price and current_price > 0:
            self._start_prices[condition_id] = current_price
            logger.info(f"Gold таргет зафиксирован: ${current_price:.2f}")
            return current_price
        return None

    def _is_gold_updown(self, question: str) -> bool:
        q = question.lower()
        return ("gold" in q or "xau" in q) and "up or down" in q

    def _calc_hours_left(self, end_date_iso: str) -> float:
        if not end_date_iso:
            return 0.0
        try:
            end  = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            secs = (end - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, secs / 3600)
        except Exception:
            return 0.0

    async def close(self):
        await self._client.aclose()
