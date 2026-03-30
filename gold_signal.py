"""
Движок сигналов для Gold (XAUUSD) Up/Down дневных рынков.

Стратегия: входим когда тренд уже установился,
но Polymarket ещё не полностью отразил направление (есть edge).

Edge = наша оценка вероятности > цена рынка
Пример: gold +0.3% от таргета, мы оцениваем UP ~80%, рынок даёт 65¢ → edge 15%
"""
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from gold_price_feed import GoldPriceFeed
from gold_scanner import GoldMarket

if TYPE_CHECKING:
    from clob_analyzer import OrderFlow

logger = logging.getLogger(__name__)

# ── Параметры стратегии ────────────────────────────────────────────────────────
GOLD_GAP_MIN_PCT     = 0.0010  # минимальный разрыв от таргета: 0.10% (~$4.5 при $4500)
GOLD_GAP_STRONG_PCT  = 0.0030  # сильный тренд: 0.30% (~$13.5)
GOLD_MARKET_MAX_PROB = 0.85    # не входить если рынок уже > 85¢ (edge исчез)
GOLD_MARKET_MIN_PROB = 0.52    # рынок должен начать соглашаться
GOLD_MIN_HOURS       = 1.0     # не менее 1 часа до закрытия
GOLD_MAX_HOURS       = 7.0     # не более 7 часов (тренд должен установиться)


@dataclass
class GoldSignal:
    direction:          str            # 'UP' или 'DOWN'
    confidence:         float          # 0.55 – 0.82
    gap_pct:            float          # % разрыв от таргета
    gap_abs:            float          # $ разрыв
    hours_left:         float          # часов до закрытия
    gold_price:         float          # текущая цена XAU/USD
    target_price:       float          # дневной таргет
    market_probability: float          # вероятность по Polymarket
    edge:               float          # наша оценка − цена рынка
    clob_flow_usd:      float          # CLOB дисбаланс в $
    candle_dir:         Optional[str]  # 'up', 'down', None
    reason:             str


GOLD_SKIP_REASONS: list[str] = []


def check_gold_signal(
    feed:      GoldPriceFeed,
    market:    GoldMarket,
    clob_flow: Optional["OrderFlow"] = None,
) -> Optional[GoldSignal]:
    GOLD_SKIP_REASONS.clear()

    # ── 1. Временное окно ─────────────────────────────────────────────────────
    h = market.hours_to_close
    if not (GOLD_MIN_HOURS <= h <= GOLD_MAX_HOURS):
        GOLD_SKIP_REASONS.append(
            f"время {h:.1f}ч вне окна [{GOLD_MIN_HOURS}–{GOLD_MAX_HOURS}]"
        )
        return None

    # ── 2. Данные доступны ────────────────────────────────────────────────────
    if not feed.current_price or not market.price_to_beat:
        GOLD_SKIP_REASONS.append("нет данных о цене gold или таргете")
        return None

    # ── 3. Разрыв от таргета ──────────────────────────────────────────────────
    gap_abs = feed.current_price - market.price_to_beat
    gap_pct = gap_abs / market.price_to_beat

    if abs(gap_pct) < GOLD_GAP_MIN_PCT:
        GOLD_SKIP_REASONS.append(
            f"разрыв {gap_pct:.3%} < минимума {GOLD_GAP_MIN_PCT:.3%}"
        )
        return None

    direction = "UP" if gap_pct > 0 else "DOWN"

    # ── 4. Polymarket ещё не полностью отразил (есть edge) ───────────────────
    market_prob = market.up_price if direction == "UP" else market.down_price

    if market_prob > GOLD_MARKET_MAX_PROB:
        GOLD_SKIP_REASONS.append(
            f"рынок уже {market_prob:.0%} — edge исчез (>{GOLD_MARKET_MAX_PROB:.0%})"
        )
        return None

    if market_prob < GOLD_MARKET_MIN_PROB:
        GOLD_SKIP_REASONS.append(
            f"рынок неопределён {market_prob:.0%} — тренд не подтверждён"
        )
        return None

    # ── 5. Подтверждение свечами ──────────────────────────────────────────────
    candle_dir = feed.last_candles_direction(n=2) if feed.is_ready else None
    if candle_dir and candle_dir != direction.lower():
        GOLD_SKIP_REASONS.append(
            f"свечи противоречат: {candle_dir} vs {direction.lower()}"
        )
        return None

    # ── 6. CLOB order flow ────────────────────────────────────────────────────
    if clob_flow and clob_flow.direction and clob_flow.direction != direction:
        GOLD_SKIP_REASONS.append(
            f"CLOB flow против: деньги идут в {clob_flow.direction} "
            f"(${clob_flow.net_usd:+.0f})"
        )
        return None

    # ── Расчёт уверенности ────────────────────────────────────────────────────
    clob_boost = clob_flow.confidence_boost if clob_flow else 0.0
    clob_usd   = clob_flow.net_usd          if clob_flow else 0.0

    confidence = _calc_confidence(
        gap_pct      = abs(gap_pct),
        hours_left   = h,
        market_prob  = market_prob,
        candle_ok    = candle_dir is not None,
        clob_boost   = clob_boost,
    )

    # Наш edge = насколько мы "умнее" рынка
    our_estimate = _estimate_probability(abs(gap_pct), h)
    edge = our_estimate - market_prob

    # ── Строим причину ────────────────────────────────────────────────────────
    parts = [
        f"{'↑' if direction == 'UP' else '↓'} XAU ${feed.current_price:,.2f}",
        f"разрыв ${abs(gap_abs):.2f} ({abs(gap_pct):.2%})",
        f"рынок {market_prob:.0%} → edge {edge:.0%}",
        f"осталось {h:.1f}ч",
    ]
    if candle_dir:
        parts.append(f"{'🟢🟢' if candle_dir == 'up' else '🔴🔴'}")
    if clob_flow and abs(clob_usd) > 50:
        parts.append(f"CLOB ${clob_usd:+.0f}")

    return GoldSignal(
        direction          = direction,
        confidence         = round(confidence, 3),
        gap_pct            = gap_pct,
        gap_abs            = gap_abs,
        hours_left         = h,
        gold_price         = feed.current_price,
        target_price       = market.price_to_beat,
        market_probability = market_prob,
        edge               = round(edge, 3),
        clob_flow_usd      = clob_usd,
        candle_dir         = candle_dir,
        reason             = " | ".join(parts),
    )


