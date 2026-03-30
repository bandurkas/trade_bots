"""
Получение активных Bitcoin Up/Down рынков с Polymarket.
Использует Gamma API (не требует авторизации).

Формат рынка: "Bitcoin Up or Down - March 30, 4:00PM-4:05PM ET"
Токены: clobTokenIds[0] = UP, clobTokenIds[1] = DOWN
Цена-таргет: BTC/USD цена по Chainlink в момент открытия раунда (eventStartTime)
"""
import logging
import re
import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from config import GAMMA_HOST, MIN_VOLUME_USD

logger = logging.getLogger(__name__)


@dataclass
class ActiveMarket:
    condition_id: str
    question: str
    end_date: str           # ISO строка закрытия раунда
    start_time: str         # ISO строка открытия раунда
    price_to_beat: float    # BTC цена в момент старта (от нас или из API)
    up_token_id: str
    down_token_id: str
    up_price: float         # вероятность UP (0..1)
    down_price: float       # вероятность DOWN (0..1)
    volume_usd: float
    seconds_to_close: int


class MarketScanner:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10)
        self._cache: Optional[ActiveMarket] = None
        self._cache_ts: float = 0
        self._cache_ttl: float = 5.0
        # Запоминаем цену BTC в момент старта раунда
        self._round_start_prices: dict[str, float] = {}

    async def get_active_btc_market(
        self, current_btc_price: Optional[float] = None
    ) -> Optional[ActiveMarket]:
        now_mono = time.monotonic()
        if self._cache and (now_mono - self._cache_ts) < self._cache_ttl:
            self._cache.seconds_to_close = self._calc_seconds_left(self._cache.end_date)
            return self._cache if self._cache.seconds_to_close > 0 else None

        market = await self._fetch_market(current_btc_price)
        if market:
            self._cache    = market
            self._cache_ts = now_mono
        return market

    async def _fetch_market(
        self, current_btc_price: Optional[float]
    ) -> Optional[ActiveMarket]:
        try:
            now = datetime.now(timezone.utc)
            soon = now + timedelta(minutes=10)
            resp = await self._client.get(
                f"{GAMMA_HOST}/markets",
                params={
                    "active":        "true",
                    "closed":        "false",
                    "limit":         200,
                    "end_date_min":  now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max":  soon.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
            resp.raise_for_status()
            markets = resp.json()
        except Exception as e:
            logger.error(f"Ошибка Gamma API: {e}")
            return None

        # Выбираем ближайший BTC Up or Down рынок
        btc_markets = [m for m in markets if self._is_btc_updown(m.get("question", ""))]
        if not btc_markets:
            logger.debug("BTC Up/Down рынков не найдено в ближайшие 10 минут")
            return None

        # Сортируем по времени закрытия (берём ближайший)
        btc_markets.sort(key=lambda m: m.get("endDate", ""))
        m = btc_markets[0]

        question    = m.get("question", "")
        end_date    = m.get("endDate", "")
        start_time  = m.get("eventStartTime") or m.get("startDate", "")
        seconds_left = self._calc_seconds_left(end_date)

        if seconds_left <= 0:
            return None

        volume = float(m.get("volumeNum") or m.get("volume") or 0)
        if volume < MIN_VOLUME_USD:
            logger.debug(f"Пропуск {question}: объём ${volume:.0f} < ${MIN_VOLUME_USD}")

        # Token IDs: [0] = UP, [1] = DOWN
        clob_ids = m.get("clobTokenIds", [])
        
        # FIX: Gamma API sometimes returns clobTokenIds as a string
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except json.JSONDecodeError:
                logger.error(f"Ошибка парсинга clobTokenIds для {question}: {clob_ids}")
                return None

        if not clob_ids or len(clob_ids) < 2:
            logger.warning(f"Нет clobTokenIds для {question}")
            return None
            
        up_token_id   = clob_ids[0]
        down_token_id = clob_ids[1]

        # Вероятности UP/DOWN из outcomePrices
        outcome_prices = m.get("outcomePrices", ["0.5", "0.5"])
        try:
            up_price   = float(outcome_prices[0])
            down_price = float(outcome_prices[1])
        except (IndexError, ValueError, TypeError):
            up_price, down_price = 0.5, 0.5

        # Цена-таргет: используем bestAsk/lastTradePrice как приближение
        price_to_beat = self._get_price_to_beat(
            condition_id      = m.get("conditionId", ""),
            start_time        = start_time,
            current_btc_price = current_btc_price,
        )

        market = ActiveMarket(
            condition_id  = m.get("conditionId", ""),
            question      = question,
            end_date      = end_date,
            start_time    = start_time,
            price_to_beat = price_to_beat or 0.0,
            up_token_id   = up_token_id,
            down_token_id = down_token_id,
            up_price      = up_price,
            down_price    = down_price,
            volume_usd    = volume,
            seconds_to_close = seconds_left,
        )
        logger.info(
            f"Рынок: {question} | "
            f"осталось={seconds_left}s | "
            f"UP={up_price:.0%} DOWN={down_price:.0%} | "
            f"объём=${volume:.0f} | "
            f"таргет={f'${price_to_beat:.2f}' if price_to_beat else 'неизвестен'}"
        )
        return market

    def _get_price_to_beat(
        self,
        condition_id: str,
        start_time: str,
        current_btc_price: Optional[float],
    ) -> Optional[float]:
        if condition_id in self._round_start_prices:
            return self._round_start_prices[condition_id]

        if current_btc_price and current_btc_price > 0:
            self._round_start_prices[condition_id] = current_btc_price
            logger.info(f"Таргет зафиксирован: ${current_btc_price:.2f} для {condition_id[:12]}...")
            return current_btc_price

        return None

    def _is_btc_updown(self, question: str) -> bool:
        q = question.lower()
        return ("bitcoin" in q or "btc" in q) and "up or down" in q

    def _calc_seconds_left(self, end_date_iso: str) -> int:
        if not end_date_iso:
            return 0
        try:
            end = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            return max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            return 0

    async def close(self):
        await self._client.aclose()
