"""
Трекер статистики для Gold сигналов.

Записывает каждый дневной прогноз и проверяет результат
после закрытия рынка (сравнивая XAU цену с таргетом).
"""
import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gold_signal import GoldSignal

logger = logging.getLogger(__name__)

STATS_FILE  = Path("gold_stats.csv")
CSV_HEADERS = [
    "date", "market", "direction", "confidence",
    "xau_at_signal", "target", "gap_pct_at_signal",
    "xau_at_close", "gap_pct_at_close", "result",
    "hours_left", "market_prob", "edge", "clob_flow_usd",
]


@dataclass
class PendingGoldSignal:
    condition_id:   str
    question:       str
    direction:      str
    confidence:     float
    xau_at_signal:  float
    target:         float
    gap_pct:        float
    hours_left:     float
    market_prob:    float
    edge:           float
    clob_flow_usd:  float
    date:           str   # YYYY-MM-DD
    end_date:       str   # ISO когда закрывается рынок


class GoldStatsTracker:
    def __init__(self):
        self._pending: list[PendingGoldSignal] = []
        self._session_total   = 0
        self._session_wins    = 0
        self._session_losses  = 0
        self._ensure_csv()

    def _ensure_csv(self):
        if not STATS_FILE.exists():
            with open(STATS_FILE, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(CSV_HEADERS)
            logger.info(f"Создан файл статистики: {STATS_FILE}")

    def record_signal(self, signal: GoldSignal, market) -> None:
        pending = PendingGoldSignal(
            condition_id  = market.condition_id,
            question      = market.question,
            direction     = signal.direction,
            confidence    = signal.confidence,
            xau_at_signal = signal.gold_price,
            target        = signal.target_price,
            gap_pct       = signal.gap_pct,
            hours_left    = signal.hours_left,
            market_prob   = signal.market_probability,
            edge          = signal.edge,
            clob_flow_usd = signal.clob_flow_usd,
            date          = datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            end_date      = market.end_date,
        )
        self._pending.append(pending)
        logger.info(
            f"Gold сигнал записан: {signal.direction} | "
            f"XAU=${signal.gold_price:.2f} | таргет=${signal.target_price:.2f}"
        )

    def check_pending(self, current_xau_price: float) -> list[dict]:
        """
        Проверяем закрытые дневные рынки.
        Вызывать периодически (каждые несколько минут достаточно).
        """
        now          = datetime.now(timezone.utc)
        resolved     = []
        still_pending = []

        for p in self._pending:
            try:
                end = datetime.fromisoformat(p.end_date.replace("Z", "+00:00"))
            except Exception:
                still_pending.append(p)
                continue

            if now < end:
                still_pending.append(p)
                continue

            # Рынок закрылся — определяем результат
            if p.direction == "UP":
                win = current_xau_price >= p.target
            else:
                win = current_xau_price < p.target

            result       = "WIN" if win else "LOSS"
            gap_at_close = (current_xau_price - p.target) / p.target

            self._write_result(p, current_xau_price, gap_at_close, result)

            if win:
                self._session_wins += 1
            else:
                self._session_losses += 1
            self._session_total += 1

            resolved.append({
                "question":   p.question,
                "direction":  p.direction,
                "result":     result,
                "xau_signal": p.xau_at_signal,
                "xau_close":  current_xau_price,
                "target":     p.target,
                "confidence": p.confidence,
                "edge":       p.edge,
            })
            logger.info(
                f"Gold результат: {result} | {p.direction} | "
                f"таргет=${p.target:.2f} | закрытие=${current_xau_price:.2f}"
            )

        self._pending = still_pending
        return resolved

    def _write_result(
        self, p: PendingGoldSignal,
        xau_at_close: float,
        gap_at_close: float,
        result: str,
    ) -> None:
        with open(STATS_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                p.date,
                p.question,
                p.direction,
                f"{p.confidence:.3f}",
                f"{p.xau_at_signal:.2f}",
                f"{p.target:.2f}",
                f"{p.gap_pct:.4f}",
                f"{xau_at_close:.2f}",
                f"{gap_at_close:.4f}",
                result,
                f"{p.hours_left:.1f}",
                f"{p.market_prob:.2f}",
                f"{p.edge:.3f}",
                f"{p.clob_flow_usd:.0f}",
            ])

    # ── Итоги ─────────────────────────────────────────────────────────────────

    @property
    def session_total(self) -> int:
        return self._session_total

    @property
    def win_rate(self) -> float:
        if self._session_total == 0:
            return 0.0
        return self._session_wins / self._session_total

    def session_summary(self) -> str:
        return (
            f"Всего: {self._session_total} | "
            f"✅ {self._session_wins} | "
            f"❌ {self._session_losses} | "
            f"Win rate: {self.win_rate:.1%}"
        )

    def all_time_stats(self) -> dict:
        if not STATS_FILE.exists():
            return {}
        total = wins = losses = 0
        with open(STATS_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += 1
                if row.get("result") == "WIN":
                    wins += 1
                elif row.get("result") == "LOSS":
                    losses += 1
        return {
            "total":    total,
            "wins":     wins,
            "losses":   losses,
            "win_rate": wins / total if total > 0 else 0,
        }
