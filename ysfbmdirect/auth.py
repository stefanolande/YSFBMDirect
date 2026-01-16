"""Authentication handler for Brandmeister login.

Handles the challenge-response authentication flow with BM servers.
"""

import asyncio
import logging
from hashlib import sha256

from .network import NetworkTransport
from .protocol import (
    PacketParser,
    YSFAckPacket,
    YSFChallengePacket,
    YSFLoginPacket,
    YSFNakPacket,
    YSFOptionsPacket,
)

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class AuthenticationHandler:
    """Handles Brandmeister authentication flow.

    Authentication flow:
    1. Send YSFL login packet with callsign
    2. Wait for YSFACK with salt (timeout: 10s)
    3. Compute SHA256(salt + password)
    4. Send YSFK challenge response with hash
    5. Send YSFO to set initial TG
    """

    def __init__(
        self,
        transport: NetworkTransport,
        parser: PacketParser,
        callsign: str,
        password: str,
        timeout: float = 10.0
    ):
        """Initialize authentication handler.

        Args:
            transport: Network transport for sending/receiving
            parser: Packet parser
            callsign: Station callsign
            password: BM password
            timeout: Seconds to wait for auth responses
        """
        self.transport = transport
        self.parser = parser
        self.callsign = callsign
        self.password = password
        self.timeout = timeout

    async def authenticate_and_set_tg(self, target_tg: int) -> None:
        """Perform full authentication and set initial TG.

        Args:
            target_tg: Initial talk group to join

        Raises:
            AuthenticationError: If authentication fails
            asyncio.TimeoutError: If timeout exceeded
        """
        logger.info(f"Authenticating to BM as {self.callsign}")

        # Step 1: Send login request
        login_packet = YSFLoginPacket.create(self.callsign)
        await self.transport.send_to_bm(login_packet.to_bytes())
        logger.debug("Sent login request")

        # Step 2: Wait for ACK with salt
        salt = await self._wait_for_salt()
        logger.debug(f"Received salt: {salt.hex()}")

        # Step 3: Compute and send challenge response
        challenge_hash = self._compute_challenge_hash(salt)
        challenge_packet = YSFChallengePacket.create(self.callsign, challenge_hash)
        await self.transport.send_to_bm(challenge_packet.to_bytes())
        logger.debug("Sent challenge response")

        # Step 4: Set target TG
        await self.set_tg(target_tg)
        logger.info(f"Successfully authenticated and joined TG {target_tg}")

    async def set_tg(self, target_tg: int) -> None:
        """Send TG change command.

        Args:
            target_tg: Target talk group
        """
        options_packet = YSFOptionsPacket.create(
            self.callsign,
            f"group={target_tg}"
        )
        await self.transport.send_to_bm(options_packet.to_bytes())
        logger.info(f"Changed to TG {target_tg}")

    async def send_logout(self) -> None:
        """Send logout message to BM.

        Sends logout packet multiple times for reliability.
        """
        from .protocol import YSFDisconnectPacket

        logout_packet = YSFDisconnectPacket.create(self.callsign)

        # Send 2 times for reliability
        for i in range(2):
            await self.transport.send_to_bm(logout_packet.to_bytes())
            if i < 1:
                await asyncio.sleep(0.1)

        logger.info("Sent logout message")

    async def _wait_for_salt(self) -> bytes:
        """Wait for YSFACK packet with salt.

        Returns:
            4-byte salt

        Raises:
            AuthenticationError: If NAK received or invalid ACK
            asyncio.TimeoutError: If timeout exceeded
        """
        try:
            # Wait for response with timeout
            data = await asyncio.wait_for(
                self.transport.receive_from_bm(),
                timeout=self.timeout
            )

            packet = self.parser.parse(data)

            # Check for NAK (auth rejected)
            if isinstance(packet, YSFNakPacket):
                raise AuthenticationError("Authentication rejected by server (NAK)")

            # Check for ACK with salt
            if isinstance(packet, YSFAckPacket):
                if not packet.has_salt:
                    raise AuthenticationError(
                        "Received ACK without salt (unexpected format)"
                    )
                return packet.salt  # type: ignore

            # Unexpected packet type
            raise AuthenticationError(
                f"Expected YSFACK with salt, got {packet.packet_type.name}"
            )

        except asyncio.TimeoutError:
            raise AuthenticationError(
                f"Timeout waiting for authentication response (>{self.timeout}s)"
            )

    def _compute_challenge_hash(self, salt: bytes) -> bytes:
        """Compute challenge response hash.

        Hash is SHA256(salt + password)

        Args:
            salt: 4-byte salt from server

        Returns:
            32-byte SHA256 hash
        """
        secret = salt + self.password.encode('utf-8')
        return sha256(secret).digest()
