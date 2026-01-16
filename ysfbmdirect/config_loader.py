"""Configuration file loader for YAML format."""

import logging
from pathlib import Path

import yaml

from .config import BridgeConfig

logger = logging.getLogger(__name__)


def load_config(file_path: str) -> BridgeConfig:
    """Load configuration from YAML file.

    Args:
        file_path: Path to YAML config file (.yaml or .yml)

    Returns:
        BridgeConfig object with validated settings

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid or format unknown
        yaml.YAMLError: If YAML parsing fails
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in (".yaml", ".yml"):
        raise ValueError(
            f"Invalid config file format: {suffix}. "
            f"Only YAML format (.yaml, .yml) is supported."
        )

    logger.info(f"Loading configuration from {file_path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"Config file is empty: {file_path}")

    # Extract configuration sections
    try:
        connection = data["connection"]
        talkgroups = data["talkgroups"]
    except KeyError as e:
        raise ValueError(f"Missing required config section: {e}")

    # Parse DGID-TO-TG mapping
    dgid_to_tg_map = talkgroups.get("dgid_to_tg", {})
    if not isinstance(dgid_to_tg_map, dict):
        raise ValueError("dgid_to_tg must be a dictionary mapping DG-ID to TG")

    # Convert keys to integers
    dgid_to_tg = {int(k): int(v) for k, v in dgid_to_tg_map.items()}

    # Web UI settings (optional)
    webui = data.get("webui", {})

    # Create config object
    config = BridgeConfig(
        callsign=connection["callsign"],
        bm_password=connection["bm_password"],
        bm_host=connection.get("bm_host", "44.131.4.1"),
        bm_port=int(connection.get("bm_port", 42000)),
        ysf_port=int(connection.get("ysf_port", 42002)),
        default_tg=int(talkgroups["default_tg"]),
        default_dgid=int(talkgroups.get("default_dgid", 0)),
        dgid_to_tg=dgid_to_tg,
        back_to_default_seconds=int(talkgroups.get("back_to_default_seconds", 0)),
        show_dgid_callsign=bool(talkgroups.get("show_dgid_callsign", False)),
        tg_change_silence_period=float(talkgroups.get("tg_change_silence_period", 0)),
        webui_enabled=bool(webui.get("enabled", False)),
        webui_host=webui.get("host", "0.0.0.0"),
        webui_port=int(webui.get("port", 8080)),
    )

    # Validate configuration
    config.validate()

    logger.info(
        f"Configuration loaded: {config.callsign} -> "
        f"BM {config.bm_host}:{config.bm_port}, "
        f"YSF port {config.ysf_port}, "
        f"Default TG {config.default_tg}"
    )

    return config
