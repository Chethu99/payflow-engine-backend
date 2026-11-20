import datetime
from decimal import Decimal
import json
import os
from typing import Optional
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

from auth_security import (
    DATABASE_URL,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    decode_and_verify_token,
    login_user,
)
from domain_patterns import (
    IllegalStateTransitionError,
    InvoiceStateMachine,
    PaymentGatewayFactory,
)

load_dotenv()

app = FastAPI(
    title="PayFlow B2B Spend & Ledger API",
    version="2.3.0",
    description="Cloud-native multi-tenant spend approval API on PostgreSQL with Google OIDC SSO & Row-Level Locking.",
)

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,https://payflow.formfit.space",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID",
    "828239570004-d6rjfm7j2ad1m49pojfige07hge3kk6n.apps.googleusercontent.com",
)


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Expected: Bearer <token>",
        )
    token = authorization.split(" ", 1)[1]
    try:
        return decode_and_verify_token(token)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


def _issue_payflow_jwt(user_id: int, tenant_id: int, role: str, full_name: str) -> str:
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "role": role,
        "name": full_name,
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


class LoginRequest(BaseModel):
    tenant_id: int = Field(..., gt=0)
    email: str
    password: str = Field(..., min_length=6)


class EmailLoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6)


class GoogleSSORequest(BaseModel):
    credential: str = Field(..., description="Google OIDC ID Token from frontend")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CreateInvoiceRequest(BaseModel):
    invoice_number: str = Field(..., min_length=3, max_length=50)
    vendor_name: str = Field(..., min_length=2, max_length=150)
    amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)


class PayInvoiceRequest(BaseModel):
    payment_rail: str = Field(default="RAZORPAYX", description="RAZORPAYX, STRIPE, or WIRE")


