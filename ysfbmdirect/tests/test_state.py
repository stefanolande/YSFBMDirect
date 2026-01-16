"""Tests for connection state management."""

import asyncio
import time

import pytest

from ysfbmdirect.state import (
    ConnectionState,
    SessionState,
    StateManager,
    StateTransitionError,
)


class TestSessionState:
    """Test SessionState value object."""

    def test_session_state_creation(self):
        """Test creating a session state snapshot."""
        state = SessionState(
            connection_state=ConnectionState.CONNECTED,
            current_tg=2225,
            current_dg_id=10,
            client_address=("192.168.1.1", 12345),
            last_packet_time=time.time(),
            last_ping_time=time.time(),
            pending_pings=0,
            authenticated_at=time.time()
        )

        assert state.connection_state == ConnectionState.CONNECTED
        assert state.current_tg == 2225
        assert state.is_connected

    def test_is_connected_property(self):
        """Test is_connected property."""
        connected = SessionState(
            connection_state=ConnectionState.CONNECTED,
            current_tg=2225,
            current_dg_id=10,
            client_address=None,
            last_packet_time=time.time(),
            last_ping_time=time.time(),
            pending_pings=0
        )
        assert connected.is_connected

        disconnected = SessionState(
            connection_state=ConnectionState.DISCONNECTED,
            current_tg=2225,
            current_dg_id=10,
            client_address=None,
            last_packet_time=time.time(),
            last_ping_time=time.time(),
            pending_pings=0
        )
        assert not disconnected.is_connected

    def test_time_since_last_packet(self):
        """Test time_since_last_packet calculation."""
        past_time = time.time() - 5.0  # 5 seconds ago
        state = SessionState(
            connection_state=ConnectionState.CONNECTED,
            current_tg=2225,
            current_dg_id=10,
            client_address=None,
            last_packet_time=past_time,
            last_ping_time=time.time(),
            pending_pings=0
        )

        # Should be approximately 5 seconds
        assert 4.9 <= state.time_since_last_packet <= 5.1


