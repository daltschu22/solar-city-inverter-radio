"""Installation settings. Importing offline decoders never opens a radio socket."""

from dataclasses import dataclass
import os
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


ENV_FIELDS = {
    "host": "SOLAR_RADIO_HOST",
    "port": "SOLAR_RADIO_PORT",
    "channel": "SOLAR_RADIO_CHANNEL",
    "pan_id": "SOLAR_PAN_ID",
    "extended_pan_id": "SOLAR_EXTENDED_PAN_ID",
    "collector_eui": "SOLAR_COLLECTOR_EUI",
    "inverter_eui": "SOLAR_INVERTER_EUI",
    "initial_address": "SOLAR_INITIAL_ADDRESS",
}


def load_config():
    if "SOLAR_CONFIG" in os.environ:
        raise ValueError("Unsupported setting SOLAR_CONFIG; unset it and supply the radio environment variables; see docs/setup.md#environment-variables")
    required = {"host", "channel", "pan_id", "extended_pan_id", "collector_eui", "inverter_eui"}
    data = {key: os.environ[name] for key, name in ENV_FIELDS.items() if name in os.environ}
    if not data:
        return RadioConfig()
    if required - data.keys():
        missing = ", ".join(sorted(ENV_FIELDS[key] for key in required - data.keys()))
        raise ValueError(f"Missing radio environment variables: {missing}; see config.example.env")
    if not data["host"].strip():
        raise ValueError("SOLAR_RADIO_HOST is required")

    def number(key, default, minimum, maximum):
        value = data.get(key, default)
        try:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError()
            value = int(value, 0) if isinstance(value, str) else value
        except ValueError as exc:
            raise ValueError(f"{ENV_FIELDS[key]} must be an integer or a 0x-prefixed hex string") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{ENV_FIELDS[key]} is outside its valid range")
        return value

    def eui(key):
        value = data[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", value):
            raise ValueError(f"{ENV_FIELDS[key]} must contain exactly 16 hexadecimal digits")
        if int(value, 16) in (0, 0xFFFFFFFFFFFFFFFF) or int(value[:2], 16) & 1:
            raise ValueError(f"{ENV_FIELDS[key]} must be a unicast device identity")
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
        raise ValueError("Set the radio environment variables; load .env with uv run --env-file .env or Docker --env-file .env; see docs/setup.md")
    return CONFIG


if __name__ == "__main__":
    require_configured()
    print("Radio configuration is valid. No radio connection was opened.")
