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
        
        if not all([self.key, self.api_key]):
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
        
        # Устанавливаем L2 креды (если есть secret/passphrase, иначе auth через EIP-712)
        if self.api_secret and self.api_passphrase:
            self.client.set_api_creds(ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase
            ))
        else:
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            logger.info(f"API creds derived from private key")
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
    ) -> Optional[dict]:
        """
        Размещает рыночный ордер. Возвращает dict с order_id, actual_price, actual_amount
        или None при ошибке.
        """
        if not self.client:
            logger.error("Трейдер не инициализирован")
            return None

        try:
            logger.info(f"Размещение ордера: {side} {token_id} на ${amount_usd:.2f}")
            
            price = 0.99 if side == "BUY" else 0.01
            amount_usd = max(amount_usd, 1.01)
            size = round(amount_usd / price, 2) if price > 0 else 0
            
            resp = self.client.create_and_post_order(OrderArgs(
                price=price,
                size=size,
                side=side,
                token_id=token_id
            ))
            
            logger.info(f"Ответ API: {resp}")

            if resp and resp.get("success"):
                order_id = resp.get("orderID", "")

                # Реальная цена заполнения из takingAmount / makingAmount
                # Polymarket возвращает суммы в строках (единицы = wei, 6 знаков для USDC/shares)
                actual_amount = amount_usd  # fallback
                actual_price  = price       # fallback
                try:
                    taking = float(resp.get("takingAmount", 0))
                    making = float(resp.get("makingAmount", 0))
                    if taking > 0 and making > 0:
                        # takingAmount = шары получено, makingAmount = USDC заплачено
                        shares        = taking
                        actual_amount = making
                        actual_price  = round(actual_amount / shares, 4)
                except Exception:
                    pass

                logger.info(
                    f"Ордер размещён! ID: {order_id} | "
                    f"заплачено: ${actual_amount:.4f} | "
                    f"цена/шара: ${actual_price:.4f}"
                )
                return {
                    "order_id":     order_id,
                    "actual_amount": actual_amount,
                    "actual_price":  actual_price,
                }
            else:
                logger.error(f"Ошибка размещения ордера: {resp}")
                return None
                
        except Exception as e:
            logger.exception(f"Критическая ошибка при размещении ордера: {e}")
            return None
