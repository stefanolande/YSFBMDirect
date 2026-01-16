"""YSF Protocol Packet Definitions.

This module defines strongly-typed packet classes for the YSF protocol,
replacing the old string-based packet detection approach.
"""

from dataclasses import dataclass
from enum import Enum


class PacketType(Enum):
    """YSF packet types based on 4-byte header."""

    PING = b"YSFP"        # Ping/keepalive
    DATA = b"YSFD"        # Voice/data transmission
    DISCONNECT = b"YSFU"  # Disconnect notification
    OPTIONS = b"YSFO"     # Options/commands (e.g., TG change)
    LOGIN = b"YSFL"       # Login request
    CHALLENGE = b"YSFK"   # Challenge response
    ACK = b"YSFACK"       # Acknowledgment
    NAK = b"YSFNAK"       # Negative acknowledgment


@dataclass(frozen=True)
class Packet:
    """Base class for all YSF packets."""

    raw_data: bytes
    packet_type: PacketType

    def to_bytes(self) -> bytes:
        """Convert packet back to wire format."""
        return self.raw_data


@dataclass(frozen=True)
class YSFPingPacket(Packet):
    """Ping/keepalive packet (YSFP).

    Format: YSFP + callsign (10 bytes padded)
    Total: 14 bytes
    """

    callsign: str

    @classmethod
    def create(cls, callsign: str) -> "YSFPingPacket":
        """Create a new ping packet."""
        padded_callsign = callsign.ljust(10).encode('utf-8')[:10]
        raw_data = PacketType.PING.value + padded_callsign
        return cls(
            raw_data=raw_data,
            packet_type=PacketType.PING,
            callsign=callsign
        )


@dataclass(frozen=True)
class YSFDataPacket(Packet):
    """Voice/data packet (YSFD).

    Format: YSFD + header (36 bytes) + payload (120 bytes)
    Total: 160 bytes

    Header structure:
    - Source callsign (10 bytes)
    - Destination callsign (10 bytes)
    - Target callsign (10 bytes)
    - Additional fields (6 bytes)

    Payload contains FICH (40 bytes offset) + voice/data
    """

    source: str
    destination: str
    target: str
    payload: bytes
    fich_data: bytes | None  # Frame information (40-byte offset in payload)

    @property
    def dg_id(self) -> int | None:
        """Extract DG-ID from FICH if available."""
        if not self.fich_data:
            return None
        # DG-ID is extracted by ysffich.getSQ() from decoded FICH
        # This will be implemented in packet parser
        return None

    @classmethod
    def create(
        cls,
        source: str,
        destination: str,
        target: str,
        payload: bytes,
        fich_data: bytes | None = None
    ) -> "YSFDataPacket":
        """Create a new data packet."""
        # Build header
        src = source.ljust(10).encode('utf-8')[:10]
        dst = destination.ljust(10).encode('utf-8')[:10]
        tgt = target.ljust(10).encode('utf-8')[:10]

        header = PacketType.DATA.value + src + dst + tgt

        # Pad payload to 120 bytes if needed
        payload_padded = payload + b'\x00' * (120 - len(payload))
        payload_padded = payload_padded[:120]

        raw_data = header + b'\x00' * 6 + payload_padded

        return cls(
            raw_data=raw_data,
            packet_type=PacketType.DATA,
            source=source,
            destination=destination,
            target=target,
            payload=payload_padded,
            fich_data=fich_data
        )


@dataclass(frozen=True)
class YSFDisconnectPacket(Packet):
    """Disconnect notification (YSFU).

    Format: YSFU + callsign (10 bytes)
    Total: 14 bytes
    """

    callsign: str

    @classmethod
    def create(cls, callsign: str) -> "YSFDisconnectPacket":
        """Create a new disconnect packet."""
        padded_callsign = callsign.ljust(10).encode('utf-8')[:10]
        raw_data = PacketType.DISCONNECT.value + padded_callsign
        return cls(
            raw_data=raw_data,
            packet_type=PacketType.DISCONNECT,
            callsign=callsign
        )


@dataclass(frozen=True)
class YSFOptionsPacket(Packet):
    """Options/command packet (YSFO).

    Used for TG changes and other commands.
    Format: YSFO + callsign (10 bytes) + options (variable)

    Example: YSFO + callsign + "group=2225"
    """

    callsign: str
    options: str

    @property
    def target_tg(self) -> int | None:
        """Extract target TG from options if this is a group command."""
        if self.options.startswith("group="):
            try:
                return int(self.options[6:])
            except ValueError:
                return None
        return None

    @classmethod
    def create(cls, callsign: str, options: str) -> "YSFOptionsPacket":
        """Create a new options packet."""
        padded_callsign = callsign.ljust(10).encode('utf-8')[:10]
        raw_data = PacketType.OPTIONS.value + padded_callsign + options.encode('utf-8')
        return cls(
            raw_data=raw_data,
            packet_type=PacketType.OPTIONS,
            callsign=callsign,
            options=options
        )


@dataclass(frozen=True)
class YSFLoginPacket(Packet):
    """Login request (YSFL).

    Format: YSFL + callsign (10 bytes)
    Total: 14 bytes
    """

    callsign: str

    @classmethod
    def create(cls, callsign: str) -> "YSFLoginPacket":
        """Create a new login packet."""
        padded_callsign = callsign.ljust(10).encode('utf-8')[:10]
        raw_data = PacketType.LOGIN.value + padded_callsign
        return cls(
            raw_data=raw_data,
            packet_type=PacketType.LOGIN,
            callsign=callsign
        )


@dataclass(frozen=True)
class YSFChallengePacket(Packet):
    """Challenge response (YSFK).

    Format: YSFK + callsign (10 bytes) + SHA256 hash (32 bytes)
    Total: 46 bytes

    Hash is computed as: SHA256(salt + password)
    """

    callsign: str
    challenge_hash: bytes

    @classmethod
    def create(cls, callsign: str, challenge_hash: bytes) -> "YSFChallengePacket":
        """Create a new challenge response packet."""
        if len(challenge_hash) != 32:
            raise ValueError("Challenge hash must be 32 bytes (SHA256)")

        padded_callsign = callsign.ljust(10).encode('utf-8')[:10]
        raw_data = PacketType.CHALLENGE.value + padded_callsign + challenge_hash
        return cls(
            raw_data=raw_data,
            packet_type=PacketType.CHALLENGE,
            callsign=callsign,
            challenge_hash=challenge_hash
        )


@dataclass(frozen=True)
class YSFAckPacket(Packet):
    """Acknowledgment packet (YSFACK).

    Two formats:
    1. Short ACK: YSFACK (6 bytes)
    2. ACK with salt: YSFACK + padding (10 bytes) + salt (4 bytes) = 20 bytes

    The salt is used for authentication challenge-response.
    """

    salt: bytes | None  # 4 bytes if present

    @property
    def has_salt(self) -> bool:
        """Check if this ACK contains a salt for authentication."""
        return self.salt is not None


@dataclass(frozen=True)
class YSFNakPacket(Packet):
    """Negative acknowledgment (YSFNAK).

    Indicates an error or rejected request.
    Format: YSFNAK (6 bytes)
    """

    pass
