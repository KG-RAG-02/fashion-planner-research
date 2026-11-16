"""Keep the local development container available for manual commands."""

from __future__ import annotations

import time


def main() -> None:
    print(
        "Sales Market Review Agent development container is ready. "
        "Start the review API explicitly with market_research_agent.http_server.",
        flush=True,
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
