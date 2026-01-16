"""Tests for packet definitions and parsing."""

import pytest

from ysfbmdirect.protocol import (
    PacketParser,
    PacketType,
    ParseError,
    YSFAckPacket,
    YSFChallengePacket,
    YSFDataPacket,
    YSFDisconnectPacket,
    YSFLoginPacket,
    YSFNakPacket,
    YSFOptionsPacket,
    YSFPingPacket,
)


class TestPacketCreation:
    """Test creating packets programmatically."""

    def test_create_ping_packet(self):
        """Test creating a YSFP ping packet."""
        packet = YSFPingPacket.create("IS0EIR")

        assert packet.callsign == "IS0EIR"
        assert packet.packet_type == PacketType.PING
        assert packet.raw_data.startswith(b"YSFP")
        assert len(packet.raw_data) == 14

    def test_create_login_packet(self):
        """Test creating a YSFL login packet."""
        packet = YSFLoginPacket.create("IS0EIR")

        assert packet.callsign == "IS0EIR"
        assert packet.packet_type == PacketType.LOGIN
        assert packet.raw_data.startswith(b"YSFL")
        assert len(packet.raw_data) == 14

    def test_create_disconnect_packet(self):
        """Test creating a YSFU disconnect packet."""
        packet = YSFDisconnectPacket.create("IS0EIR")

        assert packet.callsign == "IS0EIR"
        assert packet.packet_type == PacketType.DISCONNECT
        assert packet.raw_data.startswith(b"YSFU")

    def test_create_options_packet_with_tg(self):
        """Test creating a YSFO packet for TG change."""
        packet = YSFOptionsPacket.create("IS0EIR", "group=2225")

        assert packet.callsign == "IS0EIR"
        assert packet.options == "group=2225"
        assert packet.target_tg == 2225
        assert packet.packet_type == PacketType.OPTIONS

    def test_create_challenge_packet(self):
        """Test creating a YSFK challenge response packet."""
        test_hash = b"0" * 32
        packet = YSFChallengePacket.create("IS0EIR", test_hash)

        assert packet.callsign == "IS0EIR"
        assert packet.challenge_hash == test_hash
        assert len(packet.raw_data) == 46

    def test_create_challenge_packet_invalid_hash(self):
        """Test that creating challenge packet with wrong hash size fails."""
        with pytest.raises(ValueError, match="32 bytes"):
            YSFChallengePacket.create("IS0EIR", b"tooshort")

    def test_create_data_packet(self):
        """Test creating a YSFD data packet."""
        payload = b"X" * 120
        packet = YSFDataPacket.create("IS0EIR", "ALL", "TG2225", payload)

        assert packet.source == "IS0EIR"
        assert packet.destination == "ALL"
        assert packet.target == "TG2225"
        assert len(packet.payload) == 120


