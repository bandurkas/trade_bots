
import os
import asyncio
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from eth_account import Account

load_dotenv()

async def debug_account():
    key = os.getenv("POLY_PRIVATE_KEY")
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    
    print("Initializing CLOB client without API credentials first...")
    client = ClobClient(
        "https://clob.polymarket.com",
        key=key,
        chain_id=137,
        signature_type=0
    )
    
    # Try public balance check
    addr = Account.from_key(key).address
    print(f"Address: {addr}")
    
    try:
        print("\nChecking balance allowance (Public call via address)...")
        # The balance_allowance can be queried by address
        resp = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"Address-based Response: {resp}")
        
    except Exception as e:
        print(f"Public check failed: {e}")

    print("\nSetting API Credentials...")
    client.set_api_creds(ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase
    ))
    
    try:
        print("\nFetching Account via SDK (authenticated)...")
        # client.get_account() ?
        # In reality, ClobClient might have a method to get address
        # Check if we can list orders
        orders = client.get_orders()
        print(f"Orders count: {len(orders) if orders else 0}")
    except Exception as e:
        print(f"Authenticated check failed: {e}")

if __name__ == "__main__":
    asyncio.run(debug_account())
