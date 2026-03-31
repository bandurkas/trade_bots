
import asyncio
import httpx

async def get_fredbagus():
    async with httpx.AsyncClient() as client:
        # Searching for profile by username
        url = "https://gamma-api.polymarket.com/profiles?username=fredbagus"
        resp = await client.get(url)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            profiles = resp.json()
            if profiles:
                # Gamma returns a list of profiles
                for p in profiles:
                    print(f"Username: {p.get('displayName')}")
                    print(f"Address (Owner?): {p.get('address')}")
                    print(f"Proxy Address: {p.get('proxyAddress')}")
            else:
                print("No profiles found for username 'fredbagus'")

if __name__ == "__main__":
    asyncio.run(get_fredbagus())
