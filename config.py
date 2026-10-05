"""Installation settings. Importing offline decoders never opens a radio socket."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class RadioConfig:
    # Synthetic identities for offline packet construction and unit tests only.
    host: str = ""
    port: int = 6638
    channel: int = 14
    pan_id: int = 0x1234
    extended_pan_id: int = 0x1122334455667788
    collector_eui: str = "0200000000000001"
    inverter_eui: str = "0200000000000002"
    initial_address: int = 0x2345
    configured: bool = False


def load_config(path=None):
    explicit = path is not None or "SOLAR_CONFIG" in os.environ
    path = Path(path or os.environ.get("SOLAR_CONFIG", "radio.local.json"))
    if not path.exists():
        if explicit:
            raise ValueError("SOLAR_CONFIG file does not exist")
        return RadioConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot read radio configuration JSON") from exc
    required = {"host", "channel", "pan_id", "extended_pan_id", "collector_eui", "inverter_eui"}
    optional = {"port", "initial_address"}
    if not isinstance(data, dict) or required - data.keys() or data.keys() - required - optional:
        raise ValueError("Radio configuration has missing or unknown fields; see config.example.json")
    if not isinstance(data["host"], str) or not data["host"].strip():
        raise ValueError("Radio host is required")

    def number(key, default, minimum, maximum):
        value = data.get(key, default)
        try:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError()
            value = int(value, 0) if isinstance(value, str) else value
        except ValueError as exc:
            raise ValueError(f"{key} must be an integer or a 0x-prefixed hex string") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} is outside its valid range")
        return value

    def eui(key):
        value = data[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", value):
            raise ValueError(f"{key} must contain exactly 16 hexadecimal digits")
        if int(value, 16) in (0, 0xFFFFFFFFFFFFFFFF) or int(value[:2], 16) & 1:
            raise ValueError(f"{key} must be a unicast device identity")
        return value.lower()

    collector, inverter = eui("collector_eui"), eui("inverter_eui")
    if collector == inverter:
        raise ValueError("Collector and inverter identities must differ")
    return RadioConfig(
        host=data["host"].strip(), port=number("port", 6638, 1, 65535),
        channel=number("channel", None, 11, 26),
        pan_id=number("pan_id", None, 0, 0xFFFE),
        extended_pan_id=number("extended_pan_id", None, 1, 0xFFFFFFFFFFFFFFFE),
        collector_eui=collector, inverter_eui=inverter,
        initial_address=number("initial_address", 0x2345, 1, 0xFFF7),
        configured=True,
    )


CONFIG = load_config()


def require_configured():
    if not CONFIG.configured:
        raise ValueError("Create radio.local.json from config.example.json and set your radio parameters")
    return CONFIG


if __name__ == "__main__":
    require_configured()
    print("Radio configuration is valid. No radio connection was opened.")
