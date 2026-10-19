"""Encrypt native credential secrets at rest; decrypt only inside the service."""
import os
from pathlib import Path
from cryptography.fernet import Fernet
from pydantic import SecretStr
from agentscope.app.storage import AsyncSQLAlchemyStorage


class EncryptedStorage(AsyncSQLAlchemyStorage):
    def __init__(self, *, key_path: Path, **kwargs):
        super().__init__(**kwargs)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())

    async def upsert_credential(self, user_id, credential_data):
        data = credential_data.model_copy(deep=True)
        key = data.api_key
        plain = key.get_secret_value() if hasattr(key, 'get_secret_value') else str(key)
        data.api_key = SecretStr('tgenc:' + self.cipher.encrypt(plain.encode()).decode())
        return await super().upsert_credential(user_id, data)

    def _decrypt(self, record):
        if record is not None:
            record = record.model_copy(deep=True)
            key = record.data.get('api_key', '')
            if isinstance(key, str) and key.startswith('tgenc:'):
                record.data['api_key'] = self.cipher.decrypt(key[6:].encode()).decode()
        return record

    async def get_credential(self, user_id, credential_id):
        return self._decrypt(await super().get_credential(user_id, credential_id))

    async def list_credentials(self, user_id):
        return [self._decrypt(record) for record in await super().list_credentials(user_id)]
