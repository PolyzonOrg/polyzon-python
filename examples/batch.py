"""Fetch many model cards in parallel with bounded concurrency.

``get_models`` never raises for a single failed model: each result holds either the
model or the error. Each model costs one request of the daily quota, and the account
may send at most 20 requests per minute (rate-limited calls are retried for you).
"""

from polyzon import GoneError, ModelRef, Polyzon


def main() -> None:
    refs = [
        ModelRef("thingiverse", "tv-1234567"),
        ("printables", "pr-123456"),  # plain tuples work too
    ]
    with Polyzon() as client:
        for result in client.get_models(refs, concurrency=4):
            if result.model is not None:
                print(f"{result.ref.source}/{result.ref.id}: {result.model.title}")
            elif isinstance(result.error, GoneError):
                print(f"{result.ref.id} was removed by its right holder: delete it on your side")
            else:
                print(f"{result.ref.id} failed: {result.error}")


if __name__ == "__main__":
    main()
