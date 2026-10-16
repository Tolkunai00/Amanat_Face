"""Download a vetted PAD checkpoint and verify its pinned provenance.

This command intentionally supports only reviewed catalog entries. It never
discovers or executes arbitrary URLs.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen


CANDIDATES = {
    "anti_spoof_mn3": {
        "url": "https://storage.openvinotoolkit.org/repositories/open_model_zoo/public/2022.1/anti-spoof-mn3/anti-spoof-mn3.onnx",
        "filename": "anti-spoof-mn3.onnx",
        "size": 12_270_179,
        "sha256": "c4c99af04603b62d7e44f6f4daeb33e0daeccc696008c0b1d62f6f5cebbb3262",
        "sha384": "6de4534964b723397b3e8c995cadcf43bc007cc2f9930b95ae25f76adccece5d1d4d058d0b15117b9e4a9f758424f92a",
        "license": "MIT",
    },
}


def candidate(name: str) -> dict:
    try:
        return CANDIDATES[name]
    except KeyError as exc:
        raise ValueError(f"unsupported PAD candidate: {name}") from exc


def validate_download(path: Path, metadata: dict) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"checkpoint not found: {path}")
    raw = path.read_bytes()
    actual = {
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sha384": hashlib.sha384(raw).hexdigest(),
    }
    for key in actual:
        if actual[key] != metadata[key]:
            raise ValueError(f"checkpoint {key} mismatch")
    return actual


def fetch(name: str, destination: Path) -> dict:
    metadata = candidate(name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        with urlopen(metadata["url"], timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    try:
        result = validate_download(temporary, metadata)
        temporary.replace(destination)
        return {"path": str(destination.resolve()), **result}
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=sorted(CANDIDATES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    output = args.output or root / "deploy/models" / candidate(args.name)["filename"]
    result = fetch(args.name, output)
    print(f"PASS {args.name} path={result['path']} size={result['size']} sha256={result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
