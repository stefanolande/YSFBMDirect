"""Configuration dataclass and loader."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeConfig:
    """Configuration for YSF-BM bridge.

    This is an immutable value object representing validated configuration.
    """

    # Connection settings
    callsign: str
    bm_host: str
    bm_port: int
    bm_password: str
    ysf_port: int

    # Talk group settings
    default_tg: int
    default_dgid: int
    back_to_default_seconds: int  # seconds (0 = disabled)
    show_dgid_callsign: bool
    tg_change_silence_period: float  # seconds
    dgid_to_tg: dict[int, int]  # DG-ID -> TG mapping

    # Web UI settings (optional)
    webui_enabled: bool = False
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080

    @property
    def tg_to_dgid(self) -> dict[int, int]:
        """Reverse mapping: TG -> DG-ID."""
        return {tg: dgid for dgid, tg in self.dgid_to_tg.items()}

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Check that default TG exists in mapping
        if self.default_tg not in self.tg_to_dgid:
            raise ValueError(
                f"Default TG {self.default_tg} not found in DGID-TO-TG mapping"
            )

        # Check for duplicate TGs (same TG mapped to multiple DG-IDs)
        tg_counts: dict[int, int] = {}
        for dgid, tg in self.dgid_to_tg.items():
            tg_counts[tg] = tg_counts.get(tg, 0) + 1

        duplicates = [tg for tg, count in tg_counts.items() if count > 1]
        if duplicates:
            raise ValueError(
                f"Duplicate TGs in mapping: {duplicates}. "
                f"Each TG can only map to one DG-ID."
            )

        # Validate ports
        if not (1 <= self.bm_port <= 65535):
            raise ValueError(f"Invalid BM port: {self.bm_port}")
        if not (1 <= self.ysf_port <= 65535):
            raise ValueError(f"Invalid YSF port: {self.ysf_port}")

        # Validate callsign
        if not self.callsign or len(self.callsign) > 10:
            raise ValueError(f"Invalid callsign: {self.callsign}")
