
import os
import httpx
from eth_account import Account

# Polygon USDC (PoS) address
USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# Polymarket Proxy address we found?
# 0x3f44313510cbE1929aeF919E0AF3e85E5885ac2D
ADDRESS = "0x3f44313510cbE1929aeF919E0AF3e85E5885ac2D"

def check_balance():
    # Calling Polygonscan API or direct RPC
    # We can use public RPC: https://polygon-rpc.com
    # Need to call balanceOf. 
    # balanceOf(address) selector: 0x70a08231
    # pad address: 000000000000000000000000 + address_no_0x
    
    data = "0x70a08231" + ADDRESS[2:].lower().zfill(64)
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [
            {"to": USDC_POLYGON, "data": data},
            "latest"
        ],
        "id": 1
    }
    
    # We'll use a public RPC
    rpc_urls = [
        "https://polygon-rpc.com",
        "https://rpc-mainnet.maticvigil.com",
        "https://matic-mainnet.chainstacklabs.com"
    ]
    
    for url in rpc_urls:
        try:
            print(f"Checking {url}...")
            resp = httpx.post(url, json=payload, timeout=5.0)
            if resp.status_code == 200:
                result = resp.json().get("result")
                if result and result != "0x":
                    val = int(result, 16)
                    print(f"USDC Balance for {ADDRESS}: {val / 10**6:.6f}")
                    return
        except Exception as e:
            print(f"Failed to check {url}: {e}")

if __name__ == "__main__":
    check_balance()
