"""Read only Compose project labels and HostConfig.PortBindings, including stopped containers."""
import json
import sys

RESERVED = {"80", "3044", "3045", "3046"}


def check(lines) -> None:
    for line in lines:
        project, encoded = line.rstrip("\n").split("|", 1)
        bindings = json.loads(encoded)
        if project == "jidan-demo":
            continue
        if bindings is not None and not isinstance(bindings, dict):
            raise ValueError("invalid container port metadata")
        for ports in (bindings or {}).values():
            for binding in ports or []:
                if binding.get("HostPort") in RESERVED:
                    raise ValueError("a reserved demo port belongs to another container")


if __name__ == "__main__":
    check(sys.stdin)
