
import os
import asyncio
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from eth_account import Account

load_dotenv()

async def debug():
    key = os.getenv("POLY_PRIVATE_KEY")
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    funder = os.getenv("FUNDER_ADDRESS")
    sig_type = int(os.getenv("SIGNATURE_TYPE", "0"))

    print(f"--- Debug Info ---")
    print(f"Funder Address from .env: {funder}")
    print(f"Signature Type from .env: {sig_type}")
    
    if key:
        acc = Account.from_key(key)
        print(f"Derived Address from PK: {acc.address}")
        
    client = ClobClient(
        "https://clob.polymarket.com",
        key=key,
        chain_id=137,
        signature_type=sig_type,
        funder=funder if sig_type == 1 else None
    )
    
    client.set_api_creds(ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase
    ))
    
    try:
        print("\nChecking COLLATERAL (USDC) balance via CLOB SDK...")
        resp = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"Response: {resp}")
        
        print("\nChecking CONDITIONAL asset type...")
        resp_cond = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL))
        print(f"Response: {resp_cond}")

        print("\nFetching full account info...")
        # Check if we can get anything else
        # try to use a dummy order to see the error again in real-time
    except Exception as e:
        print(f"Error during SDK calls: {e}")

if __name__ == "__main__":
    asyncio.run(debug())
