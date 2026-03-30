"""
Telegram уведомления о сигналах.
"""
import logging
from typing import Optional

import httpx

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from signal_engine import Signal

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_message(text: str) -> bool:
    """Отправляет сообщение в Telegram. Возвращает True при успехе."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram не настроен (нет TOKEN или CHAT_ID)")
        return False

    url = TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            })
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
        return False


async def notify_signal(signal: Signal, market_question: str, dry_run: bool = True) -> None:
    """Красивое уведомление о торговом сигнале."""
    icon = "🚀" if signal.direction == "UP" else "🔻"
    mode = "📊 СИГНАЛ (симуляция)" if dry_run else "⚡ РЕАЛЬНАЯ СТАВКА"

    text = (
        f"{icon} <b>{mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 Рынок: <b>{market_question}</b>\n"
        f"🎯 Направление: <b>{signal.direction}</b>\n"
        f"💰 BTC цена: <b>${signal.btc_price:,.2f}</b>\n"
        f"📏 Разрыв от таргета: <b>${abs(signal.gap):.2f}</b>\n"
        f"⏱ Осталось: <b>{signal.seconds_left}s</b>\n"
        f"📊 Импульс: <b>{signal.momentum:.3%}</b>\n"
        f"🎲 Уверенность: <b>{signal.confidence:.1%}</b>\n"
        f"🏦 Рынок оценивает: <b>{signal.market_probability:.0%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>{signal.reason}</i>"
    )
    await send_message(text)


async def notify_skip(reason: str, seconds_left: int, btc_price: float) -> None:
    """Уведомление почему сигнал пропущен (только для отладки)."""
    text = (
        f"⏭ <b>Пропуск</b> | {seconds_left}s | BTC ${btc_price:,.2f}\n"
        f"<i>Причина: {reason}</i>"
    )
    await send_message(text)


async def notify_start(dry_run: bool) -> None:
    mode = "симуляция (DRY RUN)" if dry_run else "РЕАЛЬНАЯ ТОРГОВЛЯ"
    text = (
        f"🤖 <b>Polymarket Signal Bot запущен</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Стратегия: Импульс + Последние 60 секунд\n"
        f"Актив: Bitcoin Up/Down — 5 minutes"
    )
    await send_message(text)


async def notify_stop(reason: str) -> None:
    await send_message(f"🛑 <b>Бот остановлен</b>\nПричина: {reason}")


async def notify_result(result: dict) -> None:
    """Уведомление о результате закрытого раунда."""
    win     = result["result"] == "WIN"
    icon    = "✅" if win else "❌"
    dir_arrow = "↑" if result["direction"] == "UP" else "↓"
    text = (
        f"{icon} <b>Результат: {'ВЕРНО' if win else 'НЕВЕРНО'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Сигнал: <b>{dir_arrow} {result['direction']}</b>\n"
        f"BTC при сигнале: <b>${result['btc_signal']:,.2f}</b>\n"
        f"BTC при закрытии: <b>${result['btc_close']:,.2f}</b>\n"
        f"Таргет: <b>${result['target']:,.2f}</b>\n"
        f"Уверенность была: <b>{result['confidence']:.1%}</b>"
    )
    await send_message(text)


async def notify_session_stats(summary: str, all_time: dict) -> None:
    """Статистика сессии и за всё время."""
    wr_all = all_time.get("win_rate", 0)
    total  = all_time.get("total", 0)
    wins   = all_time.get("wins", 0)
    icon   = "📈" if wr_all >= 0.53 else "📉"
    text = (
        f"📊 <b>Статистика</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Сессия:</b> {summary}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Всё время:</b>\n"
        f"Сигналов: <b>{total}</b> | Верных: <b>{wins}</b>\n"
        f"{icon} Win rate: <b>{wr_all:.1%}</b>"
    )
    await send_message(text)


async def notify_daily_stats(
    trades: int,
    wins: int,
    total_pnl: float,
) -> None:
    win_rate = wins / trades if trades > 0 else 0
    pnl_icon = "📈" if total_pnl >= 0 else "📉"
    text = (
        f"📋 <b>Дневная статистика</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Сигналов: <b>{trades}</b>\n"
        f"Верных: <b>{wins}</b> ({win_rate:.1%})\n"
        f"{pnl_icon} P&L: <b>${total_pnl:+.2f}</b>"
    )
    await send_message(text)
