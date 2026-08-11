"""Offline checks. No Home Assistant, no network:

    python tests/test_translations.py

Entities set a translation_key derived from the event type. A key with no
matching entry renders as a nameless entity, which is easy to miss by eye and
tedious to spot in the UI -- so assert the coupling instead.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hikvision_nvr"


def _load(name: str):
    """Import one module by path, so Home Assistant is never needed."""
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    const = _load("const")
    isapi = _load("isapi")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert strings == english, "strings.json and translations/en.json have diverged"

    declared = set(strings["entity"]["binary_sensor"])
    needed = {event.lower() for event in const.EVENT_CLASSES} | {"connectivity"}
    missing = needed - declared
    assert not missing, f"binary_sensor translations missing: {sorted(missing)}"
    unused = declared - needed
    assert not unused, f"binary_sensor translations for unknown events: {sorted(unused)}"
    print(f"binary_sensor : {len(needed)} keys, all present")

    for platform in ("sensor",):
        if platform not in strings["entity"]:
            continue
        print(f"{platform:14}: {len(strings['entity'][platform])} keys")

    # Every event the sensible-default set names must be a real event type.
    unknown = const.DEFAULT_ENABLED_EVENTS - set(const.EVENT_CLASSES)
    assert not unknown, f"DEFAULT_ENABLED_EVENTS names unknown events: {unknown}"

    # Aliases must map onto event types we know how to present.
    bad = {
        source: target
        for source, target in isapi.EVENT_ALIASES.items()
        if target not in const.EVENT_CLASSES
    }
    assert not bad, f"EVENT_ALIASES point at unpresentable events: {bad}"

    # Device-level events must be presentable too; they become NVR entities.
    unpresentable = isapi.DEVICE_EVENTS - set(const.EVENT_CLASSES)
    assert not unpresentable, f"DEVICE_EVENTS not presentable: {sorted(unpresentable)}"

    print("ALL TRANSLATION CHECKS PASSED")


if __name__ == "__main__":
    main()