@pytest.mark.asyncio
class TestStateManager:
    """Test StateManager with proper state machine semantics."""

    async def test_initial_state(self):
        """Test initial state after creation."""
        manager = StateManager(default_tg=2225, default_dg_id=10)
        state = await manager.get_state()

        assert state.connection_state == ConnectionState.DISCONNECTED
        assert state.current_tg == 2225
        assert state.current_dg_id == 10
        assert state.pending_pings == 0
        assert state.client_address is None
        assert not state.is_connected

    async def test_valid_transition_disconnected_to_authenticating(self):
        """Test valid transition from DISCONNECTED to AUTHENTICATING."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        await manager.transition_to(ConnectionState.AUTHENTICATING)
        state = await manager.get_state()

        assert state.connection_state == ConnectionState.AUTHENTICATING

    async def test_valid_transition_authenticating_to_connected(self):
        """Test valid transition from AUTHENTICATING to CONNECTED."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)
        state = await manager.get_state()

        assert state.connection_state == ConnectionState.CONNECTED
        assert state.is_connected
        assert state.authenticated_at is not None
        assert state.pending_pings == 0  # Reset on connect

    async def test_invalid_transition_disconnected_to_connected(self):
        """Test that invalid transition raises error."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        with pytest.raises(StateTransitionError, match="Invalid transition"):
            await manager.transition_to(ConnectionState.CONNECTED)

    async def test_transition_to_same_state_is_valid(self):
        """Test that transitioning to same state is always valid."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Should not raise error
        await manager.transition_to(ConnectionState.DISCONNECTED)
        state = await manager.get_state()

        assert state.connection_state == ConnectionState.DISCONNECTED

    async def test_set_client_address(self):
        """Test setting client address."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        await manager.set_client_address(("192.168.1.1", 12345))
        state = await manager.get_state()

        assert state.client_address == ("192.168.1.1", 12345)

    async def test_update_last_packet_time(self):
        """Test updating last packet timestamp."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Get initial time
        state1 = await manager.get_state()
        initial_time = state1.last_packet_time

        # Wait a bit and update
        await asyncio.sleep(0.1)
        await manager.update_last_packet_time()

        state2 = await manager.get_state()
        assert state2.last_packet_time > initial_time

    async def test_ping_tracking(self):
        """Test ping sent/received tracking."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Send pings
        await manager.record_ping_sent()
        await manager.record_ping_sent()
        await manager.record_ping_sent()

        state = await manager.get_state()
        assert state.pending_pings == 3

        # Receive responses
        await manager.record_ping_received()
        await manager.record_ping_received()

        state = await manager.get_state()
        assert state.pending_pings == 1

    async def test_should_reconnect_due_to_too_many_pings(self):
        """Test reconnect detection due to too many pending pings."""
        manager = StateManager(
            default_tg=2225,
            default_dg_id=10,
            max_pending_pings=3
        )

        # Send 4 pings (exceeds max of 3)
        for _ in range(4):
            await manager.record_ping_sent()

        should_reconnect = await manager.should_reconnect_due_to_ping_timeout()
        assert should_reconnect

    async def test_should_reconnect_due_to_ping_timeout(self):
        """Test reconnect detection due to ping timeout."""
        manager = StateManager(
            default_tg=2225,
            default_dg_id=10,
            ping_timeout=0.1  # 100ms timeout
        )

        # Record ping and wait for timeout
        await manager.record_ping_sent()
        await asyncio.sleep(0.15)

        should_reconnect = await manager.should_reconnect_due_to_ping_timeout()
        assert should_reconnect

    async def test_change_tg(self):
        """Test changing TG."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Change to new TG
        changed = await manager.change_tg(new_tg=222, new_dg_id=5)
        assert changed

        state = await manager.get_state()
        assert state.current_tg == 222
        assert state.current_dg_id == 5

        # Try changing to same TG (should return False)
        changed = await manager.change_tg(new_tg=222, new_dg_id=5)
        assert not changed

    async def test_reset_to_default_tg(self):
        """Test resetting to default TG."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Change to different TG
        await manager.change_tg(new_tg=222, new_dg_id=5)

        # Reset to default
        changed = await manager.reset_to_default_tg()
        assert changed

        state = await manager.get_state()
        assert state.current_tg == 2225
        assert state.current_dg_id == 10

    async def test_should_return_to_default_tg(self):
        """Test detecting when to return to default TG."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Connect and change TG
        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)
        await manager.change_tg(new_tg=222, new_dg_id=5)

        # Should not return yet (just changed)
        should_return = await manager.should_return_to_default_tg(timeout_seconds=1)
        assert not should_return

        # Wait for timeout
        await asyncio.sleep(1.1)

        should_return = await manager.should_return_to_default_tg(timeout_seconds=1)
        assert should_return

    async def test_should_return_to_default_tg_disabled(self):
        """Test that timeout=0 disables auto-return to default."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)
        await manager.change_tg(new_tg=222, new_dg_id=5)

        # Even after timeout, should not return if feature disabled
        await asyncio.sleep(0.1)
        should_return = await manager.should_return_to_default_tg(timeout_seconds=0)
        assert not should_return

    async def test_should_return_to_default_when_already_default(self):
        """Test that we don't return to default when already there."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)

        # Already on default TG
        should_return = await manager.should_return_to_default_tg(timeout_seconds=1)
        assert not should_return

    async def test_concurrent_access(self):
        """Test that StateManager is thread-safe with concurrent access."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Multiple coroutines updating state concurrently
        async def update_pings():
            for _ in range(10):
                await manager.record_ping_sent()
                await asyncio.sleep(0.001)

        async def update_packets():
            for _ in range(10):
                await manager.update_last_packet_time()
                await asyncio.sleep(0.001)

        # Run concurrently
        await asyncio.gather(update_pings(), update_packets())

        state = await manager.get_state()
        assert state.pending_pings == 10  # All pings should be counted

    async def test_get_current_tg(self):
        """Test getting current TG."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        tg = await manager.get_current_tg()
        assert tg == 2225

        await manager.change_tg(222, 5)
        tg = await manager.get_current_tg()
        assert tg == 222

    async def test_get_current_dg_id(self):
        """Test getting current DG-ID."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        dg_id = await manager.get_current_dg_id()
        assert dg_id == 10

        await manager.change_tg(222, 5)
        dg_id = await manager.get_current_dg_id()
        assert dg_id == 5


@pytest.mark.asyncio
class TestStateTransitions:
    """Test all valid and invalid state transitions."""

    async def test_full_connection_flow(self):
        """Test complete connection flow."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Initial state
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.DISCONNECTED

        # Start authentication
        await manager.transition_to(ConnectionState.AUTHENTICATING)
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.AUTHENTICATING

        # Successfully connect
        await manager.transition_to(ConnectionState.CONNECTED)
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.CONNECTED
        assert state.is_connected

        # Disconnect
        await manager.transition_to(ConnectionState.DISCONNECTED)
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.DISCONNECTED
        assert not state.is_connected

    async def test_reconnection_flow(self):
        """Test reconnection after timeout."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Connect
        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)

        # Enter reconnecting state
        await manager.transition_to(ConnectionState.RECONNECTING)
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.RECONNECTING

        # Try to authenticate again
        await manager.transition_to(ConnectionState.AUTHENTICATING)
        await manager.transition_to(ConnectionState.CONNECTED)
        state = await manager.get_state()
        assert state.is_connected

    async def test_failed_authentication(self):
        """Test failed authentication flow."""
        manager = StateManager(default_tg=2225, default_dg_id=10)

        # Start authentication
        await manager.transition_to(ConnectionState.AUTHENTICATING)

        # Authentication fails, go back to disconnected
        await manager.transition_to(ConnectionState.DISCONNECTED)
        state = await manager.get_state()
        assert state.connection_state == ConnectionState.DISCONNECTED
