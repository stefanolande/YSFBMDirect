"""Connection state management with finite state machine.

Replaces global boolean flags with explicit state machine for connection management.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum


class ConnectionState(Enum):
    """Connection states for the bridge."""

    DISCONNECTED = "disconnected"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    pass


@dataclass
class SessionState:
    """Immutable snapshot of session state at a point in time.

    This is a value object that can be safely shared between async tasks.
    """

    connection_state: ConnectionState
    current_tg: int
    current_dg_id: int
    client_address: tuple[str, int] | None
    last_packet_time: float
    last_ping_time: float
    pending_pings: int
    authenticated_at: float | None = None

    @property
    def is_connected(self) -> bool:
        """Check if currently connected and authenticated."""
        return self.connection_state == ConnectionState.CONNECTED

    @property
    def time_since_last_packet(self) -> float:
        """Seconds since last client packet."""
        return time.time() - self.last_packet_time

    @property
    def time_since_last_ping(self) -> float:
        """Seconds since last ping."""
        return time.time() - self.last_ping_time


class StateManager:
    """Manages connection state with proper state machine semantics.

    This class is thread-safe and uses asyncio.Lock to protect state mutations.
    State transitions are validated to prevent invalid state combinations.
    """

    def __init__(
        self,
        default_tg: int,
        default_dg_id: int,
        max_pending_pings: int = 10,
        ping_timeout: float = 30.0
    ):
        """Initialize state manager.

        Args:
            default_tg: Default talk group
            default_dg_id: Default DG-ID corresponding to default TG
            max_pending_pings: Maximum pending pings before reconnect
            ping_timeout: Seconds before ping is considered timed out
        """
        self._lock = asyncio.Lock()

        # Initial state
        self._connection_state = ConnectionState.DISCONNECTED
        self._current_tg = default_tg
        self._current_dg_id = default_dg_id
        self._client_address: tuple[str, int] | None = None
        self._last_packet_time = time.time()
        self._last_ping_time = time.time()
        self._pending_pings = 0
        self._authenticated_at: float | None = None

        # Configuration
        self._default_tg = default_tg
        self._default_dg_id = default_dg_id
        self._max_pending_pings = max_pending_pings
        self._ping_timeout = ping_timeout

    async def get_state(self) -> SessionState:
        """Get immutable snapshot of current state.

        Returns:
            SessionState value object with current state
        """
        async with self._lock:
            return SessionState(
                connection_state=self._connection_state,
                current_tg=self._current_tg,
                current_dg_id=self._current_dg_id,
                client_address=self._client_address,
                last_packet_time=self._last_packet_time,
                last_ping_time=self._last_ping_time,
                pending_pings=self._pending_pings,
                authenticated_at=self._authenticated_at
            )

    async def transition_to(self, new_state: ConnectionState) -> None:
        """Transition to a new connection state.

        Args:
            new_state: Target state

        Raises:
            StateTransitionError: If transition is invalid
        """
        async with self._lock:
            if not self._is_valid_transition(self._connection_state, new_state):
                raise StateTransitionError(
                    f"Invalid transition from {self._connection_state.value} "
                    f"to {new_state.value}"
                )

            self._connection_state = new_state

            # Side effects of state transitions
            if new_state == ConnectionState.CONNECTED:
                self._authenticated_at = time.time()
                self._pending_pings = 0
            elif new_state == ConnectionState.DISCONNECTED:
                self._authenticated_at = None
                self._pending_pings = 0

    def _is_valid_transition(
        self,
        from_state: ConnectionState,
        to_state: ConnectionState
    ) -> bool:
        """Check if state transition is valid.

        Valid transitions:
        - DISCONNECTED -> AUTHENTICATING (login initiated)
        - AUTHENTICATING -> CONNECTED (auth succeeded)
        - AUTHENTICATING -> DISCONNECTED (auth failed)
        - CONNECTED -> RECONNECTING (ping timeout or error)
        - CONNECTED -> DISCONNECTED (explicit disconnect)
        - RECONNECTING -> AUTHENTICATING (retry)
        - RECONNECTING -> DISCONNECTED (give up)
        - Any state -> same state (no-op)
        """
        if from_state == to_state:
            return True

        valid_transitions = {
            ConnectionState.DISCONNECTED: {
                ConnectionState.AUTHENTICATING
            },
            ConnectionState.AUTHENTICATING: {
                ConnectionState.CONNECTED,
                ConnectionState.DISCONNECTED
            },
            ConnectionState.CONNECTED: {
                ConnectionState.RECONNECTING,
                ConnectionState.DISCONNECTED
            },
            ConnectionState.RECONNECTING: {
                ConnectionState.AUTHENTICATING,
                ConnectionState.DISCONNECTED
            }
        }

        return to_state in valid_transitions.get(from_state, set())

    async def set_client_address(self, address: tuple[str, int]) -> None:
        """Update client address."""
        async with self._lock:
            self._client_address = address

    async def update_last_packet_time(self) -> None:
        """Record that a packet was received from client."""
        async with self._lock:
            self._last_packet_time = time.time()

    async def record_ping_sent(self) -> None:
        """Record that a ping was sent (increment pending count)."""
        async with self._lock:
            self._pending_pings += 1
            self._last_ping_time = time.time()

    async def record_ping_received(self) -> None:
        """Record that a ping response was received (decrement pending count)."""
        async with self._lock:
            if self._pending_pings > 0:
                self._pending_pings -= 1

    async def should_reconnect_due_to_ping_timeout(self) -> bool:
        """Check if we should reconnect due to ping timeout."""
        state = await self.get_state()

        # Too many pending pings
        if state.pending_pings > self._max_pending_pings:
            return True

        # Ping timeout exceeded
        if state.time_since_last_ping > self._ping_timeout:
            return True

        return False

    async def change_tg(self, new_tg: int, new_dg_id: int) -> bool:
        """Change current TG and DG-ID.

        Args:
            new_tg: New talk group
            new_dg_id: New DG-ID

        Returns:
            True if TG was changed, False if already on that TG
        """
        async with self._lock:
            if self._current_tg == new_tg:
                return False

            self._current_tg = new_tg
            self._current_dg_id = new_dg_id
            return True

    async def reset_to_default_tg(self) -> bool:
        """Reset to default TG.

        Returns:
            True if TG was changed, False if already on default
        """
        return await self.change_tg(self._default_tg, self._default_dg_id)

    async def should_return_to_default_tg(
        self,
        timeout_seconds: int
    ) -> bool:
        """Check if we should return to default TG due to inactivity.

        Args:
            timeout_seconds: Seconds of inactivity before returning to default
                            (0 disables this feature)

        Returns:
            True if should return to default TG
        """
        if timeout_seconds <= 0:
            return False

        state = await self.get_state()

        # Already on default TG
        if state.current_tg == self._default_tg:
            return False

        # Not connected
        if not state.is_connected:
            return False

        # Check if timeout exceeded
        return state.time_since_last_packet > timeout_seconds

    async def get_current_tg(self) -> int:
        """Get current talk group."""
        state = await self.get_state()
        return state.current_tg

    async def get_current_dg_id(self) -> int:
        """Get current DG-ID."""
        state = await self.get_state()
        return state.current_dg_id
