"""SQLite identity store. Passwords use scrypt; only session digests persist."""
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import uuid4


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def password_matches(password: str, encoded: str) -> bool:
    return hmac.compare_digest(password_hash(password, encoded.split('$')[1]), encoded)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    def __init__(self, path: Path):
        self.path = path
        self.dummy_password = password_hash(secrets.token_urlsafe(32))
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE,
                    password TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS logins (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS model_defaults (user_id TEXT PRIMARY KEY, credential_id TEXT NOT NULL,
                    model TEXT NOT NULL, base_url TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, username: str, password: str) -> dict:
        user = {'id': 'user-' + uuid4().hex, 'username': username.lower()}
        encoded = password_hash(password)
        try:
            with self.connect() as db:
                db.execute('INSERT INTO users VALUES (?,?,?,?)', (user['id'], user['username'], encoded, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError('用户名已存在') from None
        return user

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        valid = password_matches(password, row['password'] if row else self.dummy_password)
        return {'id':row['id'], 'username':row['username']} if row and valid else None

    def login(self, user_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute('DELETE FROM logins WHERE expires<?', (now,))
            db.execute('INSERT INTO logins VALUES (?,?,?,?)', (token_hash(token), user_id, csrf, now+7*86400))
        return token, csrf

    def session(self, token: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT u.id, u.username, l.csrf FROM logins l JOIN users u ON u.id=l.user_id '
                             'WHERE l.digest=? AND l.expires>?', (token_hash(token), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token: str):
        with self.connect() as db:
            db.execute('DELETE FROM logins WHERE digest=?', (token_hash(token),))

    def limit(self, key: str, maximum: int, seconds: int) -> bool:
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM rate_limits WHERE reset<?', (now,))
            db.execute('INSERT INTO rate_limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key,now+seconds))
            return db.execute('SELECT count FROM rate_limits WHERE key=?',(key,)).fetchone()['count'] <= maximum

    def model(self, user_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT credential_id,model,base_url FROM model_defaults WHERE user_id=?',(user_id,)).fetchone()
        return dict(row) if row else None

    def save_model(self, user_id: str, credential_id: str, model: str, base_url: str):
        with self.connect() as db:
            db.execute('INSERT INTO model_defaults VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
                       'credential_id=excluded.credential_id, model=excluded.model, base_url=excluded.base_url',
                       (user_id,credential_id,model,base_url))
