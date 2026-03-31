"""
Трекер статистики сигналов.

Записывает каждый сигнал в CSV и проверяет результат
после закрытия раунда (сравнивая BTC цену с таргетом).
"""
import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from signal_engine import Signal

logger = logging.getLogger(__name__)

STATS_FILE = Path("stats.csv")
CSV_HEADERS = [
    "timestamp", "market", "direction", "confidence",
    "btc_at_signal", "target", "gap_at_signal",
    "btc_at_close", "gap_at_close", "result",
    "momentum", "seconds_left", "market_prob",
    "bet_amount", "payout", "profit"
]


@dataclass
class PendingSignal:
    """Сигнал ожидающий результата."""
    condition_id: str
    question: str
    direction: str
    confidence: float
    btc_at_signal: float
    target: float
    gap_at_signal: float
    momentum: float
    seconds_left: int
    market_prob: float
    timestamp: str
    end_date: str       # ISO когда закрывается раунд
    bet_amount: float = 0.0
    entry_price: float = 0.0


class StatsTracker:
    def __init__(self):
        self._session_total   = 0
        self._session_wins    = 0
        self._session_losses  = 0
        self._session_profit  = 0.0
        self._ensure_csv()

    def _ensure_csv(self):
        if not STATS_FILE.exists():
            with open(STATS_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)
            logger.info(f"Создан файл статистики: {STATS_FILE}")

    def record_signal(self, signal: Signal, market, bet_amount: float = 0.0, entry_price: float = 0.0) -> None:
        """Записываем сигнал — результат проверим позже."""
        pending = PendingSignal(
            condition_id   = market.condition_id,
            question       = market.question,
            direction      = signal.direction,
            confidence     = signal.confidence,
            btc_at_signal  = signal.btc_price,
            target         = market.price_to_beat,
            gap_at_signal  = signal.gap,
            momentum       = signal.momentum,
            seconds_left   = signal.seconds_left,
            market_prob    = signal.market_probability,
            timestamp      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            end_date       = market.end_date,
            bet_amount     = bet_amount,
            entry_price    = entry_price,
        )
        self._pending.append(pending)
        logger.info(f"Сигнал записан в ожидание: {signal.direction} | bet=${bet_amount} | {market.question}")

    def check_pending(self, current_btc_price: float, current_time_iso: str) -> list[dict]:
        """
        Проверяем закрытые раунды и записываем результат.
        Вызывать каждую секунду.
        """
        now = datetime.now(timezone.utc)
        resolved = []

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

            # Раунд закрылся — определяем результат
            # UP выигрывает если цена >= таргет, DOWN если < таргет
            if p.direction == "UP":
                win = current_btc_price >= p.target
            else:
                win = current_btc_price < p.target

            result = "WIN" if win else "LOSS"
            gap_at_close = current_btc_price - p.target
            
            payout = (p.bet_amount / p.entry_price) if win and p.entry_price > 0 else 0.0
            profit = (payout - p.bet_amount) if win else -p.bet_amount

            self._write_result(p, current_btc_price, gap_at_close, result, payout, profit)

            if win:
                self._session_wins += 1
            else:
                self._session_losses += 1
            
            self._session_total += 1
            self._session_profit += profit

            resolved.append({
                "question":     p.question,
                "direction":    p.direction,
                "result":       result,
                "btc_signal":   p.btc_at_signal,
                "btc_close":    current_btc_price,
                "target":       p.target,
                "confidence":   p.confidence,
                "bet_amount":   p.bet_amount,
                "payout":       payout,
                "profit":       profit
            })
            logger.info(
                f"Результат: {result} | {p.direction} | "
                f"таргет=${p.target:.2f} | закрытие=${current_btc_price:.2f}"
            )

        self._pending = still_pending
        return resolved

    def reset_session(self):
        """Сбрасывает дневную статистику."""
        self._session_total = 0
        self._session_wins = 0
        self._session_losses = 0
        self._session_profit = 0.0
        logger.info("Статистика сессии сброшена (24 часа)")

    def _write_result(
        self,
        p: PendingSignal,
        btc_at_close: float,
        gap_at_close: float,
        result: str,
        payout: float = 0.0,
        profit: float = 0.0,
    ) -> None:
        with open(STATS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                p.timestamp,
                p.question,
                p.direction,
                f"{p.confidence:.3f}",
                f"{p.btc_at_signal:.2f}",
                f"{p.target:.2f}",
                f"{p.gap_at_signal:.2f}",
                f"{btc_at_close:.2f}",
                f"{gap_at_close:.2f}",
                result,
                f"{p.momentum:.4f}",
                p.seconds_left,
                f"{p.market_prob:.2f}",
                f"{p.bet_amount:.2f}",
                f"{payout:.2f}",
                f"{profit:.2f}",
            ])

    # ── Итоги сессии ──────────────────────────────────────────────────────────

    @property
    def session_total(self) -> int:
        return self._session_total

    @property
    def win_rate(self) -> float:
        if self._session_total == 0:
            return 0.0
        return self._session_wins / self._session_total

    def session_summary(self) -> str:
        t = self._session_total
        w = self._session_wins
        l = self._session_losses
        wr = self.win_rate
        return f"Всего сигналов: {t}\n✅ {w} - ❌ {l}\nWin rate: {wr:.1%}"

    def all_time_stats(self) -> dict:
        """Читает CSV и возвращает общую статистику за всё время."""
        if not STATS_FILE.exists():
            return {}
        total = wins = losses = 0
        total_profit = 0.0
        conf_wins = conf_losses = 0.0
        with open(STATS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                conf = float(row.get("confidence", 0))
                profit = float(row.get("profit", 0.0))
                total_profit += profit
                if row.get("result") == "WIN":
                    wins += 1
                    conf_wins += conf
                elif row.get("result") == "LOSS":
                    losses += 1
                    conf_losses += conf
        return {
            "total":     total,
            "wins":      wins,
            "losses":    losses,
            "win_rate":  wins / total if total > 0 else 0,
            "total_profit": total_profit,
            "avg_conf_wins":   conf_wins / wins if wins > 0 else 0,
            "avg_conf_losses": conf_losses / losses if losses > 0 else 0,
        }

    def get_period_stats(self, days: int) -> dict:
        """Статистика за последние X дней."""
        if not STATS_FILE.exists():
            return {"total": 0, "wins": 0, "win_rate": 0}
        
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        total = wins = 0
        
        with open(STATS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # формат: 2026-03-31 10:14:07
                    ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        total += 1
                        if row.get("result") == "WIN":
                            wins += 1
                except Exception:
                    continue
        return {"total": total, "wins": wins, "win_rate": wins/total if total > 0 else 0}
