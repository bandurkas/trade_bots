
import httpx
import asyncio

ADDRESS = "0x3f44313510cbE1929aeF919e0AF3e85E5885ac2D"

async def check_onchain_history():
    # Public RPC
    rpc_url = "https://polygon-rpc.com"
    print(f"Checking transaction count for {ADDRESS}...")
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionCount",
        "params": [ADDRESS, "latest"],
        "id": 1
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(rpc_url, json=payload)
            if resp.status_code == 200:
                count = int(resp.json().get("result", "0x0"), 16)
                print(f"Transaction count (nonce): {count}")
                if count == 0:
                    print("Address 0x3f44... has NO transactions in Polygon. It's likely JUST the owner.")
                    print("The funds must be in a different address (the Proxy).")
        except Exception as e:
            print(f"Failed to check on-chain: {e}")

if __name__ == "__main__":
    asyncio.run(check_onchain_history())
