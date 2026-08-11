"""Offline checks on service registration. No Home Assistant, no network:

    python tests/test_services.py

Exists because a bad edit once produced

    hass.services.async_register(DOMAIN, SERVICE_A, SERVICE_B, func, schema=...)

which is valid Python, passes lint and byte-compiles, and only fails at runtime
with "got multiple values for argument 'schema'" -- taking the whole integration
down with it. Parsing the call shape catches that before it ships.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import yaml

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "hikvision_nvr"


def main() -> None:
    source = (COMPONENT / "services.py").read_text(encoding="utf-8")

    registered: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_register"
        ):
            continue
        assert len(node.args) == 3, (
            "async_register takes (domain, service, func) positionally; line "
            f"{node.lineno} passes {len(node.args)}"
        )
        name = node.args[1]
        assert isinstance(name, ast.Name), f"line {node.lineno}: service name is not a constant"
        registered.append(name.id)
        keywords = {kw.arg for kw in node.keywords}
        assert "schema" in keywords, f"{name.id} is registered without a schema"

    assert registered, "no services registered at all"
    assert len(registered) == len(set(registered)), f"registered twice: {registered}"

    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    names = set()
    for constant in registered:
        match = re.search(rf'^{constant} = "(.*?)"', const, re.MULTILINE)
        assert match, f"{constant} is not defined in const.py"
        names.add(match.group(1))

    in_yaml = set(yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8")))
    in_strings = set(
        json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["services"]
    )

    assert names == in_yaml, f"services.yaml disagrees: {sorted(names ^ in_yaml)}"
    assert names == in_strings, f"strings.json disagrees: {sorted(names ^ in_strings)}"

    print(f"services: {len(names)} registered, described and translated")
    for name in sorted(names):
        print(f"   {name}")
    print("ALL SERVICE CHECKS PASSED")


if __name__ == "__main__":
    main()
