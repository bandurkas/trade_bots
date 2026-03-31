
import asyncio
import os
import logging
from dotenv import load_dotenv

# We need both ClobClient and market fetching
from trader import PolymarketTrader
from market_scanner import MarketScanner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_trade():
    print("--- Polymarket Test Trade ---")
    
    # 1. Init trader
    trader = PolymarketTrader()
    scanner = MarketScanner()
    
    balance_before = await trader.get_usdc_balance()
    print(f"💰 Balance Before: ${balance_before:.2f}")
    
    # 2. Get an active market to trade on
    print("\n🔍 Scanning for active BTC market...")
    market = await scanner.get_active_btc_market(current_btc_price=66000) # Dummy BTC price just to grab the market
    
    if not market:
        print("❌ No active 5-minute BTC market found. Cannot execute test trade.")
        return
        
    print(f"📈 Selected Market: {market.question}")
    
    # Take UP token
    token_id = market.up_token_id
    print(f"🎯 Position: UP (Token ID: {token_id})")
    print(f"💵 Amount: $1.10")
    
    # 3. Execute Trade
    try:
        print("\n⏳ Executing trade...")
        order_id = await trader.place_market_order(token_id, 1.10, side="BUY")
        
        if order_id:
            print(f"✅ Trade Successful!")
            print(f"🆔 Order ID: {order_id}")
            
            # Fetch order details to get exact price filled
            try:
                # Give it a second to settle
                await asyncio.sleep(2)
                order = trader.client.get_order(order_id)
                # In polymarket, 'price' is the entry price
                entry = order.get("price", "N/A")
                print(f"💰 Entry Price: {entry}")
            except Exception as e:
                print(f"⚠️ Could not fetch order details: {e}")
                
            # 4. Check new balance
            balance_after = await trader.get_usdc_balance()
            print(f"\n💳 Updated Balance: ${balance_after:.2f}")
            print(f"Net change: ${balance_after - balance_before:.2f}")
            
        else:
            print("❌ Trade failed: No order_id returned.")
            
    except Exception as e:
        print(f"❌ Critical error during trade: {e}")
        
    finally:
        await scanner.close()

if __name__ == "__main__":
    asyncio.run(test_trade())
