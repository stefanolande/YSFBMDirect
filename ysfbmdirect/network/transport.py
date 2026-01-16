"""Network transport protocol interface.

Defines the contract for network I/O, allowing dependency injection of
real or mock implementations for testing.
"""

from typing import Protocol

# Type alias for network address
Address = tuple[str, int]


class NetworkTransport(Protocol):
    """Protocol defining network transport interface.

    This protocol allows any object that implements these methods to be
    used as a network transport, enabling dependency injection and testing.

    Real implementation uses UDP sockets.
    Test implementation uses in-memory queues.
    """

    async def send_to_bm(self, data: bytes) -> None:
        """Send packet to Brandmeister server.

        Args:
            data: Raw packet bytes to send

        Raises:
            IOError: If send fails
        """
        ...

    async def send_to_ysf(self, data: bytes, address: Address) -> None:
        """Send packet to YSF client.

        Args:
            data: Raw packet bytes to send
            address: Client address (host, port)

        Raises:
            IOError: If send fails
        """
        ...

    async def receive_from_bm(self) -> bytes:
        """Receive packet from Brandmeister server.

        Returns:
            Raw packet bytes

        Raises:
            IOError: If receive fails
            asyncio.CancelledError: If task is cancelled
        """
        ...

    async def receive_from_ysf(self) -> tuple[bytes, Address]:
        """Receive packet from YSF client.

        Returns:
            Tuple of (packet bytes, client address)

        Raises:
            IOError: If receive fails
            asyncio.CancelledError: If task is cancelled
        """
        ...

    async def close(self) -> None:
        """Close transport and release resources.

        Should be idempotent - calling multiple times has no effect.
        """
        ...
