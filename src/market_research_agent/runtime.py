"""Keep the local development container available for manual commands."""

from __future__ import annotations

import time


def main() -> None:
    print(
        "Market Research Agent development container is ready. "
        "No API server or market provider is configured.",
        flush=True,
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
