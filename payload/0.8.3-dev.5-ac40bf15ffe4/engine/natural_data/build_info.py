"""One content fingerprint shared by packaging and the running plugin."""
import hashlib
import json
from pathlib import Path

METADATA = 'build-info.json'


def current_version():
    metadata=Path(__file__).resolve().parents[2]/METADATA
    return json.loads(metadata.read_text())['version'] if metadata.is_file() else 'development'


def fingerprint(root, directories=None):
    root = Path(root)
    paths = (p for directory in directories for p in (root / directory).rglob('*')) if directories else root.rglob('*')
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(root)
        if (not path.is_file() or relative.as_posix() == METADATA or
                {'__pycache__', 'node_modules', '.git'} & set(relative.parts) or path.suffix == '.pyc'):
            continue
        digest.update(relative.as_posix().encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def current_build_id():
    root = Path(__file__).resolve().parents[2]
    metadata = root / METADATA
    if metadata.is_file():
        value = json.loads(metadata.read_text())
        build_id = value.get('build_id', '')
        if value.get('schema_version') != 1 or len(build_id) != 12 or any(c not in '0123456789abcdef' for c in build_id):
            raise ValueError('插件构建信息无效，请重新构建交付包。')
        return build_id
    # A development checkout has no release manifest. Include JS, analysis and
    # other engine modules too, never private runtime data or credentials.
    return fingerprint(root, ['engine', 'knowledge', 'packages/natural-data',
                             'packages/natural-capabilities', 'packages/capability-sdk', 'packages/ask-data-plugin'])
