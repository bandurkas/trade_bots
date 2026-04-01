"""
Движок сигналов: стратегия "Импульс + Последние 60 секунд".

Условия для входа:
  1. Временное окно: 20–90 секунд до закрытия
  2. Краткосрочный импульс >= MOMENTUM_THRESHOLD (~2 мин)
  3. Последние 2 свечи подтверждают направление
  4. Цена уже выше/ниже таргета на MIN_GAP_FROM_TARGET
  5. 5-минутный импульс согласован с краткосрочным (если доступен)
  6. RSI не в зоне перекупленности/перепроданности (если доступен)
  7. Рыночная вероятность не против нас (>= 20%)
  8. Цена входа не выше 80¢ (риск/доходность)
  9. Polymarket CLOB order flow не противоречит сигналу (если доступен)
"""
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from config import (
    ENTRY_WINDOW_MAX,
    ENTRY_WINDOW_MIN,
    MIN_GAP_FROM_TARGET,
    MOMENTUM_THRESHOLD,
)
from market_scanner import ActiveMarket
from price_feed import BTCPriceFeed

if TYPE_CHECKING:
    from clob_analyzer import OrderFlow

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    direction:          str    # 'UP' или 'DOWN'
    confidence:         float  # вероятность 0.50 – 0.80
    momentum:           float  # % изменение цены (2 мин)
    momentum_5m:        float  # % изменение цены (5 мин), 0 если нет данных
    gap:                float  # разрыв между ценой и таргетом
    seconds_left:       int
    btc_price:          float
    market_probability: float  # вероятность по Polymarket
    rsi:                float  # RSI, 0 если нет данных
    volume_spike:       bool   # True если объём аномально высокий
    clob_flow_usd:      float  # CLOB дисбаланс в $, 0 если нет данных
    reason:             str    # описание почему сигнал


SKIP_REASONS = []  # для дебага


def check_signal(
    feed:       BTCPriceFeed,
    market:     ActiveMarket,
    clob_flow:  Optional["OrderFlow"] = None,
) -> Optional[Signal]:
    """
    Основная функция проверки сигнала.
    Возвращает Signal или None.
    """
    SKIP_REASONS.clear()

    # ── Условие 1: временное окно ─────────────────────────────────────────────
    s = market.seconds_to_close
    if not (ENTRY_WINDOW_MIN <= s <= ENTRY_WINDOW_MAX):
        SKIP_REASONS.append(f"время {s}s вне окна [{ENTRY_WINDOW_MIN}-{ENTRY_WINDOW_MAX}]")
        return None

    # ── Условие 2: краткосрочный импульс ──────────────────────────────────────
    if not feed.is_ready:
        SKIP_REASONS.append("недостаточно свечей")
        return None

    momentum = feed.get_momentum()
    if momentum is None or abs(momentum) < MOMENTUM_THRESHOLD:
        SKIP_REASONS.append(
            f"импульс {momentum:.4%} < порога {MOMENTUM_THRESHOLD:.4%}"
            if momentum is not None else "импульс недоступен"
        )
        return None

    # ── Условие 3: подтверждение свечами ──────────────────────────────────────
    candle_dir = feed.last_candles_direction(n=2)
    if candle_dir is None:
        SKIP_REASONS.append("свечи смешанного направления")
        return None

    momentum_up = momentum > 0
    direction   = "UP" if momentum_up else "DOWN"

    if momentum_up and candle_dir != "up":
        SKIP_REASONS.append("импульс UP, но свечи не зелёные")
        return None
    if not momentum_up and candle_dir != "down":
        SKIP_REASONS.append("импульс DOWN, но свечи не красные")
        return None

    # ── Условие 4: разрыв от таргета ─────────────────────────────────────────
    btc_price = feed.current_price
    gap       = btc_price - market.price_to_beat  # > 0 = UP лидирует

    if direction == "UP" and gap < MIN_GAP_FROM_TARGET:
        SKIP_REASONS.append(f"UP: разрыв ${gap:.2f} < ${MIN_GAP_FROM_TARGET} (риск разворота)")
        return None
    if direction == "DOWN" and gap > -MIN_GAP_FROM_TARGET:
        SKIP_REASONS.append(f"DOWN: разрыв ${gap:.2f} недостаточен")
        return None

    # ── Условие 5: мульти-таймфрейм (5 мин) ──────────────────────────────────
    momentum_5m = feed.get_momentum_5m()
    if momentum_5m is not None:
        # 5-минутный тренд должен не противоречить краткосрочному
        if direction == "UP" and momentum_5m < 0:
            SKIP_REASONS.append(f"5m импульс противоречит: {momentum_5m:.3%}")
            return None
        if direction == "DOWN" and momentum_5m > 0:
            SKIP_REASONS.append(f"5m импульс противоречит: {momentum_5m:.3%}")
            return None

    # ── Условие 6: RSI фильтр ─────────────────────────────────────────────────
    rsi = feed.get_rsi()
    if rsi is not None:
        if direction == "UP" and rsi > 75:
            SKIP_REASONS.append(f"RSI перекуплен: {rsi:.1f} — импульс может быть на излёте")
            return None
        if direction == "DOWN" and rsi < 25:
            SKIP_REASONS.append(f"RSI перепродан: {rsi:.1f} — возможен отскок")
            return None

    # ── Условие 7: рыночная вероятность не против нас ────────────────────────
    market_prob_check = market.up_price if direction == "UP" else market.down_price
    if market_prob_check < 0.20:
        SKIP_REASONS.append(
            f"Рынок оценивает {direction} в {market_prob_check:.0%} — слишком против нас"
        )
        return None

    # ── Условие 8: максимальная цена входа (не выше 80¢) ────────────────────
    entry_price_check = market.up_price if direction == "UP" else market.down_price
    if entry_price_check > 0.80:
        SKIP_REASONS.append(
            f"Цена входа {entry_price_check:.0%} > 80¢ — риск/доходность неприемлемы"
        )
        return None

    # ── Условие 9: CLOB order flow ────────────────────────────────────────────
    if clob_flow and clob_flow.direction and clob_flow.direction != direction:
        SKIP_REASONS.append(
            f"CLOB flow противоречит: деньги идут в {clob_flow.direction} "
            f"(${clob_flow.net_usd:+.0f})"
        )
        return None

    # ── Дополнительные данные ─────────────────────────────────────────────────
    volume_spike   = feed.get_volume_spike()
    clob_boost     = clob_flow.confidence_boost if clob_flow else 0.0
    clob_flow_usd  = clob_flow.net_usd if clob_flow else 0.0
    market_prob    = market.up_price if direction == "UP" else market.down_price

    # ── Расчёт уверенности ────────────────────────────────────────────────────
    confidence = _calc_confidence(
        momentum     = abs(momentum),
        gap          = abs(gap),
        seconds_left = s,
        market_prob  = market_prob,
        momentum_5m  = momentum_5m,
        rsi          = rsi,
        volume_spike = volume_spike,
        clob_boost   = clob_boost,
    )

    if market.volume_usd < 1000:
        confidence *= 0.85  # снижаем при низкой ликвидности рынка

    # ── Строим причину ────────────────────────────────────────────────────────
    parts = [
        f"{'↑' if direction == 'UP' else '↓'} Импульс 2м {momentum:.3%}",
        f"Разрыв ${abs(gap):.2f}",
        f"{'🟢🟢' if candle_dir == 'up' else '🔴🔴'}",
    ]
    if momentum_5m is not None:
        parts.append(f"5м {momentum_5m:+.3%}")
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    if volume_spike:
        parts.append("⚡объём")
    if clob_flow and abs(clob_flow.net_usd) > 50:
        parts.append(f"CLOB ${clob_flow.net_usd:+.0f}")

    reason = " | ".join(parts)

    return Signal(
        direction          = direction,
        confidence         = round(confidence, 3),
        momentum           = momentum,
        momentum_5m        = momentum_5m or 0.0,
        gap                = gap,
        seconds_left       = s,
        btc_price          = btc_price,
        market_probability = market_prob,
        rsi                = rsi or 0.0,
        volume_spike       = volume_spike,
        clob_flow_usd      = clob_flow_usd,
        reason             = reason,
    )


