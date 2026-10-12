"""Pagination: walk results lazily and stop early.

Every page costs one request of the daily quota (200 per day by default), so take
only what you need: ``islice`` stops the iterator after 120 models (3 pages of 50).
"""

from itertools import islice

from polyzon import Polyzon


def main() -> None:
    with Polyzon() as client:
        results = client.search_all("vase", sort="popular", size=50, license=["cc0", "cc-by"])
        for number, item in enumerate(islice(results, 120), start=1):
            print(f"{number:3}. {item.title} ({item.stats.likes or 0} likes)")


if __name__ == "__main__":
    main()
