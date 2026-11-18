# from luggage import _hash_password
# import psycopg2
import secrets
import hashlib
PASSWORD_ITERATIONS = 310_000
from typing import Optional

def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 and a fresh random salt."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


print(_hash_password("Admin@12345"))