class TestPacketParser:
    """Test parsing raw bytes into packet objects."""

    def setup_method(self):
        """Set up test fixtures."""
        self.parser = PacketParser()

    def test_parse_ping_packet(self):
        """Test parsing YSFP packet."""
        # YSFP + "IS0EIR    " (padded to 10)
        data = b"YSFPIS0EIR    "
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFPingPacket)
        assert packet.callsign == "IS0EIR"
        assert packet.packet_type == PacketType.PING

    def test_parse_login_packet(self):
        """Test parsing YSFL packet."""
        data = b"YSFLIS0EIR    "
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFLoginPacket)
        assert packet.callsign == "IS0EIR"

    def test_parse_disconnect_packet(self):
        """Test parsing YSFU packet."""
        data = b"YSFUIS0EIR    "
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFDisconnectPacket)
        assert packet.callsign == "IS0EIR"

    def test_parse_options_packet(self):
        """Test parsing YSFO packet with TG change."""
        data = b"YSFOIS0EIR    group=2225"
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFOptionsPacket)
        assert packet.callsign == "IS0EIR"
        assert packet.options == "group=2225"
        assert packet.target_tg == 2225

    def test_parse_ack_without_salt(self):
        """Test parsing short YSFACK packet (no salt)."""
        data = b"YSFACK"
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFAckPacket)
        assert packet.salt is None
        assert not packet.has_salt

    def test_parse_ack_with_salt(self):
        """Test parsing YSFACK packet with salt."""
        # YSFACK + 10 padding bytes + 4 salt bytes
        data = b"YSFACK" + b"\x00" * 10 + b"salt"
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFAckPacket)
        assert packet.salt == b"salt"
        assert packet.has_salt

    def test_parse_nak_packet(self):
        """Test parsing YSFNAK packet."""
        data = b"YSFNAK"
        packet = self.parser.parse(data)

        assert isinstance(packet, YSFNakPacket)
        assert packet.packet_type == PacketType.NAK

    def test_parse_data_packet(self):
        """Test parsing YSFD data packet."""
        # Build a minimal valid YSFD packet (160 bytes)
        header = b"YSFD"
        source = b"IS0EIR    "  # 10 bytes
        destination = b"ALL       "  # 10 bytes
        target = b"TG2225    "  # 10 bytes
        padding = b"\x00" * 6
        payload = b"X" * 120

        data = header + source + destination + target + padding + payload

        packet = self.parser.parse(data)

        assert isinstance(packet, YSFDataPacket)
        assert packet.source == "IS0EIR"
        assert packet.destination == "ALL"
        assert packet.target == "TG2225"
        assert len(packet.payload) == 120

    def test_parse_packet_too_short(self):
        """Test that packets shorter than minimum size raise error."""
        data = b"YSF"  # Only 3 bytes

        with pytest.raises(ParseError, match="too short"):
            self.parser.parse(data)

    def test_parse_unknown_packet_type(self):
        """Test that unknown packet headers raise error."""
        data = b"YSFX" + b"\x00" * 10

        with pytest.raises(ParseError, match="Unrecognized"):
            self.parser.parse(data)

    def test_parse_ping_packet_truncated(self):
        """Test parsing truncated YSFP packet raises error."""
        data = b"YSFPIU5"  # Only 7 bytes total

        with pytest.raises(ParseError, match="too short"):
            self.parser.parse(data)

    def test_try_parse_returns_none_on_error(self):
        """Test that try_parse returns None instead of raising."""
        data = b"INVALID"
        packet = self.parser.try_parse(data)

        assert packet is None

    def test_try_parse_returns_packet_on_success(self):
        """Test that try_parse returns packet on successful parse."""
        data = b"YSFPIS0EIR    "
        packet = self.parser.try_parse(data)

        assert packet is not None
        assert isinstance(packet, YSFPingPacket)

    def test_packet_roundtrip(self):
        """Test creating and parsing a packet produces equivalent result."""
        original = YSFPingPacket.create("IS0EIR")
        parsed = self.parser.parse(original.to_bytes())

        assert isinstance(parsed, YSFPingPacket)
        assert parsed.callsign == original.callsign
        assert parsed.raw_data == original.raw_data


class TestPacketProperties:
    """Test packet property methods."""

    def test_options_packet_extracts_tg(self):
        """Test that YSFOptionsPacket correctly extracts TG number."""
        packet = YSFOptionsPacket.create("IS0EIR", "group=2225")
        assert packet.target_tg == 2225

    def test_options_packet_invalid_tg(self):
        """Test that invalid TG format returns None."""
        packet = YSFOptionsPacket.create("IS0EIR", "group=invalid")
        assert packet.target_tg is None

    def test_options_packet_non_group_option(self):
        """Test that non-group options return None for target_tg."""
        packet = YSFOptionsPacket.create("IS0EIR", "something=else")
        assert packet.target_tg is None

    def test_ack_packet_has_salt_property(self):
        """Test YSFAckPacket.has_salt property."""
        # Without salt
        parser = PacketParser()
        packet_no_salt = parser.parse(b"YSFACK")
        assert not packet_no_salt.has_salt

        # With salt
        packet_with_salt = parser.parse(b"YSFACK" + b"\x00" * 10 + b"salt")
        assert packet_with_salt.has_salt


class TestPacketImmutability:
    """Test that packets are immutable (frozen dataclasses)."""

    def test_ping_packet_is_immutable(self):
        """Test that packet fields cannot be modified."""
        packet = YSFPingPacket.create("IS0EIR")

        with pytest.raises(Exception):  # FrozenInstanceError
            packet.callsign = "OTHER"

    def test_ack_packet_is_immutable(self):
        """Test that ACK packet cannot be modified."""
        parser = PacketParser()
        packet = parser.parse(b"YSFACK" + b"\x00" * 10 + b"salt")

        with pytest.raises(Exception):
            packet.salt = b"hack"
