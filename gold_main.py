"""
Gold Signal Bot — дневные рынки Gold (XAUUSD) на Polymarket.

Стратегия: торгуем когда тренд дня установился, но рынок ещё даёт edge.
Оптимальное время (Jakarta/WIB UTC+7):
  Лондонская сессия: 15:00–18:00 WIB
  Нью-Йоркская:      21:30–01:00 WIB
  Лучшее окно:       15:00–20:00 WIB (2–4 часа до закрытия NY)

Запуск: python gold_main.py
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

from clob_analyzer import CLOBAnalyzer
from config import DRY_RUN
from gold_price_feed import GoldPriceFeed
from gold_scanner import GoldMarket, GoldScanner
from gold_signal import GoldSignal, GOLD_SKIP_REASONS, check_gold_signal
from gold_stats_tracker import GoldStatsTracker
from notifier import send_message

# ── Логирование ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("gold_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("gold_main")


# ── Уведомления ───────────────────────────────────────────────────────────────

async def notify_gold_start(dry_run: bool) -> None:
    mode = "симуляция (DRY RUN)" if dry_run else "РЕАЛЬНАЯ ТОРГОВЛЯ"
    await send_message(
        f"🥇 <b>Gold Signal Bot запущен</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Стратегия: Дневной тренд XAU/USD\n"
        f"Лучшее окно: <b>15:00–20:00 WIB</b> (Лондон + ранняя NY)"
    )


async def notify_gold_signal(signal: GoldSignal, question: str, dry_run: bool) -> None:
    icon     = "🚀" if signal.direction == "UP" else "🔻"
    mode     = "📊 СИГНАЛ (симуляция)" if dry_run else "⚡ РЕАЛЬНАЯ СТАВКА"
    edge_str = f"+{signal.edge:.0%}" if signal.edge > 0 else f"{signal.edge:.0%}"

    await send_message(
        f"🥇 {icon} <b>GOLD {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 Рынок: <b>{question}</b>\n"
        f"🎯 Направление: <b>{signal.direction}</b>\n"
        f"💰 XAU цена: <b>${signal.gold_price:,.2f}</b>\n"
        f"🏁 Таргет: <b>${signal.target_price:,.2f}</b>\n"
        f"📏 Разрыв: <b>${abs(signal.gap_abs):.2f} ({abs(signal.gap_pct):.2%})</b>\n"
        f"⏱ Осталось: <b>{signal.hours_left:.1f} часов</b>\n"
        f"🏦 Рынок оценивает: <b>{signal.market_probability:.0%}</b>\n"
        f"📐 Наш edge: <b>{edge_str}</b>\n"
        f"🎲 Уверенность: <b>{signal.confidence:.1%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>{signal.reason}</i>"
    )


async def notify_gold_result(res: dict) -> None:
    icon      = "✅" if res["result"] == "WIN" else "❌"
    arrow     = "↑" if res["direction"] == "UP" else "↓"
    correct   = "ВЕРНО" if res["result"] == "WIN" else "НЕВЕРНО"
    edge_str  = f"+{res['edge']:.0%}" if res["edge"] > 0 else f"{res['edge']:.0%}"

    await send_message(
        f"🥇 {icon} <b>Gold результат: {correct}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Прогноз: <b>{arrow} {res['direction']}</b>\n"
        f"XAU при сигнале: <b>${res['xau_signal']:,.2f}</b>\n"
        f"XAU при закрытии: <b>${res['xau_close']:,.2f}</b>\n"
        f"Таргет: <b>${res['target']:,.2f}</b>\n"
        f"Edge был: <b>{edge_str}</b>\n"
        f"Уверенность была: <b>{res['confidence']:.1%}</b>"
    )


async def notify_gold_stats(summary: str, all_time: dict) -> None:
    total    = all_time.get("total", 0)
    wins     = all_time.get("wins", 0)
    win_rate = all_time.get("win_rate", 0)
    icon     = "📈" if win_rate >= 0.55 else "📉"

    await send_message(
        f"🥇 📊 <b>Gold статистика</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Сессия:</b> {summary}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Всё время:</b>\n"
        f"Прогнозов: <b>{total}</b> | Верных: <b>{wins}</b>\n"
        f"{icon} Win rate: <b>{win_rate:.1%}</b>"
    )


# ── Основной цикл ──────────────────────────────────────────────────────────────

async def run():
    feed    = GoldPriceFeed()
    scanner = GoldScanner()
    clob    = CLOBAnalyzer()
    tracker = GoldStatsTracker()

    await notify_gold_start(DRY_RUN)
    logger.info(f"Gold бот запущен | DRY_RUN={DRY_RUN}")

    feed_task = asyncio.create_task(feed.start())
    logger.info("Ожидание данных XAU/USD (10 сек)...")
    await asyncio.sleep(10)

    signaled_markets: set[str] = set()
    iter_count = 0

    try:
        while True:
            iter_count += 1

            if not feed.current_price:
                await asyncio.sleep(10)
                continue

            # ── Проверяем результаты закрытых рынков ──
            resolved = tracker.check_pending(current_xau_price=feed.current_price)
            for res in resolved:
                icon = "✅" if res["result"] == "WIN" else "❌"
                logger.info(
                    f"{icon} Gold результат: {res['result']} | "
                    f"{res['direction']} | "
                    f"таргет=${res['target']:.2f} | "
                    f"закрытие=${res['xau_close']:.2f}"
                )
                await notify_gold_result(res)

            # После каждого результата — показываем статистику
            if resolved:
                all_time = tracker.all_time_stats()
                await notify_gold_stats(tracker.session_summary(), all_time)

            # ── Получаем рынок ──
            market: GoldMarket | None = await scanner.get_gold_market(
                current_gold_price=feed.current_price
            )

            if not market:
                if iter_count % 12 == 0:
                    logger.info(f"XAU=${feed.current_price:,.2f} | Gold рынок не найден")
                await asyncio.sleep(30)
                continue

            # Лог каждые 2 минуты
            if iter_count % 24 == 0:
                gap     = feed.get_gap_from_target(market.price_to_beat)
                gap_str = f"{gap:+.2%}" if gap is not None else "н/д"
                logger.info(
                    f"XAU=${feed.current_price:,.2f} | "
                    f"таргет=${market.price_to_beat:,.2f} | "
                    f"разрыв={gap_str} | "
                    f"UP={market.up_price:.0%} DOWN={market.down_price:.0%} | "
                    f"осталось={market.hours_to_close:.1f}ч"
                )

            # ── Уже отправляли сигнал по этому рынку ──
            if market.condition_id in signaled_markets:
                await asyncio.sleep(5)
                continue

            # ── CLOB order flow ──
            clob_flow = None
            if market.up_token_id and market.down_token_id:
                clob_flow = await clob.analyze(market.up_token_id, market.down_token_id)

            # ── Проверяем сигнал ──
            signal = check_gold_signal(feed, market, clob_flow=clob_flow)

            if signal:
                signaled_markets.add(market.condition_id)

                logger.info(
                    f">>> GOLD СИГНАЛ {signal.direction} | "
                    f"уверенность={signal.confidence:.1%} | "
                    f"edge={signal.edge:.0%} | "
                    f"{signal.reason}"
                )

                await notify_gold_signal(signal, market.question, dry_run=DRY_RUN)
                tracker.record_signal(signal, market)

            else:
                if GOLD_SKIP_REASONS and iter_count % 24 == 0:
                    logger.debug(f"Пропуск: {'; '.join(GOLD_SKIP_REASONS)}")

            await asyncio.sleep(5)

    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        await send_message(f"🛑 Gold бот упал: {e}")
    finally:
        feed_task.cancel()
        await scanner.close()
        await clob.close()

        # Финальная статистика
        all_time = tracker.all_time_stats()
        await notify_gold_stats(tracker.session_summary(), all_time)
        logger.info(f"Gold бот остановлен. {tracker.session_summary()}")
        await send_message("🛑 Gold бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено.")
