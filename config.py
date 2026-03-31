import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Режим работы
DRY_RUN      = os.getenv("DRY_RUN", "true").lower() == "true"
BTC_DRY_RUN  = os.getenv("BTC_DRY_RUN", os.getenv("DRY_RUN", "true")).lower() == "true"
GOLD_DRY_RUN = os.getenv("GOLD_DRY_RUN", os.getenv("DRY_RUN", "true")).lower() == "true"
BANKROLL     = float(os.getenv("BANKROLL", "50.0"))

# Стратегия: "Импульс + Последние 60 секунд"
MOMENTUM_THRESHOLD = 0.0015   # минимум 0.15% движение за 2 мин
ENTRY_WINDOW_MIN   = 20       # минимум секунд до закрытия
ENTRY_WINDOW_MAX   = 90       # максимум секунд до закрытия
MIN_GAP_FROM_TARGET = 2.0     # минимум $2 разрыв между ценой и таргетом
MIN_VOLUME_USD      = 500     # минимум объём рынка

# Риск-менеджмент
KELLY_FRACTION   = 0.25       # 25% от Kelly
MAX_BET_PCT      = 0.05       # max 5% банкролла
MIN_BET_USD      = 1.0        # минимальная ставка $1
DAILY_LOSS_LIMIT = 0.20       # стоп при -20% за день

# Polymarket API
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
CHAIN_ID  = 137               # Polygon
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_ADDRESS = os.getenv("POLY_ADDRESS", "")
