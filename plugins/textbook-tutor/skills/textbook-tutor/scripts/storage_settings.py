"""Local storage preference; call set only after the user selects a directory."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/textbook-tutor/settings.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show")
    setter = sub.add_parser("set")
    setter.add_argument("--directory", required=True)
    args = parser.parse_args()
    config = args.config.expanduser()
    data = json.loads(config.read_text()) if config.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")
    if data and (not isinstance(data.get("lessons_dir"), str) or not Path(data["lessons_dir"]).is_absolute()):
        raise ValueError("Invalid saved directory; existing settings were not changed")
    if args.action == "show":
        directory = Path(data["lessons_dir"]) if data else None
        print(json.dumps({"configured": bool(data), "settings": data, "available": bool(directory and directory.is_dir() and os.access(directory, os.W_OK))}, ensure_ascii=False))
        return
    directory = Path(args.directory).expanduser()
    if not directory.is_absolute():
        raise ValueError("Choose an absolute directory path")
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b"storage check")
        probe.flush()
    data.update(schema_version=1, lessons_dir=str(directory), updated_at=datetime.now(timezone.utc).isoformat())
    config.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.parent, delete=False) as handle:
            temp = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, config)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    assert json.loads(config.read_text()) == data
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
