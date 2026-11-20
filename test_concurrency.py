from concurrent.futures import ThreadPoolExecutor
import uuid
import pyodbc
import requests
from auth_security import CONN_STR, login_user

BASE_URL = "http://127.0.0.1:8000"


def setup_approved_invoice():
    conn = pyodbc.connect(CONN_STR)
    cur = conn.cursor()
    cur.execute("""
        SELECT TOP 1 id, invoice_number, amount
        FROM invoices
        WHERE tenant_id = 1 AND status = 'APPROVED'
        ORDER BY id DESC;
    """)
    row = cur.fetchone()
    if not row:
        cur.execute("""
            UPDATE TOP (1) invoices
            SET status = 'APPROVED'
            OUTPUT INSERTED.id, INSERTED.invoice_number, INSERTED.amount
            WHERE tenant_id = 1 AND status = 'PENDING_APPROVAL';
        """)
        row = cur.fetchone()
        conn.commit()
    token = login_user(conn, 1, "vikram@acme.com", "Welcome@123")
    conn.close()
    return row.id, row.invoice_number, float(row.amount), token


def fire_payout(args):
    inv_id, token, attempt = args
    res = requests.post(
        f"{BASE_URL}/invoices/{inv_id}/pay",
        headers={"Authorization": f"Bearer {token}"},
        json={"payment_rail": "RAZORPAYX"},
    )
    return attempt, res.status_code, res.json()


if __name__ == "__main__":
    inv_id, inv_num, amt, token = setup_approved_invoice()
    print(f"1. Firing 10 simultaneous payout threads for Invoice #{inv_id} ({inv_num}, INR {amt})...\n")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(fire_payout, [(inv_id, token, i) for i in range(1, 11)]))

    success_count = sum(1 for _, code, _ in results if code == 200)
    conflict_count = sum(1 for _, code, _ in results if code == 409)

    for attempt, code, body in results:
        print(f"Thread {attempt:02d} -> HTTP {code}: {body.get('message') or body.get('detail')}")

    print(f"\nRACE TEST SUMMARY: {success_count} settled (200 OK), {conflict_count} blocked (409 Conflict).")

    inv_id2, inv_num2, amt2, token2 = setup_approved_invoice()
    idem_key = str(uuid.uuid4())
    r1 = requests.post(
        f"{BASE_URL}/invoices/{inv_id2}/pay",
        headers={"Authorization": f"Bearer {token2}", "Idempotency-Key": idem_key},
        json={"payment_rail": "STRIPE"},
    ).json()
    r2 = requests.post(
        f"{BASE_URL}/invoices/{inv_id2}/pay",
        headers={"Authorization": f"Bearer {token2}", "Idempotency-Key": idem_key},
        json={"payment_rail": "STRIPE"},
    ).json()
    print(f"\n2. IDEMPOTENCY REPLAY TEST ({idem_key}):")
    print(f"   First call  -> replay={r1.get('idempotent_replay')}, ref={r1['receipt']['transaction_ref']}")
    print(f"   Second call -> replay={r2.get('idempotent_replay')}, ref={r2['receipt']['transaction_ref']}")