@app.get("/health", tags=["System"])
def health_check(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT 1;")
    return {"status": "healthy", "database": "neon-postgres", "version": "2.3.0"}


@app.post("/auth/login", response_model=TokenResponse, tags=["Authentication"])
def api_login(body: LoginRequest, conn=Depends(get_db)):
    try:
        token = login_user(conn, body.tenant_id, body.email, body.password)
        return {"access_token": token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@app.post("/auth/login-email", response_model=TokenResponse, tags=["Authentication"])
def api_login_by_email(body: EmailLoginRequest, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM users WHERE email = %s;", (body.email.strip(),))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    try:
        token = login_user(conn, row["tenant_id"], body.email.strip(), body.password)
        return {"access_token": token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@app.post("/auth/google", tags=["Authentication"])
def api_google_sso(body: GoogleSSORequest, conn=Depends(get_db)):
    try:
        id_info = id_token.verify_oauth2_token(
            body.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google SSO token: {str(e)}",
        )

    email = id_info.get("email")
    full_name = id_info.get("name") or email.split("@")[0]
    if not email or not id_info.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google account email is not verified.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.tenant_id, u.full_name, u.role, t.name AS org_name
            FROM users u
            INNER JOIN tenants t ON u.tenant_id = t.id
            WHERE u.email = %s;
            """,
            (email,),
        )
        user = cur.fetchone()

        if not user:
            cur.execute(
                """
                INSERT INTO users (tenant_id, full_name, email, role)
                VALUES (1, %s, %s, 'FINANCE')
                RETURNING id, tenant_id, full_name, role;
                """,
                (full_name, email),
            )
            new_u = cur.fetchone()
            conn.commit()
            user_id, tenant_id, name, role, org_name = (
                new_u["id"], new_u["tenant_id"], new_u["full_name"], new_u["role"], "Acme Logistics"
            )
        else:
            user_id, tenant_id, name, role, org_name = (
                user["id"], user["tenant_id"], user["full_name"], user["role"], user["org_name"]
            )

    payflow_token = _issue_payflow_jwt(user_id, tenant_id, role, name)
    return {
        "access_token": payflow_token,
        "token_type": "bearer",
        "user": {
            "name": name,
            "email": email,
            "role": role,
            "tenant_id": tenant_id,
            "org": org_name,
        },
    }


@app.get("/wallet", tags=["Treasury"])
def get_wallet_and_ledger(
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, balance, currency, updated_at FROM wallets WHERE tenant_id = %s;",
            (tenant_id,),
        )
        wallet = cur.fetchone()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found for organization.")

        cur.execute(
            """
            SELECT id, invoice_id, entry_type, amount, balance_after, created_at
            FROM ledger_entries
            WHERE tenant_id = %s
            ORDER BY id DESC
            LIMIT 10;
            """,
            (tenant_id,),
        )
        entries = [
            {
                "id": r["id"],
                "invoice_id": r["invoice_id"],
                "entry_type": r["entry_type"],
                "amount": float(r["amount"]),
                "balance_after": float(r["balance_after"]),
                "created_at": r["created_at"].isoformat(),
            }
            for r in cur.fetchall()
        ]

    return {
        "wallet_id": wallet["id"],
        "balance": float(wallet["balance"]),
        "currency": wallet["currency"],
        "recent_entries": entries,
    }


@app.post("/invoices", status_code=status.HTTP_201_CREATED, tags=["Invoices"])
def create_invoice(
    body: CreateInvoiceRequest,
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    user_id = int(current_user["sub"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoices (tenant_id, submitted_by, invoice_number, vendor_name, amount, status)
                VALUES (%s, %s, %s, %s, %s, 'PENDING_APPROVAL')
                RETURNING id, status, created_at;
                """,
                (tenant_id, user_id, body.invoice_number, body.vendor_name, body.amount),
            )
            row = cur.fetchone()
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invoice '{body.invoice_number}' already exists for your organization.",
        )

    return {
        "id": row["id"],
        "tenant_id": tenant_id,
        "invoice_number": body.invoice_number,
        "vendor_name": body.vendor_name,
        "amount": float(body.amount),
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


@app.get("/invoices", tags=["Invoices"])
def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    cursor_id: Optional[int] = Query(None),
    limit: int = Query(12, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    conditions = ["i.tenant_id = %s"]
    params = [tenant_id]

    if status_filter:
        conditions.append("i.status = %s")
        params.append(status_filter.upper())

    if cursor_id is not None:
        conditions.append("i.id < %s")
        params.append(cursor_id)

    where_clause = " AND ".join(conditions)
    query = f"""
        SELECT
            i.id, i.invoice_number, i.vendor_name, i.amount, i.status, i.created_at,
            u.full_name AS submitted_by_name
        FROM invoices i
        INNER JOIN users u ON i.submitted_by = u.id
        WHERE {where_clause}
        ORDER BY i.id DESC
        LIMIT %s;
    """
    with conn.cursor() as cur:
        cur.execute(query, params + [limit])
        rows = cur.fetchall()

    items = [
        {
            "id": r["id"],
            "invoice_number": r["invoice_number"],
            "vendor_name": r["vendor_name"],
            "amount": float(r["amount"]),
            "status": r["status"],
            "submitted_by": r["submitted_by_name"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@app.patch("/invoices/{invoice_id}/approve", tags=["Invoices"])
def approve_invoice_endpoint(
    invoice_id: int,
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    approver_id = int(current_user["sub"])
    role = current_user["role"]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, status FROM invoices WHERE id = %s AND tenant_id = %s;",
            (invoice_id, tenant_id),
        )
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found in your organization.")

        try:
            next_state = InvoiceStateMachine.from_status(inv["status"]).approve(role)
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except IllegalStateTransitionError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        cur.execute(
            "UPDATE invoices SET status = %s, approved_by = %s WHERE id = %s AND tenant_id = %s;",
            (next_state.name, approver_id, invoice_id, tenant_id),
        )
    conn.commit()
    return {"message": f"Invoice #{invoice_id} transitioned to {next_state.name}.", "status": next_state.name}


@app.patch("/invoices/{invoice_id}/reject", tags=["Invoices"])
def reject_invoice_endpoint(
    invoice_id: int,
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    role = current_user["role"]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, status FROM invoices WHERE id = %s AND tenant_id = %s;",
            (invoice_id, tenant_id),
        )
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found in your organization.")

        try:
            next_state = InvoiceStateMachine.from_status(inv["status"]).reject(role)
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except IllegalStateTransitionError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        cur.execute(
            "UPDATE invoices SET status = %s WHERE id = %s AND tenant_id = %s;",
            (next_state.name, invoice_id, tenant_id),
        )
    conn.commit()
    return {"message": f"Invoice #{invoice_id} rejected.", "status": next_state.name}


@app.post("/invoices/{invoice_id}/pay", tags=["Treasury"])
def pay_invoice_endpoint(
    invoice_id: int,
    body: PayInvoiceRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    """
    Concurrency-safe payout endpoint using PostgreSQL SELECT ... FOR UPDATE
    row-level locks and Idempotency-Key replay protection.
    """
    tenant_id = current_user["tenant_id"]
    role = current_user["role"]
    endpoint_tag = f"POST /invoices/{invoice_id}/pay"

    try:
        with conn.cursor() as cur:
            # 1. Idempotency Check
            if idempotency_key:
                cur.execute(
                    """
                    SELECT response_body
                    FROM idempotency_keys
                    WHERE tenant_id = %s AND idempotency_key = %s;
                    """,
                    (tenant_id, idempotency_key.strip()),
                )
                cached = cur.fetchone()
                if cached:
                    conn.rollback()
                    payload = json.loads(cached["response_body"])
                    payload["idempotent_replay"] = True
                    return payload

            # 2. Select Payment Strategy via Factory
            gateway = PaymentGatewayFactory.get_gateway(body.payment_rail)

            # 3. Pessimistic Row-Level Lock on the Invoice row (FOR UPDATE)
            cur.execute(
                """
                SELECT id, invoice_number, vendor_name, amount, status
                FROM invoices
                WHERE id = %s AND tenant_id = %s
                FOR UPDATE;
                """,
                (invoice_id, tenant_id),
            )
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found in your organization.")

            # 4. Validate State Transition
            next_state = InvoiceStateMachine.from_status(inv["status"]).pay(role)

            # 5. Pessimistic Row-Level Lock on the Tenant Wallet row (FOR UPDATE)
            cur.execute(
                """
                SELECT id, balance
                FROM wallets
                WHERE tenant_id = %s
                FOR UPDATE;
                """,
                (tenant_id,),
            )
            wallet = cur.fetchone()
            if not wallet or wallet["balance"] < inv["amount"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Insufficient wallet balance to settle this invoice.",
                )

            # 6. Execute External Payout Strategy
            payout_receipt = gateway.execute_payout(
                inv["invoice_number"], inv["vendor_name"], inv["amount"]
            )
            new_balance = wallet["balance"] - inv["amount"]

            # 7. Atomic Wallet Debit + Ledger Entry + Invoice Update
            cur.execute(
                "UPDATE wallets SET balance = %s, updated_at = NOW() WHERE id = %s;",
                (new_balance, wallet["id"]),
            )
            cur.execute(
                """
                INSERT INTO ledger_entries (tenant_id, wallet_id, invoice_id, entry_type, amount, balance_after)
                VALUES (%s, %s, %s, 'DEBIT', %s, %s);
                """,
                (tenant_id, wallet["id"], inv["id"], inv["amount"], new_balance),
            )
            cur.execute(
                "UPDATE invoices SET status = %s WHERE id = %s AND tenant_id = %s;",
                (next_state.name, inv["id"], tenant_id),
            )

            response_payload = {
                "message": f"Settled {inv['invoice_number']} via {payout_receipt['provider']} ({payout_receipt['transaction_ref']}).",
                "status": next_state.name,
                "new_wallet_balance": float(new_balance),
                "receipt": payout_receipt,
                "idempotent_replay": False,
            }

            if idempotency_key:
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (tenant_id, idempotency_key, endpoint, response_status, response_body)
                    VALUES (%s, %s, %s, 200, %s);
                    """,
                    (tenant_id, idempotency_key.strip(), endpoint_tag, json.dumps(response_payload)),
                )

        conn.commit()
        return response_payload

    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        conn.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except IllegalStateTransitionError as e:
        conn.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise