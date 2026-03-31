"""
Модуль для автоматического исполнения сделок на Polymarket CLOB.
Использует py-clob-client для EIP-712 подписи и gasless исполнения.
"""
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.constants import POLYGON

load_dotenv()

logger = logging.getLogger(__name__)

class PolymarketTrader:
    def __init__(self):
        self.host = "https://clob.polymarket.com"
        self.key  = os.getenv("POLY_PRIVATE_KEY")
        self.funder = os.getenv("FUNDER_ADDRESS")
        self.chain_id = 137  # Polygon
        
        # L2 Creds
        self.api_key = os.getenv("POLY_API_KEY")
        self.api_secret = os.getenv("POLY_API_SECRET")
        self.api_passphrase = os.getenv("POLY_API_PASSPHRASE")
        
        if not all([self.key, self.api_key, self.api_secret, self.api_passphrase]):
            logger.error("Кредиты Polymarket не полностью настроены в .env!")
            self.client = None
            return

        self.signature_type = int(os.getenv("SIGNATURE_TYPE", "0"))
        
        self.client = ClobClient(
            self.host,
            key=self.key,
            chain_id=self.chain_id,
            signature_type=self.signature_type,
            funder=self.funder if self.signature_type == 1 else None
        )
        
        # Устанавливаем L2 креды
        self.client.set_api_creds(ApiCreds(
            api_key=self.api_key,
            api_secret=self.api_secret,
            api_passphrase=self.api_passphrase
        ))
        logger.info("PolymarketTrader инициализирован (gasless mode)")

    async def get_usdc_balance(self) -> float:
        """Возвращает баланс USDC на счету Polymarket."""
        if not self.client:
            return 0.0
        try:
            # asset_type=0 обычно означает Collateral (USDC)
            # В SDK это синхронный метод, но мы можем обернуть его или оставить так
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            resp = self.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            if resp and "balance" in resp:
                # Баланс возвращается в вей-формате (6 знаков для USDC)
                raw_balance = float(resp["balance"])
                return raw_balance / 1_000_000.0
            return 0.0
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return 0.0

    async def place_market_order(
        self, 
        token_id: str, 
        amount_usd: float, 
        side: str = "BUY"
    ) -> Optional[str]:
        """
        Размещает рыночный ордер (на самом деле Limit FOK/IOC для CLOB).
        amount_usd: сколько USDC потратить.
        side: 'BUY' или 'SELL'.
        """
        if not self.client:
            logger.error("Трейдер не инициализирован")
            return None

        try:
            logger.info(f"Размещение ордера: {side} {token_id} на ${amount_usd:.2f}")
            
            # The precision must securely be >= 1.00 after multiplication.
            price = 0.99 if side == "BUY" else 0.01
            
            # Размер в долях (shares): to deal with floating point we can floor or round, 
            # but to ensure we pass the $1 limit we can safely add a tiny buffer
            amount_usd = max(amount_usd, 1.01)
            size = round(amount_usd / price, 2) if price > 0 else 0
            
            # SDK: create_and_post_order
            resp = self.client.create_and_post_order(OrderArgs(
                price=price,
                size=size,
                side=side,
                token_id=token_id
            ))
            
            if resp and resp.get("success"):
                order_id = resp.get("orderID")
                logger.info(f"Ордер успешно размещен! ID: {order_id}")
                return order_id
            else:
                logger.error(f"Ошибка размещения ордера: {resp}")
                return None
                
        except Exception as e:
            logger.exception(f"Критическая ошибка при размещении ордера: {e}")
            return None
