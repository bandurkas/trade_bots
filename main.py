"""
Главный цикл сигнального бота.

Запуск:
  python main.py

Режимы:
  DRY_RUN=true  → только Telegram уведомления, никаких ставок
  DRY_RUN=false → (будущее) реальные ставки
"""
import asyncio
import logging
import sys
from datetime import date, datetime, timezone

from clob_analyzer import CLOBAnalyzer
from config import DRY_RUN, ENTRY_WINDOW_MAX
from market_scanner import ActiveMarket, MarketScanner
from notifier import (
    notify_daily_stats, notify_result, notify_session_stats,
    notify_signal, notify_start, notify_stop,
)
from price_feed import BTCPriceFeed
from signal_engine import SKIP_REASONS, Signal, check_signal
from stats_tracker import StatsTracker

# ── Логирование ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ── Статистика сессии ──────────────────────────────────────────────────────────
class SessionStats:
    def __init__(self):
        self.signals_sent = 0
        self.skipped      = 0
        self.start_time   = datetime.now()
        self.today        = date.today()

    def on_signal(self):
        self.signals_sent += 1

    def on_skip(self):
        self.skipped += 1

    def summary(self) -> str:
        uptime = datetime.now() - self.start_time
        h, rem = divmod(int(uptime.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        return (
            f"Uptime: {h:02d}:{m:02d}:{s:02d} | "
            f"Сигналов: {self.signals_sent} | "
            f"Пропущено: {self.skipped}"
        )


# ── Основной цикл ──────────────────────────────────────────────────────────────
async def run():
    feed     = BTCPriceFeed()
    scanner  = MarketScanner()
    tracker  = StatsTracker()
    clob     = CLOBAnalyzer()
    stats    = SessionStats()

    await notify_start(DRY_RUN)
    logger.info(f"Бот запущен | DRY_RUN={DRY_RUN}")

    feed_task = asyncio.create_task(feed.start())
    logger.info("Ожидание данных с Kraken (5 сек)...")
    await asyncio.sleep(5)

    last_signal_key: str = ""
    iter_count = 0

    try:
        while True:
            iter_count += 1

            # Ежедневный сброс
            if date.today() != stats.today:
                await notify_daily_stats(stats.signals_sent, 0, 0.0)
                stats = SessionStats()

            # ── Проверяем результаты закрытых раундов ──
            if feed.current_price:
                resolved = tracker.check_pending(
                    current_btc_price=feed.current_price,
                    current_time_iso=datetime.now(timezone.utc).isoformat(),
                )
                for res in resolved:
                    icon = "✅" if res["result"] == "WIN" else "❌"
                    logger.info(
                        f"{icon} Результат: {res['result']} | "
                        f"{res['direction']} | "
                        f"таргет=${res['target']:.2f} | "
                        f"закрытие=${res['btc_close']:.2f}"
                    )
                    await notify_result(res)

                # Каждые 20 сигналов — показываем статистику
                if tracker.session_total > 0 and tracker.session_total % 20 == 0:
                    all_time = tracker.all_time_stats()
                    await notify_session_stats(tracker.session_summary(), all_time)

            # ── Получаем текущий рынок ──
            market: ActiveMarket | None = await scanner.get_active_btc_market(
                current_btc_price=feed.current_price
            )

            if not market:
                logger.debug("Активный рынок не найден, ждём...")
                await asyncio.sleep(5)
                continue

            if not feed.is_ready:
                logger.debug("Ждём свечей (нужно 3 закрытые минуты)...")
                await asyncio.sleep(2)
                continue

            btc  = feed.current_price
            gap  = btc - market.price_to_beat
            secs = market.seconds_to_close

            # Подробный лог каждые 10 итераций
            if iter_count % 10 == 0:
                rsi = feed.get_rsi()
                m5  = feed.get_momentum_5m()
                logger.info(
                    f"BTC=${btc:,.2f} | таргет=${market.price_to_beat:,.2f} | "
                    f"разрыв=${gap:+.2f} | осталось={secs}s | "
                    f"UP={market.up_price:.0%} DOWN={market.down_price:.0%} | "
                    f"RSI={f'{rsi:.0f}' if rsi else 'н/д'} | "
                    f"5м={f'{m5:.3%}' if m5 else 'н/д'}"
                )

            # Слишком рано — ждём
            if secs > ENTRY_WINDOW_MAX + 30:
                await asyncio.sleep(3)
                continue

            # ── Получаем CLOB order flow ──
            clob_flow = None
            if market.up_token_id and market.down_token_id:
                clob_flow = await clob.analyze(
                    market.up_token_id,
                    market.down_token_id,
                )

            # ── Проверяем сигнал ──
            signal: Signal | None = check_signal(feed, market, clob_flow=clob_flow)

            if signal:
                signal_key = f"{market.condition_id}_{signal.direction}"
                if signal_key == last_signal_key:
                    await asyncio.sleep(1)
                    continue

                last_signal_key = signal_key
                stats.on_signal()

                logger.info(
                    f">>> СИГНАЛ {signal.direction} | "
                    f"уверенность={signal.confidence:.1%} | "
                    f"{signal.reason}"
                )

                # Отправляем в Telegram
                await notify_signal(signal, market.question, dry_run=DRY_RUN)

                # Записываем в статистику (результат узнаем после закрытия)
                tracker.record_signal(signal, market)

            else:
                stats.on_skip()
                if SKIP_REASONS:
                    logger.debug(f"Пропуск: {'; '.join(SKIP_REASONS)}")

            await asyncio.sleep(1)

    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        await notify_stop(str(e))
    finally:
        feed_task.cancel()
        await scanner.close()
        await clob.close()

        # Финальная статистика
        all_time = tracker.all_time_stats()
        await notify_session_stats(tracker.session_summary(), all_time)
        logger.info(f"Бот остановлен. {stats.summary()}")
        logger.info(f"Статистика сигналов: {tracker.session_summary()}")
        await notify_stop(f"Остановка. {stats.summary()}")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено.")
