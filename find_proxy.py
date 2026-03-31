
import os
import asyncio
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from eth_account import Account

load_dotenv()

async def find_proxy():
    key = os.getenv("POLY_PRIVATE_KEY")
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    
    owner_addr = Account.from_key(key).address
    print(f"Owner Address: {owner_addr}")

    client = ClobClient(
        "https://clob.polymarket.com",
        key=key,
        chain_id=137
    )
    client.set_api_creds(ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase
    ))

    print("\nAttempting to find proxy address...")
    
    # Method 1: Check if the SDK has a way to get it
    try:
        if hasattr(client, 'get_proxy_address'):
            proxy = client.get_proxy_address()
            print(f"SDK get_proxy_address(): {proxy}")
    except Exception as e:
        print(f"SDK method failed: {e}")

    # Method 2: Check standard CLOB API endpoint for proxy
    try:
        import httpx
        headers = {
            "x-api-key": api_key,
            "x-poly-api-secret": api_secret,
            "x-poly-api-passphrase": api_passphrase
        }
        async with httpx.AsyncClient() as h:
            # TRY TO GET ACCOUNT INFO
            # We don't have a direct endpoint documented but we can try common ones
            resp = await h.get(f"https://clob.polymarket.com/proxy?address={owner_addr}")
            if resp.status_code == 200:
                print(f"CLOB /proxy response: {resp.json()}")
    except Exception as e:
        print(f"CLOB API check failed: {e}")

    # Method 3: Try to find by placing a dummy order with a very low price
    # Sometimes the error message contains hints or we can see logs
    print("\nCheck finished. If no proxy found, we need to ask the user.")

if __name__ == "__main__":
    asyncio.run(find_proxy())
