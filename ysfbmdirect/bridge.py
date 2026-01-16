"""YSF to Brandmeister bridge orchestrator.

Main application logic coordinating packet flow, authentication, and state management.
"""

import asyncio
import logging

from .auth import AuthenticationError, AuthenticationHandler
from .config import BridgeConfig
from .network import NetworkTransport
from .protocol import (
    PacketParser,
    YSFAckPacket,
    YSFDataPacket,
    YSFDisconnectPacket,
    YSFNakPacket,
    YSFOptionsPacket,
    YSFPingPacket,
)
from .state import ConnectionState, StateManager

# Import YSF codec utilities (from ysf/ package in project root)
# These are installed as a top-level package via pyproject.toml
try:
    from ysf import ysffich, ysfpayload
    from ysf.ysffich import DT
except ImportError:
    # Gracefully handle missing ysf package (e.g., during testing)
    ysffich = None
    ysfpayload = None
    DT = None


logger = logging.getLogger(__name__)


class YSFBMBridge:
    """Main bridge orchestrator.

    Coordinates packet flow between YSF client and Brandmeister server.
    Uses dependency injection for all external dependencies, making it fully testable.
    """

    def __init__(
        self,
        config: BridgeConfig,
        transport: NetworkTransport,
        state_manager: StateManager | None = None,
    ):
        """Initialize bridge.

        Args:
            config: Bridge configuration
            transport: Network transport implementation
            state_manager: Optional state manager (creates default if not provided)
        """
        self.config = config
        self.transport = transport
        self.parser = PacketParser()

        # State management
        self.state = state_manager or StateManager(
            default_tg=config.default_tg,
            default_dg_id=config.default_dgid,
            max_pending_pings=10,
            ping_timeout=30.0,
        )

        # Authentication handler
        self.auth = AuthenticationHandler(
            transport=transport,
            parser=self.parser,
            callsign=config.callsign,
            password=config.bm_password,
        )

        # Task tracking
        self._tasks: list[asyncio.Task] = []
        self._running = False

        # Event callbacks
        self._packet_callbacks: list = []

    def register_packet_callback(self, callback):
        """Register callback for packet events.

        Callback signature: callback(direction: str, protocol: str)
        - direction: "sent" or "received"
        - protocol: "bm" or "ysf"

        Args:
            callback: Callable to invoke on packet events
        """
        self._packet_callbacks.append(callback)

    def _emit_packet_event(self, direction: str, protocol: str):
        """Emit packet event to all registered callbacks.

        Args:
            direction: "sent" or "received"
            protocol: "bm" or "ysf"
        """
        for callback in self._packet_callbacks:
            try:
                callback(direction, protocol)
            except Exception as e:
                logger.error(f"Error in packet callback: {e}", exc_info=True)

    async def run(self) -> None:
        """Run the bridge.

        Main entry point that starts all async tasks and runs until stopped.
        """
        logger.info("Starting YSF-BM Bridge")
        self._running = True

        try:
            # Create all concurrent tasks
            self._tasks = [
                asyncio.create_task(self._bm_to_ysf_loop(), name="bm_to_ysf"),
                asyncio.create_task(self._ysf_to_bm_loop(), name="ysf_to_bm"),
                asyncio.create_task(self._timed_checks_loop(), name="timed_checks"),
            ]

            # Wait for all tasks
            await asyncio.gather(*self._tasks)

        except asyncio.CancelledError:
            logger.info("Bridge cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the bridge gracefully."""
        if not self._running:
            return

        logger.info("Stopping bridge")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Cleanup
        session = await self.state.get_state()
        if session.is_connected:
            try:
                await self.auth.send_logout()
            except Exception as e:
                logger.error(f"Error sending logout: {e}")

        await self.transport.close()
        logger.info("Bridge stopped")

    async def _bm_to_ysf_loop(self) -> None:
        """Receive packets from BM and forward to YSF client."""
        logger.info("Started BM -> YSF packet loop")

        while self._running:
            try:
                # Receive packet from BM
                data = await self.transport.receive_from_bm()

                if not data:
                    continue

                logger.debug(f"BM -> YSF: {len(data)} bytes")

                # Emit packet received event
                self._emit_packet_event("received", "bm")

                # Parse packet
                packet = self.parser.try_parse(data)
                if not packet:
                    logger.warning(f"Failed to parse BM packet: {data[:20]}")
                    continue

                # Handle based on packet type
                await self._handle_bm_packet(packet, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in BM->YSF loop: {e}", exc_info=True)
                await asyncio.sleep(1)  # Back off on error

    async def _ysf_to_bm_loop(self) -> None:
        """Receive packets from YSF client and forward to BM."""
        logger.info("Started YSF -> BM packet loop")

        while self._running:
            try:
                # Receive packet from YSF client
                data, address = await self.transport.receive_from_ysf()

                # Update client address
                await self.state.set_client_address(address)

                if not data:
                    continue

                logger.debug(f"YSF -> BM: {len(data)} bytes from {address}")

                # Emit packet received event
                self._emit_packet_event("received", "ysf")

                # Parse packet
                packet = self.parser.try_parse(data)
                if not packet:
                    logger.warning(f"Failed to parse YSF packet: {data[:20]}")
                    continue

                # Handle based on packet type
                await self._handle_ysf_packet(packet, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in YSF->BM loop: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _timed_checks_loop(self) -> None:
        """Periodic checks for timeouts and state management."""
        logger.info("Started timed checks loop")

        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                session = await self.state.get_state()

                # Check for ping timeout
                if session.is_connected:
                    should_reconnect = await self.state.should_reconnect_due_to_ping_timeout()
                    if should_reconnect:
                        logger.warning("Ping timeout detected, logging out")
                        await self.auth.send_logout()
                        await self.state.transition_to(ConnectionState.DISCONNECTED)

                # Check for return to default TG
                if self.config.back_to_default_seconds > 0:
                    should_return = await self.state.should_return_to_default_tg(
                        self.config.back_to_default_seconds
                    )
                    if should_return:
                        logger.info(
                            f"Returning to default TG {self.config.default_tg} after timeout"
                        )
                        await self.state.reset_to_default_tg()
                        await self.auth.set_tg(self.config.default_tg)

                        # Send notification to YSF client
                        if session.client_address and ysffich:
                            await self._send_tg_change_notification(
                                self.config.default_tg, session.client_address
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in timed checks: {e}", exc_info=True)

    async def _handle_bm_packet(self, packet, raw_data: bytes) -> None:
        """Handle packet received from BM server.

        Args:
            packet: Parsed packet object
            raw_data: Original raw bytes
        """
        session = await self.state.get_state()

        # Handle PING response
        if isinstance(packet, YSFPingPacket):
            if session.is_connected:
                await self.state.record_ping_received()
                logger.debug("Received ping response from BM")

        # Handle NAK (error response)
        elif isinstance(packet, YSFNakPacket):
            logger.error("Received NAK from BM")
            await self.state.transition_to(ConnectionState.DISCONNECTED)

        # Handle ACK (acknowledgment)
        elif isinstance(packet, YSFAckPacket):
            # ACK packets are handled during authentication
            # Ignore here as they're consumed by auth handler
            pass

        # Handle DATA packets (voice/data)
        elif isinstance(packet, YSFDataPacket):  # noqa: SIM102
            # Optionally add DG-ID to callsign display
            if self.config.show_dgid_callsign and ysffich and ysfpayload:
                modified_data = await self._add_dgid_to_callsign(raw_data)
                raw_data = modified_data

        # Forward to YSF client if we have an address
        if session.client_address:
            await self.transport.send_to_ysf(raw_data, session.client_address)
            self._emit_packet_event("sent", "ysf")

    async def _handle_ysf_packet(self, packet, raw_data: bytes) -> None:
        """Handle packet received from YSF client.

        Args:
            packet: Parsed packet object
            raw_data: Original raw bytes
        """
        session = await self.state.get_state()

        # Handle PING from client
        if isinstance(packet, YSFPingPacket):
            if not session.is_connected:
                # Not logged in yet, trigger authentication
                logger.info("Client ping received, authenticating to BM")
                try:
                    await self.state.transition_to(ConnectionState.AUTHENTICATING)
                    await self.auth.authenticate_and_set_tg(self.config.default_tg)
                    await self.state.transition_to(ConnectionState.CONNECTED)
                    await self.state.record_ping_sent()
                except AuthenticationError as e:
                    logger.error(f"Authentication failed: {e}")
                    await self.state.transition_to(ConnectionState.DISCONNECTED)
                    return
            else:
                # Already connected, forward ping
                await self.state.record_ping_sent()

        # Handle DATA packets
        elif isinstance(packet, YSFDataPacket):
            await self.state.update_last_packet_time()

            # Check for TG change based on DG-ID
            if session.is_connected and ysffich:
                await self._handle_potential_tg_change(raw_data)

        # Handle DISCONNECT from client
        elif isinstance(packet, YSFDisconnectPacket):
            logger.info("Client disconnected")
            await self.state.transition_to(ConnectionState.DISCONNECTED)

        # Handle OPTIONS (ignore wires-x commands sent to client)
        elif isinstance(packet, YSFOptionsPacket):
            # Don't forward YSFO to BM
            return

        # Forward to BM (unless it was OPTIONS)
        if not isinstance(packet, YSFOptionsPacket):
            await self.transport.send_to_bm(raw_data)
            self._emit_packet_event("sent", "bm")

    async def _handle_potential_tg_change(self, raw_data: bytes) -> None:
        """Check if DATA packet contains a DG-ID change and handle it.

        Args:
            raw_data: Raw YSFD packet data
        """
        if not ysffich:
            return

        try:
            # Decode FICH from packet
            ysffich.decode(raw_data[40:])
            dg_id = ysffich.getSQ()
            dt = ysffich.getDT()

            # Skip wires-x commands
            if dt == DT.DATA and (dg_id == 127 or dg_id == 0):
                return

            # Check if DG-ID maps to a TG and is different from current
            if dg_id not in self.config.dgid_to_tg:
                return

            current_dg_id = await self.state.get_current_dg_id()
            if dg_id == current_dg_id:
                return

            # Check silence period
            session = await self.state.get_state()
            if session.time_since_last_packet < self.config.tg_change_silence_period:
                return

            # Perform TG change
            new_tg = self.config.dgid_to_tg[dg_id]
            logger.info(f"Changing TG to {new_tg} (DG-ID {dg_id})")

            await self.state.change_tg(new_tg, dg_id)
            await self.auth.set_tg(new_tg)

            # Drain any buffered packets to prevent processing stale data
            await self._drain_buffered_packets()

            # Send notification to YSF client
            if session.client_address:
                await self._send_tg_change_notification(new_tg, session.client_address)

            await self.state.update_last_packet_time()

        except Exception as e:
            logger.error(f"Error handling TG change: {e}", exc_info=True)

    async def _add_dgid_to_callsign(self, raw_data: bytes) -> bytes:
        """Add DG-ID to source callsign in data packet.

        Args:
            raw_data: Original YSFD packet

        Returns:
            Modified packet with DG-ID in callsign field
        """
        if not ysffich or not ysfpayload:
            return raw_data

        try:
            data_array = bytearray(raw_data)
            ysffich.decode(data_array[40:])

            fn = ysffich.getFN()
            dt = ysffich.getDT()

            # Only modify VD2 packets on frame 1
            if fn == 1 and dt == DT.VD2:
                payload = bytearray(data_array[35:])
                orig = str(data_array[14:24], "utf-8").strip()

                # Get current DG-ID and add to callsign
                current_dg_id = await self.state.get_current_dg_id()
                src = (str(current_dg_id) + "/" + orig).ljust(10).encode()

                ysfpayload.writeVDMmode2Data(payload, src)
                data_array[35:] = payload

            return bytes(data_array)

        except Exception as e:
            logger.error(f"Error adding DG-ID to callsign: {e}")
            return raw_data

    async def _drain_buffered_packets(self) -> None:
        """Drain any buffered packets from receive queue.

        This prevents processing stale packets after a TG change.
        Attempts to read up to 5 packets with a short timeout.
        """
        try:
            for _ in range(5):
                try:
                    # Try to read with 110ms timeout
                    await asyncio.wait_for(self.transport.receive_from_ysf(), timeout=0.11)
                except asyncio.TimeoutError:
                    # No more packets buffered
                    break
        except Exception as e:
            logger.debug(f"Error draining buffered packets: {e}")

    async def _send_tg_change_notification(self, new_tg: int, client_address) -> None:
        """Send TG change notification to YSF client.

        Sends 16 YSF data frames with the TG number as the source callsign
        (e.g., "TG222") to notify the client about the TG change.

        Args:
            new_tg: New talk group number
            client_address: Client address to send to
        """
        if not ysfpayload:
            logger.warning("Cannot send TG notification: ysfpayload not available")
            return

        try:
            # Prepare TG string as callsign (e.g., "TG222     ")
            tg_str = f"TG{new_tg}".ljust(10)[:10]

            # Prepare network header (40 bytes)
            # Format: "YSFD" + callsign (10) + callsign (10) + "ALL" (10)
            callsign_padded = self.config.callsign.ljust(10)[:10].encode("ascii")
            dest_padded = "ALL".ljust(10)[:10].encode("ascii")
            header = b"YSFD" + callsign_padded + callsign_padded + dest_padded

            # Generate C4FM payload (120 bytes)
            # CSD1: Destination + Source (10+10 = 20 bytes)
            # CSD2: Source + Source (for redundancy)
            call_src = tg_str.encode("ascii")
            call_dst = "ALL".ljust(10)[:10].encode("ascii")
            csd1 = call_dst + call_src
            csd2 = call_src + call_src

            payload = [0] * 120
            ysfpayload.writeHeader(payload, csd1, csd2)
            payload_bytes = bytearray(payload)

            # Send 16 frames to ensure client receives the notification
            logger.debug(f"Sending TG change notification: {tg_str.strip()}")
            for _ in range(16):
                await self.transport.send_to_ysf(header + payload_bytes, client_address)
                self._emit_packet_event("sent", "ysf")

        except Exception as e:
            logger.error(f"Error sending TG change notification: {e}", exc_info=True)
