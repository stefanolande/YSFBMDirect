"""UDP network transport implementation.

Real network transport using UDP sockets for production use.
"""

import asyncio
import logging

from .transport import Address

logger = logging.getLogger(__name__)


class UDPTransport:
    """UDP-based network transport for production use.

    Manages two UDP sockets:
    - BM socket: Connected to Brandmeister server
    - YSF socket: Listening for YSF client connections

    Uses asyncio's datagram protocol for async I/O.
    """

    def __init__(
        self,
        bm_host: str,
        bm_port: int,
        ysf_port: int,
        receive_buffer_size: int = 1024
    ):
        """Initialize UDP transport.

        Args:
            bm_host: Brandmeister server hostname/IP
            bm_port: Brandmeister server port
            ysf_port: Local port to listen for YSF client
            receive_buffer_size: Max bytes to receive per packet
        """
        self.bm_host = bm_host
        self.bm_port = bm_port
        self.ysf_port = ysf_port
        self.receive_buffer_size = receive_buffer_size

        # Socket objects (initialized in start())
        self._bm_transport: asyncio.DatagramTransport | None = None
        self._bm_protocol: _UDPProtocol | None = None
        self._ysf_transport: asyncio.DatagramTransport | None = None
        self._ysf_protocol: _UDPProtocol | None = None

        self._closed = False

    async def start(self) -> None:
        """Initialize and bind sockets.

        Must be called before using the transport.

        Raises:
            IOError: If socket creation fails
        """
        loop = asyncio.get_running_loop()

        # Create BM socket (connected)
        try:
            self._bm_transport, self._bm_protocol = await loop.create_datagram_endpoint(
                lambda: _UDPProtocol(buffer_size=self.receive_buffer_size),
                remote_addr=(self.bm_host, self.bm_port)
            )
            logger.info(f"Connected to Brandmeister at {self.bm_host}:{self.bm_port}")
        except Exception as e:
            raise OSError(f"Failed to connect to BM server: {e}") from e

        # Create YSF socket (listening)
        try:
            self._ysf_transport, self._ysf_protocol = await loop.create_datagram_endpoint(
                lambda: _UDPProtocol(buffer_size=self.receive_buffer_size),
                local_addr=("0.0.0.0", self.ysf_port)
            )
            logger.info(f"Listening for YSF client on port {self.ysf_port}")
        except Exception as e:
            # Clean up BM socket if YSF socket fails
            if self._bm_transport:
                self._bm_transport.close()
            raise OSError(f"Failed to bind YSF port: {e}") from e

    async def send_to_bm(self, data: bytes) -> None:
        """Send packet to Brandmeister server."""
        if self._closed or not self._bm_transport:
            raise OSError("Transport is closed or not started")

        self._bm_transport.sendto(data)
        logger.debug(f"Sent {len(data)} bytes to BM")

    async def send_to_ysf(self, data: bytes, address: Address) -> None:
        """Send packet to YSF client."""
        if self._closed or not self._ysf_transport:
            raise OSError("Transport is closed or not started")

        self._ysf_transport.sendto(data, address)
        logger.debug(f"Sent {len(data)} bytes to YSF client at {address}")

    async def receive_from_bm(self) -> bytes:
        """Receive packet from Brandmeister server."""
        if self._closed or not self._bm_protocol:
            raise OSError("Transport is closed or not started")

        return await self._bm_protocol.receive()

    async def receive_from_ysf(self) -> tuple[bytes, Address]:
        """Receive packet from YSF client."""
        if self._closed or not self._ysf_protocol:
            raise OSError("Transport is closed or not started")

        data = await self._ysf_protocol.receive()
        address = self._ysf_protocol.get_last_sender()

        if address is None:
            raise OSError("No sender address available")

        return data, address

    async def close(self) -> None:
        """Close transport and release resources."""
        if self._closed:
            return

        self._closed = True

        if self._bm_transport:
            self._bm_transport.close()
            logger.info("Closed BM socket")

        if self._ysf_transport:
            self._ysf_transport.close()
            logger.info("Closed YSF socket")


class _UDPProtocol(asyncio.DatagramProtocol):
    """Internal asyncio protocol for handling UDP datagrams."""

    def __init__(self, buffer_size: int = 1024):
        """Initialize protocol.

        Args:
            buffer_size: Maximum packet size to receive
        """
        self.buffer_size = buffer_size
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._last_sender: Address | None = None

    def datagram_received(self, data: bytes, addr: Address) -> None:
        """Called when a datagram is received.

        Args:
            data: Received data
            addr: Sender address
        """
        self._last_sender = addr

        # Truncate if too large
        if len(data) > self.buffer_size:
            logger.warning(
                f"Received packet larger than buffer "
                f"({len(data)} > {self.buffer_size}), truncating"
            )
            data = data[:self.buffer_size]

        # Add to queue for async retrieval
        self._queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        """Called when a send/receive error occurs.

        Args:
            exc: The exception
        """
        logger.error(f"Protocol error: {exc}")

    async def receive(self) -> bytes:
        """Receive next packet from queue.

        Returns:
            Received packet bytes

        Raises:
            asyncio.CancelledError: If task is cancelled
        """
        return await self._queue.get()

    def get_last_sender(self) -> Address | None:
        """Get address of last packet sender.

        Returns:
            Address tuple or None if no packets received
        """
        return self._last_sender
