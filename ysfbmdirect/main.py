"""Main entry point for YSF-BM Bridge (refactored version)."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from .bridge import YSFBMBridge
from .config_loader import load_config
from .network import UDPTransport
from .webui import WebUIServer

logger = logging.getLogger(__name__)


async def main_async(config_file: str) -> int:
    """Async main function.

    Args:
        config_file: Path to configuration file

    Returns:
        Exit code (0 = success, non-zero = error)
    """
    try:
        # Load configuration
        config = load_config(config_file)
        logger.info(f"Loaded configuration for {config.callsign}")
        logger.info(f"Default TG: {config.default_tg} (DG-ID {config.default_dgid})")
        logger.info(f"DG-ID mappings: {config.dgid_to_tg}")

        # Create transport
        transport = UDPTransport(
            bm_host=config.bm_host,
            bm_port=config.bm_port,
            ysf_port=config.ysf_port
        )

        # Initialize sockets
        await transport.start()

        # Create and run bridge
        bridge = YSFBMBridge(config, transport)

        # Create Web UI if enabled
        webui_server = None
        if config.webui_enabled:
            logger.info(f"Web UI enabled on http://{config.webui_host}:{config.webui_port}")
            webui_server = WebUIServer(
                host=config.webui_host,
                port=config.webui_port,
                config_file=config_file
            )
            webui_server.set_bridge(bridge)

            # Register packet event callback
            def packet_callback(direction: str, protocol: str):
                if direction == "sent":
                    webui_server.record_packet_sent(protocol)
                elif direction == "received":
                    webui_server.record_packet_received(protocol)

            bridge.register_packet_callback(packet_callback)

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()

        def signal_handler():
            logger.info("Received shutdown signal")
            asyncio.create_task(bridge.stop())

        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)

        # Run bridge and web UI concurrently
        logger.info("YSF-BM Bridge started")

        if webui_server:
            # Run both bridge and web UI
            await asyncio.gather(
                bridge.run(),
                webui_server.run(),
            )
        else:
            # Run bridge only
            await bridge.run()

        logger.info("YSF-BM Bridge stopped cleanly")
        return 0

    except FileNotFoundError as e:
        logger.error(f"Configuration file error: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Configuration validation error: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


def main() -> int:
    """Main entry point.

    Returns:
        Exit code
    """
    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python -m ysfbmdirect.main <config_file>")
        print("\nExamples:")
        print("  python -m ysfbmdirect.main config.yaml")
        print("  python -m ysfbmdirect.main YSFBMDirect.conf  # Legacy format")
        return 1

    config_file = sys.argv[1]

    # Check config file exists
    if not Path(config_file).exists():
        print(f"Error: Configuration file not found: {config_file}")
        return 1

    # Setup logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    # Run async main
    try:
        return asyncio.run(main_async(config_file))
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