def _calc_confidence(
    momentum:     float,
    gap:          float,
    seconds_left: int,
    market_prob:  float,
    momentum_5m:  Optional[float] = None,
    rsi:          Optional[float] = None,
    volume_spike: bool = False,
    clob_boost:   float = 0.0,
) -> float:
    """
    Расчёт уверенности сигнала.

    Базовая:        0.52
    + импульс:      до +0.10
    + разрыв:       до +0.05
    + время:        до +0.05
    + рынок:        до +0.02
    + 5м таймфрейм: до +0.03
    + RSI зона:     до +0.02
    + объём spike:  +0.03
    + CLOB flow:    до +0.08
    ─────────────────────────
    Максимум:       0.80
    """
    base = 0.52

    # Импульс: каждый 0.1% → +0.01, но не более +0.10
    momentum_bonus = min(momentum / 0.001 * 0.01, 0.10)

    # Разрыв: каждые $5 → +0.01, но не более +0.05
    gap_bonus = min(gap / 5.0 * 0.01, 0.05)

    # Время: чем меньше осталось — тем выше (более определённо)
    if seconds_left <= 30:
        time_bonus = 0.05
    elif seconds_left <= 60:
        time_bonus = 0.03
    else:
        time_bonus = 0.01

    # Согласованность с рынком Polymarket
    market_bonus = 0.02 if market_prob > 0.65 else 0.0

    # 5м таймфрейм согласован и сильный
    mtf_bonus = 0.0
    if momentum_5m is not None and abs(momentum_5m) > 0.001:
        mtf_bonus = 0.03

    # RSI в нейтральной зоне (40–65) = сигнал надёжнее
    rsi_bonus = 0.0
    if rsi is not None and 40.0 <= rsi <= 65.0:
        rsi_bonus = 0.02

    # Объём выше нормы = движение подтверждено
    volume_bonus = 0.03 if volume_spike else 0.0

    total = (
        base
        + momentum_bonus
        + gap_bonus
        + time_bonus
        + market_bonus
        + mtf_bonus
        + rsi_bonus
        + volume_bonus
        + clob_boost
    )
    return min(total, 0.80)
