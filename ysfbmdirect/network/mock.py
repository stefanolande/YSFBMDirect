"""Mock network transport for testing.

Uses in-memory queues instead of real sockets, allowing full control
over network behavior in tests.
"""

import asyncio

from .transport import Address


class MockTransport:
    """Mock network transport using asyncio queues.

    This implementation stores sent packets in lists and uses queues
    for simulating received packets. Perfect for unit testing.

    Example:
        transport = MockTransport()

        # Simulate receiving a packet from BM
        await transport.bm_receive_queue.put(b"YSFP...")

        # Send packet and verify
        await transport.send_to_ysf(b"YSFD...", ("192.168.1.1", 12345))
        assert b"YSFD..." in transport.ysf_sent_packets
    """

    def __init__(self):
        """Initialize mock transport."""
        # Queues for simulating received data
        self.bm_receive_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.ysf_receive_queue: asyncio.Queue[tuple[bytes, Address]] = asyncio.Queue()

        # Lists tracking sent packets
        self.bm_sent_packets: list[bytes] = []
        self.ysf_sent_packets: list[tuple[bytes, Address]] = []

        # Track if closed
        self._closed = False

    async def send_to_bm(self, data: bytes) -> None:
        """Record packet sent to BM."""
        if self._closed:
            raise OSError("Transport is closed")

        self.bm_sent_packets.append(data)

    async def send_to_ysf(self, data: bytes, address: Address) -> None:
        """Record packet sent to YSF client."""
        if self._closed:
            raise OSError("Transport is closed")

        self.ysf_sent_packets.append((data, address))

    async def receive_from_bm(self) -> bytes:
        """Receive simulated packet from BM."""
        if self._closed:
            raise OSError("Transport is closed")

        return await self.bm_receive_queue.get()

    async def receive_from_ysf(self) -> tuple[bytes, Address]:
        """Receive simulated packet from YSF client."""
        if self._closed:
            raise OSError("Transport is closed")

        return await self.ysf_receive_queue.get()

    async def close(self) -> None:
        """Mark transport as closed."""
        self._closed = True

    # Helper methods for testing

    def clear_sent_packets(self) -> None:
        """Clear all sent packet history."""
        self.bm_sent_packets.clear()
        self.ysf_sent_packets.clear()

    def get_bm_sent_count(self) -> int:
        """Get number of packets sent to BM."""
        return len(self.bm_sent_packets)

    def get_ysf_sent_count(self) -> int:
        """Get number of packets sent to YSF."""
        return len(self.ysf_sent_packets)

    def find_bm_packet_by_prefix(self, prefix: bytes) -> bytes | None:
        """Find first BM packet starting with prefix."""
        for packet in self.bm_sent_packets:
            if packet.startswith(prefix):
                return packet
        return None

    def find_ysf_packet_by_prefix(self, prefix: bytes) -> tuple[bytes, Address] | None:
        """Find first YSF packet starting with prefix."""
        for packet, addr in self.ysf_sent_packets:
            if packet.startswith(prefix):
                return (packet, addr)
        return None

    async def simulate_bm_packet(self, data: bytes) -> None:
        """Simulate receiving a packet from BM.

        Helper method for tests to inject packets.
        """
        await self.bm_receive_queue.put(data)

    async def simulate_ysf_packet(self, data: bytes, address: Address) -> None:
        """Simulate receiving a packet from YSF client.

        Helper method for tests to inject packets.
        """
        await self.ysf_receive_queue.put((data, address))