def _estimate_probability(gap_pct: float, hours_left: float) -> float:
    """
    Грубая оценка вероятности что тренд сохранится.
    Чем больше разрыв и меньше времени — тем выше.
    """
    base = 0.60

    # Разрыв: 0.1% → +0.05, 0.3% → +0.15, 0.5%+ → +0.20
    gap_bonus = min(gap_pct / 0.001 * 0.05, 0.20)

    # Времени мало → тренд скорее сохранится
    if hours_left <= 1.5:
        time_bonus = 0.10
    elif hours_left <= 3.0:
        time_bonus = 0.05
    else:
        time_bonus = 0.0

    return min(base + gap_bonus + time_bonus, 0.90)


def _calc_confidence(
    gap_pct:     float,
    hours_left:  float,
    market_prob: float,
    candle_ok:   bool,
    clob_boost:  float,
) -> float:
    """
    Базовая: 0.55 (дневной тренд надёжнее 5-мин)
    + разрыв:      до +0.10
    + время:       до +0.05 (оптимум 1.5–3 часа)
    + рынок зона:  до +0.05 (60–80¢ = есть и тренд и edge)
    + свечи:       +0.03
    + CLOB:        до +0.08
    ──────────────────────
    Максимум:      0.82
    """
    base = 0.55

    # Разрыв
    gap_bonus = min(gap_pct / 0.001 * 0.02, 0.10)

    # Оптимальное время: 1.5–3 часа
    if 1.5 <= hours_left <= 3.0:
        time_bonus = 0.05
    elif 1.0 <= hours_left < 1.5 or 3.0 < hours_left <= 5.0:
        time_bonus = 0.03
    else:
        time_bonus = 0.01

    # Рынок в зоне 60–82%: и тренд виден и edge ещё есть
    if 0.60 <= market_prob <= 0.82:
        market_bonus = 0.05
    elif 0.55 <= market_prob < 0.60:
        market_bonus = 0.02
    else:
        market_bonus = 0.0

    candle_bonus = 0.03 if candle_ok else 0.0

    total = base + gap_bonus + time_bonus + market_bonus + candle_bonus + clob_boost
    return min(total, 0.82)
