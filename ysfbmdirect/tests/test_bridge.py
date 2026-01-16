"""Integration tests for YSFBMBridge.

These tests demonstrate how the new architecture enables testing
complex bridge logic without any real network I/O.
"""

import asyncio

import pytest

from ysfbmdirect.bridge import YSFBMBridge
from ysfbmdirect.config import BridgeConfig
from ysfbmdirect.network import MockTransport
from ysfbmdirect.protocol import (
    YSFDataPacket,
    YSFPingPacket,
)
from ysfbmdirect.state import ConnectionState, StateManager


@pytest.fixture
def test_config():
    """Create test configuration."""
    return BridgeConfig(
        callsign="TEST",
        server_ip="127.0.0.1",
        bm_port=42000,
        bm_password="testpass",
        ysf_port=42001,
        default_tg=2225,
        back_to_default_time=0,  # Disabled for tests
        show_dgid_callsign=False,
        tg_change_silence_period=0,
        dgid_to_tg={
            10: 2225,
            20: 222,
            30: 91,
        }
    )


@pytest.fixture
def mock_transport():
    """Create mock transport."""
    return MockTransport()


@pytest.fixture
def state_manager(test_config):
    """Create state manager."""
    return StateManager(
        default_tg=test_config.default_tg,
        default_dg_id=test_config.default_dgid
    )


@pytest.mark.asyncio
class TestBridgeAuthentication:
    """Test authentication flow."""

    async def test_authentication_sends_login_sequence(
        self,
        test_config,
        mock_transport,
        state_manager
    ):
        """Test that bridge sends correct login sequence.

        Note: This is a simplified test that verifies the authentication
        handler works correctly in isolation. Full end-to-end authentication
        testing is complex due to concurrent receive loops.
        """
        # Test auth handler directly
        from ysfbmdirect.auth import AuthenticationHandler
        from ysfbmdirect.protocol import PacketParser

        parser = PacketParser()
        auth = AuthenticationHandler(
            transport=mock_transport,
            parser=parser,
            callsign="TEST",
            password="testpass"
        )

        # Start auth in background
        async def do_auth():
            try:
                await auth.authenticate_and_set_tg(2225)
            except Exception:
                pass  # Will timeout waiting for salt, that's ok for this test

        auth_task = asyncio.create_task(do_auth())

        # Give it time to send login
        await asyncio.sleep(0.05)

        # Verify login was sent
        login_sent = mock_transport.find_bm_packet_by_prefix(b"YSFL")
        assert login_sent is not None, "Should send YSFL login packet"

        # Now simulate salt response
        salt = b"1234"
        ack_packet_data = b"YSFACK" + b"\x00" * 10 + salt
        await mock_transport.simulate_bm_packet(ack_packet_data)

        # Wait for auth to complete (or timeout)
        await asyncio.sleep(0.2)

        # Verify challenge was sent
        challenge_sent = mock_transport.find_bm_packet_by_prefix(b"YSFK")
        assert challenge_sent is not None, "Should send YSFK challenge"

        # Verify TG option was sent
        options_sent = mock_transport.find_bm_packet_by_prefix(b"YSFO")
        assert options_sent is not None, "Should send YSFO with TG"

        # Cleanup
        auth_task.cancel()
        try:
            await auth_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
