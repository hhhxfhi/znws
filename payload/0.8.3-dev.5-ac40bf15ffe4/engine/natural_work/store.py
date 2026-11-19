"""Project-scoped immutable artifacts and compare-and-swap view state."""
import hashlib
import json
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


class ConflictError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS artifacts (
                    ref TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
                    schema_version INTEGER NOT NULL, producer TEXT NOT NULL,
                    parents TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                    created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS artifacts_project ON artifacts(project, kind);
                CREATE TABLE IF NOT EXISTS artifact_heads (
                    project TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL,
                    revision INTEGER NOT NULL, ref TEXT NOT NULL,
                    PRIMARY KEY(project, subject, kind));
                CREATE TABLE IF NOT EXISTS capability_calls (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT NOT NULL,
                    operation TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL,
                    status TEXT NOT NULL, output TEXT, created_at REAL NOT NULL, finished_at REAL,
                    UNIQUE(project, operation, request_key));
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _put_with_digest(self, project, kind, payload, *, producer, parents=(), ref=None, connection=None):
        if connection is None:
            with self.connect() as c:
                return self._put_with_digest(project, kind, payload, producer=producer, parents=parents, ref=ref, connection=c)
        for parent in parents:
            if not connection.execute('SELECT 1 FROM artifacts WHERE project=? AND ref=?', (project, parent)).fetchone():
                raise PermissionError('产物不存在或不属于当前项目。')
        body = encode(payload)
        digest = hashlib.sha256(body.encode()).hexdigest()
        if ref is not None:
            existing = connection.execute('SELECT kind,digest,parents,producer FROM artifacts WHERE project=? AND ref=?', (project, ref)).fetchone()
            if existing:
                if (existing[0], existing[1], json.loads(existing[2]), existing[3]) != (kind, digest, list(parents), producer):
                    raise ConflictError('产物引用已存在，不能覆盖不可变产物。')
                return ref, digest
        ref = ref or uuid.uuid4().hex
        cursor = connection.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)',
                                    (ref, project, kind, 1, producer, encode(list(parents)), body, digest, time.time()))
        if cursor.rowcount == 0:
            row = connection.execute('SELECT kind,digest,parents,producer FROM artifacts WHERE project=? AND ref=?', (project, ref)).fetchone()
            if not row:
                raise PermissionError('产物不存在或不属于当前项目。')
            if (row[0], row[1], json.loads(row[2]), row[3]) != (kind, digest, list(parents), producer):
                raise ConflictError('产物引用已存在，不能覆盖不可变产物。')
        return ref, digest

    def put(self, project, kind, payload, *, producer, parents=(), ref=None, connection=None):
        ref, _ = self._put_with_digest(project, kind, payload, producer=producer, parents=parents, ref=ref, connection=connection)
        return ref

    def put_recent(self, project, kind, payload, *, producer, max_age):
        """Reuse an identical artifact created within max_age seconds; else put.

        Returns (ref, age_seconds). Artifacts are immutable, so an identical
        digest is the same content; reuse keeps paged reads from growing storage.
        """
        body = encode(payload)
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = time.time()
        with self.connect() as c:
            row = c.execute('SELECT ref, created_at FROM artifacts WHERE project=? AND kind=? AND digest=? AND producer=? AND parents=? AND created_at>=? '
                            'ORDER BY created_at DESC LIMIT 1', (project, kind, digest, producer, encode([]), now - max_age)).fetchone()
            if row:
                return row[0], now - row[1]
            return self.put(project, kind, payload, producer=producer, connection=c), 0.0

    def get(self, project, ref, *, connection=None):
        if connection is None:
            with self.connect() as c:
                return self.get(project, ref, connection=c)
        row = connection.execute('SELECT kind,schema_version,producer,parents,payload,digest,created_at FROM artifacts WHERE project=? AND ref=?', (project, ref)).fetchone()
        if not row:
            raise PermissionError('产物不存在或不属于当前项目。')
        return dict(ref=ref, kind=row[0], schema_version=row[1], producer=row[2], parents=json.loads(row[3]), payload=json.loads(row[4]), digest=row[5], created_at=row[6])

    def head(self, project, subject, kind, *, connection=None):
        if connection is None:
            with self.connect() as c:
                return self.head(project, subject, kind, connection=c)
        row = connection.execute('SELECT revision,ref FROM artifact_heads WHERE project=? AND subject=? AND kind=?', (project, subject, kind)).fetchone()
        return (row[0], self.get(project, row[1], connection=connection)) if row else (0, None)

    def publish(self, project, subject, kind, payload, *, expected_revision, producer, parents):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision FROM artifact_heads WHERE project=? AND subject=? AND kind=?', (project, subject, kind)).fetchone()
            revision = row[0] if row else 0
            if expected_revision != revision:
                raise ConflictError('结果视图已更新，请刷新后重试；本次操作未覆盖新版本。')
            ref = self.put(project, kind, payload, producer=producer, parents=parents, connection=c)
            c.execute('INSERT INTO artifact_heads VALUES (?,?,?,?,?) ON CONFLICT(project,subject,kind) DO UPDATE SET revision=excluded.revision,ref=excluded.ref', (project, subject, kind, revision + 1, ref))
            return ref, revision + 1
