import logging
from typing import Any
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

async def send_message(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram не настроен!")
        return
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Ошибка Telegram API: {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")


async def notify_start(dry_run: bool) -> None:
    mode = "🛠 <b>DRY RUN</b> (симуляция)" if dry_run else "🚀 <b>LIVE</b> (реальные ставки)"
    await send_message(f"✅ <b>Бот запущен!</b>\nРежим: {mode}")


async def notify_stop(reason: str = "") -> None:
    await send_message(f"🛑 <b>Бот остановлен!</b>\nПричина: {reason}")


async def notify_signal(
    signal: Any,
    question: str,
    dry_run: bool = True,
    balance: float = 0.0,
) -> None:
    mode_icon = "🧪" if dry_run else "💰"
    dir_icon  = "🟢 UP" if signal.direction == "UP" else "🔴 DOWN"

    if not dry_run:
        bet_info = (
            f"\n🎯 Ставка: <b>$1.00 USDC</b>"
            f"\n💳 Баланс до ставки: <b>${balance:,.2f}</b>"
        )
    else:
        bet_info = ""

    text = (
        f"{mode_icon} <b>{'СТАВКА ОТКРЫТА' if not dry_run else 'НОВЫЙ СИГНАЛ'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 Рынок: <b>{question}</b>\n"
        f"Направление: <b>{dir_icon}</b>\n"
        f"Цена BTC: <b>${signal.btc_price:,.2f}</b>\n"
        f"Уверенность: <b>{signal.confidence:.1%}</b>\n"
        f"До закрытия: <b>{signal.seconds_left} сек</b>"
        f"{bet_info}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💬 {signal.reason}"
    )
    await send_message(text)


async def notify_skip(reason: str, seconds: int, price: float) -> None:
    text = (
        f"⏭ <b>Сигнал пропущен</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Причина: <b>{reason}</b>\n"
        f"BTC: <b>${price:,.2f}</b> | Ост. время: <b>{seconds} сек</b>"
    )
    await send_message(text)


async def notify_result(result: dict, balance: float = 0.0) -> None:
    icon      = "✅ <b>WIN</b>" if result["result"] == "WIN" else "❌ <b>LOSS</b>"
    dir_arrow = "⬆️" if result["direction"] == "UP" else "⬇️"
    next_line = "\n🔄 Ищем следующую сделку..." if result["result"] == "WIN" else ""

    text = (
        f"{icon} <b>СДЕЛКА ЗАВЕРШЕНА</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 Рынок: <b>{result.get('question', '')}</b>\n"
        f"Сигнал: <b>{dir_arrow} {result['direction']}</b>\n"
        f"BTC при ставке: <b>${result['btc_signal']:,.2f}</b>\n"
        f"BTC при закрытии: <b>${result['btc_close']:,.2f}</b>\n"
        f"Уверенность была: <b>{result['confidence']:.1%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 Текущий баланс: <b>${balance:,.2f}</b>"
        f"{next_line}"
    )
    await send_message(text)


async def notify_session_stats(summary: str, all_time: dict, balance: float = 0.0) -> None:
    wr_all = all_time.get("win_rate", 0)
    total  = all_time.get("total", 0)
    wins   = all_time.get("wins", 0)
    icon   = "📈" if wr_all >= 0.53 else "📉"
    text = (
        f"📊 <b>Статистика BTC</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Сессия:</b>\n{summary}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 Баланс: <b>${balance:,.2f}</b>\n"
        f"<b>Всё время:</b>\n"
        f"Сигналов: <b>{total}</b> | Верных: <b>{wins}</b>\n"
        f"{icon} Win rate: <b>{wr_all:.1%}</b>"
    )
    await send_message(text)


async def notify_period_report(title: str, stats_7d: dict, stats_30d: dict, stats_365d: dict) -> None:
    def fmt(s):
        w  = s["wins"]
        l  = s["total"] - s["wins"]
        wr = s["win_rate"]
        return f"✅ {w} - ❌ {l} | WR: {wr:.1%}"

    text = (
        f"📅 <b>ОТЧЕТ ПО ПЕРИОДАМ: {title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🗓 <b>За 7 дней:</b>\n{fmt(stats_7d)}\n\n"
        f"🗓 <b>За 30 дней:</b>\n{fmt(stats_30d)}\n\n"
        f"🗓 <b>За всё время:</b>\n{fmt(stats_365d)}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await send_message(text)


async def notify_daily_stats(total: int, wins: int, wr: float) -> None:
    await send_message(
        f"📅 <b>Итоги дня</b>\n"
        f"Всего: <b>{total}</b> | Верных: <b>{wins}</b>\n"
        f"Win rate: <b>{wr:.1%}</b>"
    )


# ── Gold уведомления ──────────────────────────────────────────────────────────

async def notify_gold_start(dry_run: bool) -> None:
    mode = "🛠 <b>DRY RUN</b>" if dry_run else "🚀 <b>LIVE</b>"
    await send_message(f"🥇 ✅ <b>Gold Бот запущен!</b>\nРежим: {mode}")


async def notify_gold_signal(signal: Any, question: str, dry_run: bool = True) -> None:
    mode_icon = "🧪" if dry_run else "💰"
    dir_icon  = "🟢 UP" if signal.direction == "UP" else "🔴 DOWN"
    text = (
        f"🥇 {mode_icon} <b>{'GOLD СТАВКА ОТКРЫТА' if not dry_run else 'GOLD СИГНАЛ'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 Рынок: <b>{question}</b>\n"
        f"Направление: <b>{dir_icon}</b>\n"
        f"Цена Gold: <b>${signal.gold_price:,.2f}</b>\n"
        f"Уверенность: <b>{signal.confidence:.1%}</b>\n"
        f"Edge: <b>{signal.edge:.1%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💬 {signal.reason}"
    )
    await send_message(text)


async def notify_gold_result(res: dict, balance: float = 0.0) -> None:
    icon     = "✅" if res["result"] == "WIN" else "❌"
    arrow    = "↑" if res["direction"] == "UP" else "↓"
    status   = "ВИН" if res["result"] == "WIN" else "ЛОСС"
    edge_str = f"+{res['edge']:.0%}" if res["edge"] > 0 else f"{res['edge']:.0%}"
    await send_message(
        f"🥇 {icon} <b>Gold сделка завершена: {status}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Прогноз: <b>{arrow} {res['direction']}</b>\n"
        f"XAU при сигнале: <b>${res['xau_signal']:,.2f}</b>\n"
        f"XAU при закрытии: <b>${res['xau_close']:,.2f}</b>\n"
        f"Edge был: <b>{edge_str}</b>\n"
        f"Уверенность была: <b>{res['confidence']:.1%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 Текущий баланс: <b>${balance:,.2f}</b>"
    )
