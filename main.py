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
from config import BTC_DRY_RUN, ENTRY_WINDOW_MAX
from market_scanner import ActiveMarket, MarketScanner
from notifier import (
    notify_daily_stats, notify_period_report, notify_result, 
    notify_session_stats, notify_signal, notify_start, notify_stop,
)
from price_feed import BTCPriceFeed
from signal_engine import SKIP_REASONS, Signal, check_signal
from stats_tracker import StatsTracker
from trader import PolymarketTrader

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
    trader   = PolymarketTrader()

    await notify_start(BTC_DRY_RUN)
    logger.info(f"Бот запущен | DRY_RUN={BTC_DRY_RUN}")

    feed_task = asyncio.create_task(feed.start())
    logger.info("Ожидание данных с Kraken (5 сек)...")
    await asyncio.sleep(5)

    last_signal_key: str = ""
    last_stats_total: int = 0
    iter_count = 0

    try:
        while True:
            iter_count += 1

            # Ежедневный сброс
            if date.today() != stats.today:
                # Отчет за периоды перед сбросом
                await notify_period_report(
                    "BTC Статистика",
                    tracker.get_period_stats(7),
                    tracker.get_period_stats(30),
                    tracker.get_period_stats(365)
                )
                await notify_daily_stats(stats.signals_sent, 0, 0.0)
                stats = SessionStats()
                tracker.reset_session()

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
                    balance = await trader.get_usdc_balance()
                    if res["result"] == "WIN" and not BTC_DRY_RUN:
                        logger.info("WIN — ждём выплаты от Polymarket (до 5 мин)...")
                        for _ in range(30):  # 30 × 10с = 5 минут макс
                            await asyncio.sleep(10)
                            new_bal = await trader.get_usdc_balance()
                            if new_bal > balance + 0.01:
                                logger.info(f"Баланс обновился: ${balance:.2f} -> ${new_bal:.2f}")
                                balance = new_bal
                                break
                        else:
                            logger.warning("Баланс не изменился за 5 мин после WIN")
                    
                    # Передаем обновленный баланс в уведомление
                    await notify_result(res, balance=balance)

                # Каждые 20 сигналов — показываем статистику
                if tracker.session_total > 0 and tracker.session_total % 20 == 0 and tracker.session_total != last_stats_total:
                    last_stats_total = tracker.session_total
                    all_time = tracker.all_time_stats()
                    balance = await trader.get_usdc_balance()
                    await notify_session_stats(tracker.session_summary(), all_time, balance=balance)

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

                # ── Порог уверенности (59.5%) ──
                if signal.confidence < 0.595:
                    logger.info(f"Пропуск: уверенность {signal.confidence:.1%} < 59.5%")
                    from notifier import notify_skip
                    await notify_skip(f"Уверенность {signal.confidence:.1%} < 59.5%", signal.seconds_left, signal.btc_price)
                    stats.on_skip()
                    continue

                # 1. Сначала уведомляем о сигнале/ставке
                # Если это не DRY_RUN, пробуем поставить
                order_id = None
                bet_amount = 1.10 # Минимальный безопасный размер
                entry_price = 0.99
                
                # Получаем баланс перед ставкой
                balance = await trader.get_usdc_balance()

                if not BTC_DRY_RUN:
                    token_id = market.up_token_id if signal.direction == "UP" else market.down_token_id
                    if token_id:
                        logger.info(f"Торговля (${bet_amount}): {signal.direction} | баланс=${balance:.2f}")
                        order_id = await trader.place_market_order(token_id, bet_amount, side="BUY")
                        if order_id:
                            logger.info(f"Ордер размещён: {order_id}")
                            # Записываем в статистику (результат узнаем после закрытия)
                            tracker.record_signal(signal, market, bet_amount=bet_amount, entry_price=entry_price)
                        else:
                            logger.error("Ордер не прошёл")
                            continue
                    else:
                        logger.error("Token ID не найден!")
                        continue
                else:
                    # В симуляции просто записываем
                    tracker.record_signal(signal, market, bet_amount=bet_amount, entry_price=0.50)

                # 2. Уведомляем Telegram
                await notify_signal(
                    signal, 
                    market.question, 
                    dry_run=BTC_DRY_RUN, 
                    balance=balance,
                    bet_amount=bet_amount,
                    entry_price=entry_price if not BTC_DRY_RUN else 0.50
                )

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
        balance = await trader.get_usdc_balance()
        await notify_session_stats(tracker.session_summary(), all_time, balance=balance)
        logger.info(f"Бот остановлен. {stats.summary()}")
        logger.info(f"Статистика сигналов: {tracker.session_summary()}")
        await notify_stop(f"Остановка. {stats.summary()}")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено.")
