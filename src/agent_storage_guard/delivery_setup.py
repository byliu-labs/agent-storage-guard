"""Explicit interactive setup: private inputs go only to a named Keychain item."""
from __future__ import annotations

import getpass
import json
from pathlib import Path
import sys
import warnings


def _config(directory: Path, roots: list[Path] | None) -> dict:
    from . import monitor_roots, ownership
    config = ownership.read_json(directory / "config.json")
    if config is not None and roots is not None:
        raise RuntimeError("setup_existing_roots; omit --root to preserve configured roots")
    if config is None:
        config = {**monitor_roots.defaults(roots), "destinations": []}
    return monitor_roots.validate(config)


def _destination(config: dict, provider: str, destination: str) -> dict:
    from . import delivery
    spec = {"id": destination, "provider": provider,
            "settings_keychain": f"asg-{provider}-{destination}"}
    if any(entry["id"] == destination for entry in delivery.destinations(config)):
        raise RuntimeError("setup_destination_exists; choose a new --destination")
    candidate = {**config, "destinations": [*delivery.destinations(config), spec]}
    try:
        delivery.validate_config(candidate)
    except ValueError:
        raise RuntimeError("setup_invalid_destination") from None
    return candidate


def _settings(provider: str) -> dict:
    from .delivery_transports import check, DeliveryError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            if provider == "feishu":
                values = {"webhook_url": getpass.getpass("Feishu/Lark webhook (hidden): "),
                          "signing_secret": getpass.getpass("Signing secret (hidden, required): ")}
                if not values["signing_secret"]:
                    raise RuntimeError("setup_invalid_settings")
            else:
                values = {"recipient": getpass.getpass("iMessage recipient (hidden): ")}
        check(provider, values)
    except (DeliveryError, ValueError):
        raise RuntimeError("setup_invalid_settings") from None
    except (getpass.GetPassWarning, EOFError):
        raise RuntimeError("setup_terminal_required") from None
    return values


def configure(directory: Path, provider: str, destination: str,
              roots: list[Path] | None = None) -> dict:
    from . import delivery_credentials, monitor, ownership
    from .monitor_store import MonitorStore
    directory = Path(directory).absolute()
    resolved = directory.resolve()
    if any((parent / ".git").exists() or (parent / ".git").is_symlink()
           for parent in (resolved, *resolved.parents)):
        raise RuntimeError("setup_state_must_be_outside_git")
    if sys.platform != "darwin":
        raise RuntimeError("setup_macos_required")
    if provider not in {"feishu", "imessage"}:
        raise RuntimeError("setup_unsupported_provider")
    if not sys.stdin.isatty():
        raise RuntimeError("setup_terminal_required")
    _destination(_config(directory, roots), provider, destination)
    values = _settings(provider)
    ownership.private_directory(directory)
    with monitor.locked(directory, "policy.lock"):
        # Recheck after prompting: another setup may have installed this ID.
        candidate = _destination(_config(directory, roots), provider, destination)
        try:
            delivery_credentials.write_keychain(f"asg-{provider}-{destination}", json.dumps(values))
        except RuntimeError as error:
            if str(error) == "keychain_item_exists":
                raise RuntimeError("setup_keychain_item_exists; choose a new --destination") from None
            raise RuntimeError("setup_keychain_unavailable; unlock or authorize the existing Keychain manually") from None
        with MonitorStore(directory):
            ownership.write_json(directory / "config.json", candidate)
    return {"destination": destination, "provider": provider, "credential_stored": True,
            "next_commands": ["monitor delivery-check", "monitor install",
                              f"monitor test-send --destination {destination} --installed",
                              "monitor install --activate"]}
