"""Quick start: who am I, search, open a model card, check the quota.

Run with a token from https://polyzon.org/account/ (section «API»):

    POLYZON_API_KEY=pzk_... python examples/quickstart.py
"""

from polyzon import NotFoundError, Polyzon


def main() -> None:
    with Polyzon() as client:  # reads POLYZON_API_KEY
        me = client.me()
        print(f"Token {me.token.name!r} of {me.account.display_name or me.account.id}")

        page = client.search("дракон", free=True, formats=["stl"], size=5)
        print(f"{page.total} results for {page.query!r}")
        for item in page.items:
            print(f"- [{item.source}] {item.title} {item.url}")

        if page.items:
            try:
                model = client.get_model(page.items[0].source, page.items[0].id, lang="en")
            except NotFoundError:
                print("The model disappeared meanwhile")
            else:
                print(f"{model.title}: {len(model.images)} images, license {model.license}")

        limits = client.limits()
        print(f"Daily quota left: {limits.daily.remaining}/{limits.daily.limit}")


if __name__ == "__main__":
    main()
