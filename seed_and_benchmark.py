import random
import time
import pyodbc

SERVER_NAME = "APAC-W-11208\SQLEXPRESS"  # <-- Update if needed (e.g., 'localhost\\SQLEXPRESS')
DRIVER = "{ODBC Driver 17 for SQL Server}"

CONN_STR = (
    f"DRIVER={DRIVER};"
    f"SERVER={SERVER_NAME};"
    "DATABASE=payflow_db;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


def seed_data(conn):
    cursor = conn.cursor()

    # Check if already seeded so running twice doesn't crash
    cursor.execute("SELECT COUNT(*) FROM tenants;")
    if cursor.fetchone()[0] > 0:
        print("Database already seeded. Skipping insert.")
        return

    # 1. Insert 2 Companies (Tenants)
    cursor.execute(
        "INSERT INTO tenants (name, plan_tier) VALUES (?, ?), (?, ?);",
        ("Acme Logistics", "ENTERPRISE", "Nova Retail", "PRO"),
    )

    # 2. Create Wallets for Tenant 1 and Tenant 2
    cursor.execute(
        """
        INSERT INTO wallets (tenant_id, balance, currency)
        VALUES (1, 5000000.00, 'INR'), (2, 1500000.00, 'INR');
        """
    )

    # 3. Create Users for both companies
    users = [
        (1, "rahul@acme.com", "Rahul Sharma", "EMPLOYEE"),
        (1, "priya@acme.com", "Priya Nair", "MANAGER"),
        (1, "vikram@acme.com", "Vikram Mehta", "FINANCE"),
        (2, "ananya@nova.com", "Ananya Rao", "EMPLOYEE"),
        (2, "kabir@nova.com", "Kabir Das", "MANAGER"),
    ]
    cursor.executemany(
        "INSERT INTO users (tenant_id, email, full_name, role) VALUES (?, ?, ?, ?);",
        users,
    )
    conn.commit()

    # 4. Generate 50,000 realistic invoices
    print("Generating 50,000 invoices... please wait a few seconds...")
    statuses = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "PAID"]
    vendors = ["AWS Cloud", "Delhivery", "Razorpay", "Zoho Corp", "Tata Tele"]

    batch = []
    for i in range(1, 50001):
        tenant_id = 1 if i % 2 == 0 else 2
        submitter_id = 1 if tenant_id == 1 else 4
        inv_num = f"INV-2026-{i:06d}"
        vendor = random.choice(vendors)
        amount = round(random.uniform(500.0, 95000.0), 2)
        status = random.choice(statuses)
        batch.append((tenant_id, submitter_id, inv_num, vendor, amount, status))

    # fast_executemany sends rows in bulk packets instead of 50,000 tiny network calls
    cursor.fast_executemany = True
    cursor.executemany(
        """
        INSERT INTO invoices (tenant_id, submitted_by, invoice_number, vendor_name, amount, status)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        batch,
    )
    conn.commit()
    print("Successfully inserted 50,000 invoices!")


def demo_n_plus_one_vs_join(conn):
    cursor = conn.cursor()

    print("\n--- TEST 1: The BAD Way (N+1 Query Problem) ---")
    start_bad = time.perf_counter()

    # Query 1: Fetch 200 pending invoices
    cursor.execute(
        """
        SELECT TOP 200 id, invoice_number, amount, submitted_by
        FROM invoices
        WHERE tenant_id = 1 AND status = 'PENDING_APPROVAL'
        ORDER BY created_at DESC;
        """
    )
    invoices = cursor.fetchall()

    # Query 2..201: Loop through each invoice and hit the DB AGAIN for the user's name!
    results_bad = []
    for inv in invoices:
        cursor.execute("SELECT full_name FROM users WHERE id = ?;", (inv.submitted_by,))
        user_row = cursor.fetchone()
        results_bad.append((inv.invoice_number, inv.amount, user_row.full_name))

    bad_ms = (time.perf_counter() - start_bad) * 1000
    print(f"Fetched {len(results_bad)} invoices using 201 separate queries in: {bad_ms:.2f} ms")

    print("\n--- TEST 2: The PRO Way (Single INNER JOIN Query) ---")
    start_good = time.perf_counter()

    # Only 1 single query! SQL Server joins invoices + users in memory
    cursor.execute(
        """
        SELECT TOP 200
            i.id,
            i.invoice_number,
            i.amount,
            u.full_name AS submitted_by_name
        FROM invoices i
        INNER JOIN users u ON i.submitted_by = u.id
        WHERE i.tenant_id = 1 AND i.status = 'PENDING_APPROVAL'
        ORDER BY i.created_at DESC;
        """
    )
    results_good = cursor.fetchall()

    good_ms = (time.perf_counter() - start_good) * 1000
    print(f"Fetched {len(results_good)} invoices using 1 JOIN query in: {good_ms:.2f} ms")


if __name__ == "__main__":
    connection = pyodbc.connect(CONN_STR)
    seed_data(connection)
    demo_n_plus_one_vs_join(connection)
    connection.close()