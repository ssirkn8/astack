"""Project-owned fixture, not a generic data mover or installed storage framework."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path


def verified(path: Path, checksum: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Required retained input is unavailable or unsafe')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Frozen input identity mismatch')
    return data


def producer_identity() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def from_raw(raw_root: Path, sources: dict) -> bytes:
    data = verified(raw_root / 'measurements.csv', sources['raw']['raw_checksum'])
    values = [Decimal(line) for line in data.decode().splitlines()[1:]]
    product = {
        'mean': str(sum(values) / len(values)),
        'derivation': {
            'input_sha256': sources['raw']['raw_checksum'],
            'producer_sha256': producer_identity(),
            'operation': 'decimal-mean-of-value-column-v1',
        },
    }
    return json.dumps(product, sort_keys=True).encode()


def render_retained(product_root: Path, sources: dict) -> bytes:
    data = verified(product_root / 'summary.json', sources['retained']['raw_checksum'])
    product = json.loads(data)
    expected = sources['retained']['derivation']
    if (product['derivation'] != expected
            or expected['input_sha256'] != sources['raw']['raw_checksum']
            or expected['producer_sha256'] != producer_identity()
            or expected['operation'] != 'decimal-mean-of-value-column-v1'):
        raise ValueError('Retained product derivation is not verified')
    return ('mean\n' + product['mean'] + '\n').encode()
