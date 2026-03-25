"""StandX MM Bot エントリーポイント."""

import asyncio
import logging
import signal
import sys

from standx_mm_bot.config import Settings
from standx_mm_bot.strategy.maker import MakerStrategy

logger = logging.getLogger("standx_mm_bot")


async def main() -> None:
    """メイン関数."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = Settings()
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        sys.exit(1)

    strategy = MakerStrategy(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(strategy.shutdown())
        )

    logger.info(
        f"StandX MM Bot starting: symbol={config.symbol}, "
        f"chain={config.standx_chain}, "
        f"target={config.target_distance_bps}bps"
    )

    try:
        await strategy.run()
    except Exception as e:
        logger.error(f"Bot error: {e}")
        await strategy.shutdown()
        sys.exit(1)

    logger.info("StandX MM Bot stopped.")
    sys.exit(strategy._exit_code)


if __name__ == "__main__":
    asyncio.run(main())
