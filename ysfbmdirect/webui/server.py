"""FastAPI web server for monitoring and configuration."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TGChangeRequest(BaseModel):
    """Request model for TG change."""
    tg: int
    dgid: int | None = None


class ConfigUpdateRequest(BaseModel):
    """Request model for configuration update."""
    config: dict


@dataclass
class BridgeStatus:
    """Current bridge status snapshot."""
    state: str
    current_tg: int
    current_dgid: int
    client_address: str | None
    uptime_seconds: float
    bm_packets_sent: int
    bm_packets_received: int
    ysf_packets_sent: int
    ysf_packets_received: int
    pending_pings: int
    last_activity: str | None


class WebUIServer:
    """Web UI server for monitoring and configuration."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, config_file: str | None = None):
        """Initialize web UI server.

        Args:
            host: Host to bind to
            port: Port to listen on
            config_file: Path to configuration file (for editing)
        """
        self.host = host
        self.port = port
        self.config_file = config_file
        self.app = FastAPI(title="YSFBMDirect Web UI")

        # Bridge reference (set by caller)
        self.bridge = None
        self.start_time = time.time()

        # Statistics
        self.stats = {
            "bm_packets_sent": 0,
            "bm_packets_received": 0,
            "ysf_packets_sent": 0,
            "ysf_packets_received": 0,
        }

        # Log buffer (last 100 messages)
        self.log_buffer: deque[dict] = deque(maxlen=100)

        # WebSocket clients
        self.ws_clients: list[WebSocket] = []

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup FastAPI routes."""

        @self.app.get("/")
        async def root():
            """Serve main HTML page."""
            return HTMLResponse(content=self._get_html())

        @self.app.get("/api/status")
        async def get_status():
            """Get current bridge status."""
            return await self._get_status()

        @self.app.get("/api/stats")
        async def get_stats():
            """Get statistics."""
            return {
                **self.stats,
                "uptime_seconds": time.time() - self.start_time,
            }

        @self.app.get("/api/logs")
        async def get_logs():
            """Get recent log messages."""
            return {"logs": list(self.log_buffer)}

        @self.app.post("/api/tg")
        async def change_tg(request: TGChangeRequest):
            """Change talk group manually.

            Args:
                request: TG change request with tg and optional dgid

            Returns:
                Success message
            """
            if not self.bridge:
                raise HTTPException(status_code=503, detail="Bridge not initialized")

            try:
                # Determine DG-ID
                dgid = request.dgid
                if dgid is None:
                    # Look up DG-ID from TG mapping
                    tg_to_dgid = self.bridge.config.tg_to_dgid
                    if request.tg not in tg_to_dgid:
                        raise HTTPException(
                            status_code=400,
                            detail=f"TG {request.tg} not found in configuration"
                        )
                    dgid = tg_to_dgid[request.tg]

                # Change TG
                changed = await self.bridge.state.change_tg(request.tg, dgid)

                if changed:
                    # Update BM server
                    await self.bridge.auth.set_tg(request.tg)

                    # Log the change
                    self.add_log("INFO", f"Manually changed to TG {request.tg} (DG-ID {dgid})")

                    # Broadcast status update
                    await self.broadcast_status_update()

                    return {"success": True, "message": f"Changed to TG {request.tg}"}
                else:
                    return {"success": False, "message": f"Already on TG {request.tg}"}

            except Exception as e:
                logger.error(f"Error changing TG: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/config")
        async def get_config():
            """Get current configuration.

            Returns:
                Current configuration as dictionary
            """
            if not self.config_file:
                raise HTTPException(
                    status_code=503,
                    detail="Configuration file path not available"
                )

            try:
                with open(self.config_file) as f:
                    config_data = yaml.safe_load(f)
                return {"config": config_data}
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="Configuration file not found")
            except Exception as e:
                logger.error(f"Error reading config: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/config")
        async def update_config(request: ConfigUpdateRequest):
            """Update configuration.

            Args:
                request: Configuration update request

            Returns:
                Success message (restart required)
            """
            if not self.config_file:
                raise HTTPException(
                    status_code=503,
                    detail="Configuration file path not available"
                )

            try:
                # Validate configuration structure
                config_data = request.config

                # Basic validation
                required_sections = ["connection", "talkgroups"]
                for section in required_sections:
                    if section not in config_data:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Missing required section: {section}"
                        )

                # Write configuration to file
                with open(self.config_file, "w") as f:
                    yaml.safe_dump(config_data, f, default_flow_style=False, sort_keys=False)

                self.add_log("WARNING", "Configuration updated - restart required to apply changes")

                return {
                    "success": True,
                    "message": "Configuration saved successfully. Restart required to apply changes."
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating config: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/config/validate")
        async def validate_config(request: ConfigUpdateRequest):
            """Validate configuration without saving.

            Args:
                request: Configuration to validate

            Returns:
                Validation result
            """
            try:
                from ..config import BridgeConfig

                config_data = request.config

                # Extract configuration sections
                connection = config_data.get("connection", {})
                talkgroups = config_data.get("talkgroups", {})

                # Parse DGID-TO-TG mapping
                dgid_to_tg_map = talkgroups.get("dgid_to_tg", {})
                dgid_to_tg = {int(k): int(v) for k, v in dgid_to_tg_map.items()}

                # Create config object for validation
                config = BridgeConfig(
                    callsign=connection.get("callsign", ""),
                    bm_host=connection.get("bm_host", "44.131.4.1"),
                    bm_port=int(connection.get("bm_port", 42000)),
                    bm_password=connection.get("bm_password", ""),
                    ysf_port=int(connection.get("ysf_port", 42002)),
                    default_tg=int(talkgroups.get("default_tg", 0)),
                    default_dgid=int(talkgroups.get("default_dgid", 0)),
                    dgid_to_tg=dgid_to_tg,
                    back_to_default_seconds=int(talkgroups.get("back_to_default_seconds", 0)),
                    show_dgid_callsign=bool(talkgroups.get("show_dgid_callsign", False)),
                    tg_change_silence_period=float(talkgroups.get("tg_change_silence_period", 0)),
                )

                # Validate
                config.validate()

                return {"valid": True, "message": "Configuration is valid"}

            except ValueError as e:
                return {"valid": False, "message": str(e)}
            except Exception as e:
                logger.error(f"Error validating config: {e}", exc_info=True)
                return {"valid": False, "message": f"Validation error: {str(e)}"}

        @self.app.websocket("/api/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates."""
            await websocket.accept()
            self.ws_clients.append(websocket)
            logger.info(f"WebSocket client connected: {websocket.client}")

            try:
                # Send initial status
                status = await self._get_status()
                await websocket.send_json({"type": "status_update", "data": status})

                # Keep connection alive and listen for messages
                while True:
                    try:
                        # Ping every 30 seconds to keep connection alive
                        await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Send ping to keep alive
                        await websocket.send_json({"type": "ping"})

            except WebSocketDisconnect:
                logger.info(f"WebSocket client disconnected: {websocket.client}")
            finally:
                if websocket in self.ws_clients:
                    self.ws_clients.remove(websocket)

    async def _get_status(self) -> dict:
        """Get current bridge status.

        Returns:
            Status dictionary
        """
        if not self.bridge:
            return {
                "state": "not_initialized",
                "current_tg": 0,
                "current_dgid": 0,
                "client_address": None,
                "uptime_seconds": time.time() - self.start_time,
                **self.stats,
                "pending_pings": 0,
                "last_activity": None,
            }

        # Get state from bridge
        session = await self.bridge.state.get_state()

        return {
            "state": session.connection_state.value,
            "current_tg": session.current_tg,
            "current_dgid": session.current_dg_id,
            "client_address": f"{session.client_address[0]}:{session.client_address[1]}"
                               if session.client_address else None,
            "uptime_seconds": time.time() - self.start_time,
            **self.stats,
            "pending_pings": session.pending_pings,
            "last_activity": datetime.fromtimestamp(session.last_packet_time).isoformat()
                            if session.last_packet_time else None,
        }

    async def broadcast_status_update(self):
        """Broadcast status update to all WebSocket clients."""
        if not self.ws_clients:
            return

        try:
            status = await self._get_status()
            message = {"type": "status_update", "data": status}

            # Send to all connected clients
            dead_clients = []
            for client in self.ws_clients:
                try:
                    await client.send_json(message)
                except Exception as e:
                    logger.warning(f"Failed to send to WebSocket client: {e}")
                    dead_clients.append(client)

            # Remove dead clients
            for client in dead_clients:
                self.ws_clients.remove(client)

        except Exception as e:
            logger.error(f"Error broadcasting status update: {e}")

    def add_log(self, level: str, message: str):
        """Add log message to buffer.

        Args:
            level: Log level (INFO, WARNING, ERROR, etc.)
            message: Log message
        """
        log_entry = {
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        self.log_buffer.append(log_entry)

        # Broadcast to WebSocket clients
        if self.ws_clients:
            asyncio.create_task(self._broadcast_log(log_entry))

    async def _broadcast_log(self, log_entry: dict):
        """Broadcast log entry to WebSocket clients."""
        message = {"type": "log", "data": log_entry}

        dead_clients = []
        for client in self.ws_clients:
            try:
                await client.send_json(message)
            except Exception:
                dead_clients.append(client)

        for client in dead_clients:
            if client in self.ws_clients:
                self.ws_clients.remove(client)

    def _get_html(self) -> str:
        """Get HTML content for main page."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YSFBMDirect Web UI</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        header {
            background: #2d2d2d;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border-left: 4px solid #4CAF50;
        }
        h1 {
            color: #4CAF50;
            margin-bottom: 5px;
        }
        .subtitle {
            color: #888;
            font-size: 14px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: #2d2d2d;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #3d3d3d;
        }
        .card h2 {
            color: #4CAF50;
            margin-bottom: 15px;
            font-size: 18px;
            border-bottom: 1px solid #3d3d3d;
            padding-bottom: 10px;
        }
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-connected { background: #4CAF50; box-shadow: 0 0 8px #4CAF50; }
        .status-disconnected { background: #f44336; }
        .status-authenticating { background: #FFC107; }
        .info-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #3d3d3d;
        }
        .info-row:last-child { border-bottom: none; }
        .info-label {
            color: #888;
            font-weight: 500;
        }
        .info-value {
            color: #e0e0e0;
            font-family: monospace;
        }
        .logs-container {
            background: #1a1a1a;
            border: 1px solid #3d3d3d;
            border-radius: 4px;
            padding: 15px;
            height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 13px;
        }
        .log-entry {
            padding: 4px 0;
            border-bottom: 1px solid #2d2d2d;
        }
        .log-timestamp {
            color: #666;
            margin-right: 8px;
        }
        .log-level-INFO { color: #4CAF50; }
        .log-level-WARNING { color: #FFC107; }
        .log-level-ERROR { color: #f44336; }
        .log-message {
            color: #e0e0e0;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔗 YSFBMDirect Web UI</h1>
            <div class="subtitle">Monitor and configure your YSF to Brandmeister bridge</div>
        </header>

        <div class="grid">
            <div class="card">
                <h2>Status</h2>
                <div class="info-row">
                    <span class="info-label">Connection:</span>
                    <span class="info-value">
                        <span id="status-indicator" class="status-indicator status-disconnected"></span>
                        <span id="status-text">Disconnected</span>
                    </span>
                </div>
                <div class="info-row">
                    <span class="info-label">Talk Group:</span>
                    <span class="info-value" id="current-tg">-</span>
                </div>
                <div class="info-row">
                    <span class="info-label">DG-ID:</span>
                    <span class="info-value" id="current-dgid">-</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Client:</span>
                    <span class="info-value" id="client-address">-</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Uptime:</span>
                    <span class="info-value" id="uptime">-</span>
                </div>
            </div>

            <div class="card">
                <h2>Statistics</h2>
                <div class="stats-grid">
                    <div class="info-row">
                        <span class="info-label">BM TX:</span>
                        <span class="info-value" id="bm-tx">0</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">BM RX:</span>
                        <span class="info-value" id="bm-rx">0</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">YSF TX:</span>
                        <span class="info-value" id="ysf-tx">0</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">YSF RX:</span>
                        <span class="info-value" id="ysf-rx">0</span>
                    </div>
                </div>
                <div class="info-row">
                    <span class="info-label">Pending Pings:</span>
                    <span class="info-value" id="pending-pings">0</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Last Activity:</span>
                    <span class="info-value" id="last-activity">-</span>
                </div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Manual TG Change</h2>
                <div style="margin-bottom: 15px;">
                    <div style="display: flex; gap: 10px; margin-bottom: 10px;">
                        <input type="number" id="manual-tg-input" placeholder="Enter TG number"
                               style="flex: 1; padding: 8px; background: #1a1a1a; border: 1px solid #3d3d3d;
                                      color: #e0e0e0; border-radius: 4px;">
                        <button onclick="changeTGManual()" style="padding: 8px 20px; background: #4CAF50;
                                color: white; border: none; border-radius: 4px; cursor: pointer;">
                            Change
                        </button>
                    </div>
                    <div id="tg-change-result" style="padding: 8px; margin-top: 10px; border-radius: 4px;
                         display: none;"></div>
                </div>
                <div style="border-top: 1px solid #3d3d3d; padding-top: 15px;">
                    <div class="info-label" style="margin-bottom: 10px;">Quick Access:</div>
                    <div id="tg-quick-buttons" style="display: flex; flex-wrap: wrap; gap: 8px;">
                        <span style="color: #888;">Loading...</span>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Configuration Editor</h2>
                <div style="margin-bottom: 10px;">
                    <textarea id="config-editor" style="width: 100%; height: 200px; padding: 10px;
                             background: #1a1a1a; border: 1px solid #3d3d3d; color: #e0e0e0;
                             font-family: 'Courier New', monospace; font-size: 13px; border-radius: 4px;
                             resize: vertical;"
                             placeholder="Loading configuration..."></textarea>
                </div>
                <div style="display: flex; gap: 10px; margin-bottom: 10px;">
                    <button onclick="loadConfig()" style="padding: 8px 20px; background: #2196F3;
                            color: white; border: none; border-radius: 4px; cursor: pointer;">
                        Load
                    </button>
                    <button onclick="validateConfig()" style="padding: 8px 20px; background: #FFC107;
                            color: #1a1a1a; border: none; border-radius: 4px; cursor: pointer;">
                        Validate
                    </button>
                    <button onclick="saveConfig()" style="padding: 8px 20px; background: #4CAF50;
                            color: white; border: none; border-radius: 4px; cursor: pointer;">
                        Save
                    </button>
                </div>
                <div id="config-result" style="padding: 8px; border-radius: 4px; display: none;"></div>
            </div>
        </div>

        <div class="card">
            <h2>Recent Logs</h2>
            <div class="logs-container" id="logs-container"></div>
        </div>
    </div>

    <script>
        let ws;
        let reconnectInterval;

        function connect() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/api/ws`);

            ws.onopen = () => {
                console.log('WebSocket connected');
                clearInterval(reconnectInterval);
            };

            ws.onmessage = (event) => {
                const message = JSON.parse(event.data);

                if (message.type === 'status_update') {
                    updateStatus(message.data);
                } else if (message.type === 'log') {
                    addLog(message.data);
                } else if (message.type === 'ping') {
                    ws.send('pong');
                }
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected, reconnecting...');
                reconnectInterval = setInterval(connect, 5000);
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                ws.close();
            };
        }

        function updateStatus(status) {
            // Connection status
            const indicator = document.getElementById('status-indicator');
            const statusText = document.getElementById('status-text');

            indicator.className = 'status-indicator';
            if (status.state === 'connected') {
                indicator.classList.add('status-connected');
                statusText.textContent = 'Connected';
            } else if (status.state === 'authenticating') {
                indicator.classList.add('status-authenticating');
                statusText.textContent = 'Authenticating';
            } else {
                indicator.classList.add('status-disconnected');
                statusText.textContent = 'Disconnected';
            }

            // Current TG and DG-ID
            document.getElementById('current-tg').textContent = `TG ${status.current_tg}`;
            document.getElementById('current-dgid').textContent = status.current_dgid;

            // Client address
            document.getElementById('client-address').textContent =
                status.client_address || 'No client';

            // Uptime
            const uptimeSeconds = Math.floor(status.uptime_seconds);
            const hours = Math.floor(uptimeSeconds / 3600);
            const minutes = Math.floor((uptimeSeconds % 3600) / 60);
            const seconds = uptimeSeconds % 60;
            document.getElementById('uptime').textContent =
                `${hours}h ${minutes}m ${seconds}s`;

            // Statistics
            document.getElementById('bm-tx').textContent = status.bm_packets_sent;
            document.getElementById('bm-rx').textContent = status.bm_packets_received;
            document.getElementById('ysf-tx').textContent = status.ysf_packets_sent;
            document.getElementById('ysf-rx').textContent = status.ysf_packets_received;
            document.getElementById('pending-pings').textContent = status.pending_pings;

            // Last activity
            if (status.last_activity) {
                const date = new Date(status.last_activity);
                document.getElementById('last-activity').textContent =
                    date.toLocaleTimeString();
            } else {
                document.getElementById('last-activity').textContent = '-';
            }
        }

        function addLog(log) {
            const container = document.getElementById('logs-container');
            const entry = document.createElement('div');
            entry.className = 'log-entry';

            const timestamp = new Date(log.timestamp).toLocaleTimeString();
            entry.innerHTML = `
                <span class="log-timestamp">${timestamp}</span>
                <span class="log-level-${log.level}">[${log.level}]</span>
                <span class="log-message">${log.message}</span>
            `;

            container.appendChild(entry);
            container.scrollTop = container.scrollHeight;

            // Keep only last 100 logs in DOM
            while (container.children.length > 100) {
                container.removeChild(container.firstChild);
            }
        }

        // Initialize
        connect();

        // Fetch initial logs
        fetch('/api/logs')
            .then(response => response.json())
            .then(data => {
                data.logs.forEach(log => addLog(log));
            })
            .catch(error => console.error('Failed to fetch logs:', error));

        // TG Change functions
        async function changeTG(tg) {
            const resultDiv = document.getElementById('tg-change-result');
            resultDiv.style.display = 'none';

            try {
                const response = await fetch('/api/tg', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tg: tg })
                });

                const data = await response.json();

                resultDiv.style.display = 'block';
                if (response.ok) {
                    resultDiv.style.background = '#2d5016';
                    resultDiv.style.color = '#4CAF50';
                    resultDiv.textContent = data.message;
                } else {
                    resultDiv.style.background = '#5c1a1a';
                    resultDiv.style.color = '#f44336';
                    resultDiv.textContent = `Error: ${data.detail || data.message}`;
                }

                setTimeout(() => {
                    resultDiv.style.display = 'none';
                }, 5000);
            } catch (error) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#5c1a1a';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = `Error: ${error.message}`;
            }
        }

        function changeTGManual() {
            const input = document.getElementById('manual-tg-input');
            const tg = parseInt(input.value);

            if (!tg || tg < 1) {
                const resultDiv = document.getElementById('tg-change-result');
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#5c1a1a';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = 'Please enter a valid TG number';
                setTimeout(() => {
                    resultDiv.style.display = 'none';
                }, 3000);
                return;
            }

            changeTG(tg);
        }

        // Load TG mappings for quick buttons
        async function loadTGMappings() {
            try {
                const response = await fetch('/api/config');
                const data = await response.json();

                const tgMapping = data.config.talkgroups.dgid_to_tg || {};
                const buttonContainer = document.getElementById('tg-quick-buttons');

                if (Object.keys(tgMapping).length === 0) {
                    buttonContainer.innerHTML = '<span style="color: #888;">No TG mappings configured</span>';
                    return;
                }

                buttonContainer.innerHTML = '';
                const sortedEntries = Object.entries(tgMapping).sort((a, b) => a[1] - b[1]);

                sortedEntries.forEach(([dgid, tg]) => {
                    const button = document.createElement('button');
                    button.textContent = `TG ${tg}`;
                    button.title = `DG-ID ${dgid} → TG ${tg}`;
                    button.style.cssText = `
                        padding: 6px 12px;
                        background: #3d3d3d;
                        color: #e0e0e0;
                        border: 1px solid #4d4d4d;
                        border-radius: 4px;
                        cursor: pointer;
                        font-size: 13px;
                    `;
                    button.onmouseover = () => {
                        button.style.background = '#4CAF50';
                        button.style.borderColor = '#4CAF50';
                    };
                    button.onmouseout = () => {
                        button.style.background = '#3d3d3d';
                        button.style.borderColor = '#4d4d4d';
                    };
                    button.onclick = () => changeTG(tg);
                    buttonContainer.appendChild(button);
                });
            } catch (error) {
                console.error('Failed to load TG mappings:', error);
                document.getElementById('tg-quick-buttons').innerHTML =
                    '<span style="color: #f44336;">Failed to load TG mappings</span>';
            }
        }

        // Configuration Editor functions
        async function loadConfig() {
            const editor = document.getElementById('config-editor');
            const resultDiv = document.getElementById('config-result');
            resultDiv.style.display = 'none';

            try {
                const response = await fetch('/api/config');
                const data = await response.json();

                // Convert to YAML string
                editor.value = jsyaml.dump(data.config, {
                    indent: 2,
                    lineWidth: -1,
                    sortKeys: false
                });

                resultDiv.style.display = 'block';
                resultDiv.style.background = '#2d5016';
                resultDiv.style.color = '#4CAF50';
                resultDiv.textContent = 'Configuration loaded';
                setTimeout(() => {
                    resultDiv.style.display = 'none';
                }, 2000);
            } catch (error) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#5c1a1a';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = `Error loading config: ${error.message}`;
            }
        }

        async function validateConfig() {
            const editor = document.getElementById('config-editor');
            const resultDiv = document.getElementById('config-result');
            resultDiv.style.display = 'none';

            try {
                const configYaml = editor.value;
                const configObj = jsyaml.load(configYaml);

                const response = await fetch('/api/config/validate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config: configObj })
                });

                const data = await response.json();

                resultDiv.style.display = 'block';
                if (data.valid) {
                    resultDiv.style.background = '#2d5016';
                    resultDiv.style.color = '#4CAF50';
                    resultDiv.textContent = '✓ Configuration is valid';
                } else {
                    resultDiv.style.background = '#5c1a1a';
                    resultDiv.style.color = '#f44336';
                    resultDiv.textContent = `✗ Validation failed: ${data.message}`;
                }
            } catch (error) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#5c1a1a';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = `Error: ${error.message}`;
            }
        }

        async function saveConfig() {
            const editor = document.getElementById('config-editor');
            const resultDiv = document.getElementById('config-result');
            resultDiv.style.display = 'none';

            if (!confirm('Save configuration? The bridge will need to be restarted to apply changes.')) {
                return;
            }

            try {
                const configYaml = editor.value;
                const configObj = jsyaml.load(configYaml);

                const response = await fetch('/api/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config: configObj })
                });

                const data = await response.json();

                resultDiv.style.display = 'block';
                if (response.ok) {
                    resultDiv.style.background = '#5c3a00';
                    resultDiv.style.color = '#FFC107';
                    resultDiv.textContent = '⚠ ' + data.message;
                } else {
                    resultDiv.style.background = '#5c1a1a';
                    resultDiv.style.color = '#f44336';
                    resultDiv.textContent = `Error: ${data.detail || data.message}`;
                }
            } catch (error) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#5c1a1a';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = `Error: ${error.message}`;
            }
        }

        // Initialize TG mappings and config on load
        loadTGMappings();
        loadConfig();
    </script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js"></script>
</body>
</html>
'''

    async def run(self):
        """Run the web UI server."""
        logger.info(f"Starting Web UI server on http://{self.host}:{self.port}")

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,  # Disable access logs to avoid clutter
        )
        server = uvicorn.Server(config)
        await server.serve()

    def set_bridge(self, bridge):
        """Set bridge reference for monitoring.

        Args:
            bridge: YSFBMBridge instance
        """
        self.bridge = bridge

    def record_packet_sent(self, to: str):
        """Record packet sent.

        Args:
            to: "bm" or "ysf"
        """
        if to == "bm":
            self.stats["bm_packets_sent"] += 1
        elif to == "ysf":
            self.stats["ysf_packets_sent"] += 1

    def record_packet_received(self, from_: str):
        """Record packet received.

        Args:
            from_: "bm" or "ysf"
        """
        if from_ == "bm":
            self.stats["bm_packets_received"] += 1
        elif from_ == "ysf":
            self.stats["ysf_packets_received"] += 1
