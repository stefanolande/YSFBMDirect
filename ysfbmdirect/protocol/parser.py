"""YSF Protocol Packet Parser.

Parses raw bytes into strongly-typed Packet objects.
"""


from .packets import (
    Packet,
    PacketType,
    YSFAckPacket,
    YSFChallengePacket,
    YSFDataPacket,
    YSFDisconnectPacket,
    YSFLoginPacket,
    YSFNakPacket,
    YSFOptionsPacket,
    YSFPingPacket,
)


class ParseError(Exception):
    """Raised when packet parsing fails."""

    pass


class PacketParser:
    """Parser for YSF protocol packets.

    Converts raw bytes from the network into strongly-typed Packet objects.
    Validates packet structure and extracts fields.
    """

    # Minimum packet size is 4 bytes (header only)
    MIN_PACKET_SIZE = 4

    def parse(self, data: bytes) -> Packet:
        """Parse raw packet data into a Packet object.

        Args:
            data: Raw packet bytes from network

        Returns:
            Parsed Packet object

        Raises:
            ParseError: If packet is malformed or unrecognized
        """
        if len(data) < self.MIN_PACKET_SIZE:
            raise ParseError(
                f"Packet too short: {len(data)} bytes (minimum {self.MIN_PACKET_SIZE})"
            )

        # Try to identify packet type from header
        packet_type = self._identify_packet_type(data)

        # Dispatch to appropriate parser based on type
        if packet_type == PacketType.PING:
            return self._parse_ping(data)
        elif packet_type == PacketType.DATA:
            return self._parse_data(data)
        elif packet_type == PacketType.DISCONNECT:
            return self._parse_disconnect(data)
        elif packet_type == PacketType.OPTIONS:
            return self._parse_options(data)
        elif packet_type == PacketType.LOGIN:
            return self._parse_login(data)
        elif packet_type == PacketType.CHALLENGE:
            return self._parse_challenge(data)
        elif packet_type == PacketType.ACK:
            return self._parse_ack(data)
        elif packet_type == PacketType.NAK:
            return self._parse_nak(data)
        else:
            raise ParseError(f"Unknown packet type: {data[:4]}")

    def _identify_packet_type(self, data: bytes) -> PacketType:
        """Identify packet type from header bytes.

        Args:
            data: Raw packet data

        Returns:
            PacketType enum value

        Raises:
            ParseError: If header doesn't match any known packet type
        """
        # Check 6-byte headers first (YSFACK, YSFNAK)
        if len(data) >= 6:
            header_6 = data[:6]
            if header_6 == PacketType.ACK.value:
                return PacketType.ACK
            if header_6 == PacketType.NAK.value:
                return PacketType.NAK

        # Check 4-byte headers
        header_4 = data[:4]
        for packet_type in PacketType:
            if packet_type.value == header_4:
                return packet_type

        raise ParseError(f"Unrecognized packet header: {data[:6]}")

    def _parse_ping(self, data: bytes) -> YSFPingPacket:
        """Parse YSFP ping packet."""
        if len(data) < 14:
            raise ParseError(f"YSFP packet too short: {len(data)} bytes (expected 14)")

        callsign = data[4:14].decode('utf-8', errors='replace').strip()

        return YSFPingPacket(
            raw_data=data,
            packet_type=PacketType.PING,
            callsign=callsign
        )

    def _parse_data(self, data: bytes) -> YSFDataPacket:
        """Parse YSFD data packet."""
        if len(data) < 160:
            raise ParseError(f"YSFD packet too short: {len(data)} bytes (expected 160)")

        # Extract header fields
        source = data[4:14].decode('utf-8', errors='replace').strip()
        destination = data[14:24].decode('utf-8', errors='replace').strip()
        target = data[24:34].decode('utf-8', errors='replace').strip()

        # Extract payload (starts at offset 40)
        payload = data[40:160]

        # FICH data is embedded in the payload
        # It needs to be decoded using ysffich module
        fich_data = data[40:] if len(data) >= 40 else None

        return YSFDataPacket(
            raw_data=data,
            packet_type=PacketType.DATA,
            source=source,
            destination=destination,
            target=target,
            payload=payload,
            fich_data=fich_data
        )

    def _parse_disconnect(self, data: bytes) -> YSFDisconnectPacket:
        """Parse YSFU disconnect packet."""
        if len(data) < 14:
            raise ParseError(f"YSFU packet too short: {len(data)} bytes (expected 14)")

        callsign = data[4:14].decode('utf-8', errors='replace').strip()

        return YSFDisconnectPacket(
            raw_data=data,
            packet_type=PacketType.DISCONNECT,
            callsign=callsign
        )

    def _parse_options(self, data: bytes) -> YSFOptionsPacket:
        """Parse YSFO options packet."""
        if len(data) < 14:
            raise ParseError(f"YSFO packet too short: {len(data)} bytes (minimum 14)")

        callsign = data[4:14].decode('utf-8', errors='replace').strip()
        options = data[14:].decode('utf-8', errors='replace').strip()

        return YSFOptionsPacket(
            raw_data=data,
            packet_type=PacketType.OPTIONS,
            callsign=callsign,
            options=options
        )

    def _parse_login(self, data: bytes) -> YSFLoginPacket:
        """Parse YSFL login packet."""
        if len(data) < 14:
            raise ParseError(f"YSFL packet too short: {len(data)} bytes (expected 14)")

        callsign = data[4:14].decode('utf-8', errors='replace').strip()

        return YSFLoginPacket(
            raw_data=data,
            packet_type=PacketType.LOGIN,
            callsign=callsign
        )

    def _parse_challenge(self, data: bytes) -> YSFChallengePacket:
        """Parse YSFK challenge response packet."""
        if len(data) < 46:
            raise ParseError(f"YSFK packet too short: {len(data)} bytes (expected 46)")

        callsign = data[4:14].decode('utf-8', errors='replace').strip()
        challenge_hash = data[14:46]

        if len(challenge_hash) != 32:
            raise ParseError(
                f"Invalid challenge hash length: {len(challenge_hash)} (expected 32)"
            )

        return YSFChallengePacket(
            raw_data=data,
            packet_type=PacketType.CHALLENGE,
            callsign=callsign,
            challenge_hash=challenge_hash
        )

    def _parse_ack(self, data: bytes) -> YSFAckPacket:
        """Parse YSFACK acknowledgment packet.

        Two formats:
        - Short: YSFACK (6 bytes)
        - With salt: YSFACK + padding (10) + salt (4) = 20 bytes
        """
        if len(data) == 6:
            # Short ACK without salt
            return YSFAckPacket(
                raw_data=data,
                packet_type=PacketType.ACK,
                salt=None
            )
        elif len(data) == 20:
            # ACK with salt
            salt = data[16:20]
            return YSFAckPacket(
                raw_data=data,
                packet_type=PacketType.ACK,
                salt=salt
            )
        else:
            raise ParseError(
                f"Invalid YSFACK packet length: {len(data)} (expected 6 or 20)"
            )

    def _parse_nak(self, data: bytes) -> YSFNakPacket:
        """Parse YSFNAK negative acknowledgment packet."""
        if len(data) < 6:
            raise ParseError(f"YSFNAK packet too short: {len(data)} bytes (expected 6)")

        return YSFNakPacket(
            raw_data=data,
            packet_type=PacketType.NAK
        )

    def try_parse(self, data: bytes) -> Packet | None:
        """Attempt to parse packet, returning None on failure.

        Convenience method that catches ParseError and returns None
        instead of raising an exception.

        Args:
            data: Raw packet bytes

        Returns:
            Parsed Packet object or None if parsing failed
        """
        try:
            return self.parse(data)
        except ParseError:
            return None
