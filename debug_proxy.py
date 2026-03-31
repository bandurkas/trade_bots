
import os
import asyncio
import httpx
from dotenv import load_dotenv
from eth_account import Account

load_dotenv()

async def debug_proxy():
    owner = "0x3f44313510cbe1929aef919e0af3e85e5885ac2d"
    print(f"Checking Proxy for Owner: {owner}")
    
    # Try Gamma API to find proxy
    async with httpx.AsyncClient() as client:
        # endpoint: https://gamma-api.polymarket.com/profiles?address=0x...
        resp = await client.get(f"https://gamma-api.polymarket.com/profiles?address={owner}")
        print(f"Gamma Profile API Response: {resp.status_code}")
        if resp.status_code == 200:
            print(f"Body: {resp.json()}")
            
        # Try to find proxy address
        # Another endpoint: https://clob.polymarket.com/proxy?address=0x...
        resp_proxy = await client.get(f"https://clob.polymarket.com/proxy?address={owner}")
        print(f"CLOB Proxy API Response: {resp_proxy.status_code}")
        if resp_proxy.status_code == 200:
            print(f"Body: {resp_proxy.json()}")

if __name__ == "__main__":
    asyncio.run(debug_proxy())
