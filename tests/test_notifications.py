"""Offline checks for the notification edit. No Home Assistant, no network:

    python tests/test_notifications.py

The service adds "Notify Surveillance Center" to a trigger that only records.
It edits the device's own XML rather than rebuilding it, because rebuilding is
what broke it: the rebuild dropped the <dynVideoInputID> the firmware attaches
to a record notification and renumbered the new entry, and the NVR answered
HTTP 400 to every trigger type except motion.

So the property under test is narrow and absolute: everything the device sent
must come back byte for byte, with exactly one block added.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hikvision_nvr"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Captured from a DS-7608NI-K2/8P on V4.40.015. Note dynVideoInputID, which an
# earlier version of this code dropped.
REAL = """<?xml version="1.0" encoding="UTF-8" ?>
<EventTriggerNotificationList version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
<EventTriggerNotification>
<id>record-3</id>
<notificationMethod>record</notificationMethod>
<dynVideoInputID>3</dynVideoInputID>
</EventTriggerNotification>
</EventTriggerNotificationList>"""

# The same device once the linkage exists.
REAL_WITH_CENTER = """<?xml version="1.0" encoding="UTF-8" ?>
<EventTriggerNotificationList version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
<EventTriggerNotification>
<id>record-3</id>
<notificationMethod>record</notificationMethod>
<dynVideoInputID>3</dynVideoInputID>
</EventTriggerNotification>
<EventTriggerNotification>
<id>center</id>
<notificationMethod>center</notificationMethod>
</EventTriggerNotification>
</EventTriggerNotificationList>"""

# Older firmwares use the hikvision.com schema and carry fields this
# integration has never heard of.
LEGACY = """<?xml version="1.0" encoding="UTF-8"?>
<EventTriggerNotificationList version="1.0" xmlns="http://www.hikvision.com/ver10/XMLSchema">
<EventTriggerNotification>
<id>beep</id>
<notificationMethod>beep</notificationMethod>
<notificationRecurrence>beginningandend</notificationRecurrence>
</EventTriggerNotification>
<EventTriggerNotification>
<id>record</id>
<notificationMethod>record</notificationMethod>
<notificationRecurrence>beginning</notificationRecurrence>
<dynVideoInputID>1</dynVideoInputID>
<extraField>keep me</extraField>
</EventTriggerNotification>
</EventTriggerNotificationList>"""

# Some devices emit an explicit namespace prefix.
PREFIXED = """<?xml version="1.0" encoding="UTF-8"?>
<isapi:EventTriggerNotificationList xmlns:isapi="http://www.isapi.org/ver20/XMLSchema">
<isapi:EventTriggerNotification>
<isapi:id>record-1</isapi:id>
<isapi:notificationMethod>record</isapi:notificationMethod>
</isapi:EventTriggerNotification>
</isapi:EventTriggerNotificationList>"""

# A trigger with nothing linked at all.
EMPTY = """<?xml version="1.0" encoding="UTF-8" ?>
<EventTriggerNotificationList version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
</EventTriggerNotificationList>"""

# Whitespace inside the closing tag is legal XML.
LOOSE_CLOSE = REAL.replace(
    "</EventTriggerNotificationList>", "</EventTriggerNotificationList >"
)

NOT_A_LIST = """<?xml version="1.0" encoding="UTF-8" ?>
<ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
<statusCode>4</statusCode>
</ResponseStatus>"""


def check_preserves_everything(isapi, document: str, label: str) -> None:
    """The device's own bytes must survive, in order, with one block added."""
    updated = isapi.with_center_notification(document)
    assert updated is not None, f"{label}: refused a document it should edit"

    # Every original character is still there, in the original order.
    stripped = updated.replace(
        _added_block(isapi, document, updated), "", 1
    )
    assert stripped == document, f"{label}: the original document was altered"

    # Exactly one center linkage, and one more notification than before.
    assert updated.count("<notificationMethod>center<") + updated.count(
        ":notificationMethod>center<"
    ) == 1, f"{label}: expected exactly one center notification"
    before = len(re.findall(r"<(?:[\w.-]+:)?EventTriggerNotification\b", document))
    after = len(re.findall(r"<(?:[\w.-]+:)?EventTriggerNotification\b", updated))
    assert after == before + 1, f"{label}: added {after - before} blocks, expected 1"

    # Idempotent: the result needs no further change.
    assert isapi.has_center_notification(updated), f"{label}: center not detected after adding"
    assert (
        isapi.with_center_notification(updated) is None
    ), f"{label}: a second pass would edit it again"


