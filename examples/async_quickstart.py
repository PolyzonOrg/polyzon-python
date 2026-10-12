"""The asyncio client: concurrent calls with ``asyncio.gather`` and ``async for``."""

import asyncio

from polyzon import AsyncPolyzon


async def main() -> None:
    async with AsyncPolyzon() as client:
        me, limits = await asyncio.gather(client.me(), client.limits())
        print(f"{me.token.name}: {limits.daily.remaining} requests left today")

        count = 0
        async for item in client.search_all("chess", size=20, lang="en"):
            print(item.title)
            count += 1
            if count == 20:  # one page, one daily-quota request
                break


if __name__ == "__main__":
    asyncio.run(main())
