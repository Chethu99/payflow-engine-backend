import datetime
import os
import bcrypt
from dotenv import load_dotenv
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-prod-9f8e7d6c5b4a3f2e1d")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")


def get_pg_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def login_user(conn, tenant_id: int, email: str, password: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, tenant_id, full_name, password_hash, role
            FROM users
            WHERE tenant_id = %s AND email = %s;
            """,
            (tenant_id, email),
        )
        user = cur.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid credentials or unauthorized organization access.")

    payload = {
        "sub": str(user["id"]),
        "tenant_id": user["tenant_id"],
        "role": user["role"],
        "name": user["full_name"],
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_and_verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise PermissionError("Session token has expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise PermissionError("Invalid authentication token.")