def _added_block(isapi, document: str, updated: str) -> str:
    """The text the edit inserted, derived by difference."""
    head = 0
    while head < len(document) and document[head] == updated[head]:
        head += 1
    tail = 0
    while tail < len(document) - head and document[-1 - tail] == updated[-1 - tail]:
        tail += 1
    return updated[head : len(updated) - tail]


class _Node:
    """The parts of an ElementTree node that _matches uses."""

    def __init__(self, trigger_id: str, event_type: str) -> None:
        self._fields = {"id": trigger_id, "eventType": event_type}

    def find(self, path: str):
        if path in self._fields:
            return _Text(self._fields[path])
        return None


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


def main() -> None:
    isapi = _load("isapi")

    for label, document in (
        ("reference NVR", REAL),
        ("legacy schema", LEGACY),
        ("namespace prefix", PREFIXED),
        ("no linkages", EMPTY),
        ("loose closing tag", LOOSE_CLOSE),
    ):
        check_preserves_everything(isapi, document, label)
        print(f"preserves      : {label}")

    # dynVideoInputID and any other unknown field must survive verbatim.
    updated = isapi.with_center_notification(LEGACY)
    for field in ("<dynVideoInputID>1</dynVideoInputID>", "<extraField>keep me</extraField>",
                  "<notificationRecurrence>beginningandend</notificationRecurrence>"):
        assert field in updated, f"lost unknown field: {field}"
    print("preserves      : dynVideoInputID, notificationRecurrence, unknown fields")

    # The inserted block inherits the document's namespace prefix.
    prefixed = isapi.with_center_notification(PREFIXED)
    assert "<isapi:notificationMethod>center</isapi:notificationMethod>" in prefixed
    assert "<notificationMethod>" not in prefixed, "mixed prefixed and bare tags"
    print("namespace      : inserted block matches the document's prefix")

    # Already linked: nothing to do, and nothing done.
    assert isapi.has_center_notification(REAL_WITH_CENTER)
    assert isapi.with_center_notification(REAL_WITH_CENTER) is None
    print("idempotent     : a document with center is left untouched")

    # Not a notification list: refuse rather than guess.
    assert isapi.with_center_notification(NOT_A_LIST) is None
    assert isapi.with_center_notification("") is None
    print("refuses        : documents that are not notification lists")

    # --------------------------------------------------------------- scoping
    match = isapi.HikvisionISAPI._matches
    triggers = [
        _Node("linedetection-3", "linedetection"),
        _Node("VMD-3", "VMD"),
        _Node("fielddetection-3", "fielddetection"),
        _Node("tamper-3", "tamper"),          # aliased to tamperdetection
        _Node("diskfull", "diskfull"),        # device level, no channel suffix
    ]

    def scoped(event_types=None, channels=None):
        return [
            node._fields["id"]
            for node in triggers
            if match(None, node, event_types, channels)
        ]

    assert scoped({"linedetection"}) == ["linedetection-3"], scoped({"linedetection"})
    assert scoped({"LineDetection"}) == ["linedetection-3"], "filter must ignore case"
    # An alias must match under either name, and still reach nothing else.
    assert scoped({"tamperdetection"}) == ["tamper-3"]
    assert scoped({"tamper"}) == ["tamper-3"]
    assert scoped({"nosuchevent"}) == []
    assert scoped(channels={3}) == [
        "linedetection-3", "VMD-3", "fielddetection-3", "tamper-3"
    ], "channel filter must exclude device-level triggers"
    assert scoped({"linedetection"}, {4}) == [], "filters must both apply"
    assert scoped() == [node._fields["id"] for node in triggers], "no filter means all"
    print("scoping        : a filter reaches only what it names, alias and case aware")

    print("ALL NOTIFICATION CHECKS PASSED")


if __name__ == "__main__":
    main()