class TestBridgePacketRouting:
    """Test packet routing between BM and YSF."""

    async def test_forward_data_from_bm_to_ysf(
        self,
        test_config,
        mock_transport,
        state_manager
    ):
        """Test forwarding data packets from BM to YSF client."""
        bridge = YSFBMBridge(test_config, mock_transport, state_manager)

        # Set client address manually
        client_addr = ("192.168.1.100", 12345)
        await state_manager.set_client_address(client_addr)

        # Simulate BM sending data packet
        data_packet = YSFDataPacket.create("SOURCE", "DEST", "TARGET", b"X" * 120)
        await mock_transport.simulate_bm_packet(data_packet.to_bytes())

        # Start bridge briefly
        bridge_task = asyncio.create_task(bridge.run())

        try:
            await asyncio.sleep(0.1)

            # Verify packet was forwarded to YSF client
            ysf_packet = mock_transport.find_ysf_packet_by_prefix(b"YSFD")
            assert ysf_packet is not None
            packet_data, addr = ysf_packet
            assert addr == client_addr

        finally:
            await bridge.stop()
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass

    async def test_forward_data_from_ysf_to_bm(
        self,
        test_config,
        mock_transport,
        state_manager
    ):
        """Test forwarding data packets from YSF to BM."""
        bridge = YSFBMBridge(test_config, mock_transport, state_manager)

        # Simulate connected state
        await state_manager.transition_to(ConnectionState.AUTHENTICATING)
        await state_manager.transition_to(ConnectionState.CONNECTED)

        # Simulate YSF client sending data packet
        client_addr = ("192.168.1.100", 12345)
        data_packet = YSFDataPacket.create("TEST", "ALL", "TG2225", b"X" * 120)
        await mock_transport.simulate_ysf_packet(
            data_packet.to_bytes(),
            client_addr
        )

        # Start bridge briefly
        bridge_task = asyncio.create_task(bridge.run())

        try:
            await asyncio.sleep(0.1)

            # Verify packet was forwarded to BM
            bm_packet = mock_transport.find_bm_packet_by_prefix(b"YSFD")
            assert bm_packet is not None

            # Verify last packet time was updated
            session = await state_manager.get_state()
            assert session.time_since_last_packet < 1.0

        finally:
            await bridge.stop()
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
class TestBridgePingHandling:
    """Test ping/keepalive handling."""

    async def test_ping_tracking(
        self,
        test_config,
        mock_transport,
        state_manager
    ):
        """Test that pings are tracked correctly."""
        bridge = YSFBMBridge(test_config, mock_transport, state_manager)

        # Set connected state
        await state_manager.transition_to(ConnectionState.AUTHENTICATING)
        await state_manager.transition_to(ConnectionState.CONNECTED)

        # Simulate YSF client sending ping
        client_addr = ("192.168.1.100", 12345)
        ping_packet = YSFPingPacket.create("TEST")
        await mock_transport.simulate_ysf_packet(
            ping_packet.to_bytes(),
            client_addr
        )

        # Start bridge briefly
        bridge_task = asyncio.create_task(bridge.run())

        try:
            await asyncio.sleep(0.1)

            # Verify ping was forwarded to BM
            bm_ping = mock_transport.find_bm_packet_by_prefix(b"YSFP")
            assert bm_ping is not None

            # Verify pending pings increased
            session = await state_manager.get_state()
            assert session.pending_pings > 0

            # Simulate BM responding with ping
            await mock_transport.simulate_bm_packet(b"YSFPTEST      ")
            await asyncio.sleep(0.1)

            # Verify pending pings decreased
            session = await state_manager.get_state()
            assert session.pending_pings == 0

        finally:
            await bridge.stop()
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
class TestBridgeStateManagement:
    """Test state management in bridge."""

    async def test_initial_state_is_disconnected(
        self,
        test_config,
        mock_transport
    ):
        """Test that bridge starts in disconnected state."""
        bridge = YSFBMBridge(test_config, mock_transport)

        session = await bridge.state.get_state()
        assert session.connection_state == ConnectionState.DISCONNECTED
        assert not session.is_connected

    async def test_client_address_tracking(
        self,
        test_config,
        mock_transport,
        state_manager
    ):
        """Test that client address is tracked."""
        bridge = YSFBMBridge(test_config, mock_transport, state_manager)

        # Simulate YSF packet from client
        client_addr = ("192.168.1.100", 12345)
        ping_packet = YSFPingPacket.create("TEST")
        await mock_transport.simulate_ysf_packet(
            ping_packet.to_bytes(),
            client_addr
        )

        # Need to simulate ACK for auth to complete
        await mock_transport.simulate_bm_packet(b"YSFACK" + b"\x00" * 10 + b"salt")

        bridge_task = asyncio.create_task(bridge.run())

        try:
            await asyncio.sleep(0.2)

            # Verify client address was recorded
            session = await state_manager.get_state()
            assert session.client_address == client_addr

        finally:
            await bridge.stop()
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass


class TestBridgeConfiguration:
    """Test configuration validation and usage."""

    def test_config_validation_duplicate_tgs(self):
        """Test that duplicate TGs in mapping are detected."""
        config = BridgeConfig(
            callsign="TEST",
            server_ip="127.0.0.1",
            bm_port=42000,
            bm_password="test",
            ysf_port=42001,
            default_tg=2225,
            back_to_default_time=0,
            show_dgid_callsign=False,
            tg_change_silence_period=0,
            dgid_to_tg={
                10: 2225,
                20: 2225,  # Duplicate!
            }
        )

        with pytest.raises(ValueError, match="Duplicate TGs"):
            config.validate()

    def test_config_validation_default_tg_missing(self):
        """Test that default TG must exist in mapping."""
        config = BridgeConfig(
            callsign="TEST",
            server_ip="127.0.0.1",
            bm_port=42000,
            bm_password="test",
            ysf_port=42001,
            default_tg=9999,  # Not in mapping
            back_to_default_time=0,
            show_dgid_callsign=False,
            tg_change_silence_period=0,
            dgid_to_tg={10: 2225}
        )

        with pytest.raises(ValueError, match="not found"):
            config.validate()

    def test_config_back_to_default_seconds(self):
        """Test conversion from minutes to seconds."""
        config = BridgeConfig(
            callsign="TEST",
            server_ip="127.0.0.1",
            bm_port=42000,
            bm_password="test",
            ysf_port=42001,
            default_tg=2225,
            back_to_default_time=5,  # 5 minutes
            show_dgid_callsign=False,
            tg_change_silence_period=0,
            dgid_to_tg={10: 2225}
        )

        assert config.back_to_default_seconds == 300  # 5 * 60

    def test_config_tg_to_dgid_mapping(self):
        """Test reverse TG -> DG-ID mapping."""
        config = BridgeConfig(
            callsign="TEST",
            server_ip="127.0.0.1",
            bm_port=42000,
            bm_password="test",
            ysf_port=42001,
            default_tg=2225,
            back_to_default_time=0,
            show_dgid_callsign=False,
            tg_change_silence_period=0,
            dgid_to_tg={
                10: 2225,
                20: 222,
            }
        )

        assert config.tg_to_dgid == {2225: 10, 222: 20}
        assert config.default_dgid == 10
