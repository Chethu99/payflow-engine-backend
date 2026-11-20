import pyodbc

SERVER_NAME = "APAC-W-11208\SQLEXPRESS"  # <-- Use the exact SERVER_NAME that worked in test_db.py
DRIVER = "{ODBC Driver 17 for SQL Server}"  # <-- Or Driver 18 if you used that

conn_str = (
    f"DRIVER={DRIVER};"
    f"SERVER={SERVER_NAME};"
    "DATABASE=master;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

def run_schema():
    # autocommit=True is required when running CREATE DATABASE in SQL Server
    conn = pyodbc.connect(conn_str, autocommit=True)
    cursor = conn.cursor()

    with open("schema.sql", "r", encoding="utf-8") as f:
        sql_text = f.read()

    # T-SQL uses 'GO' to separate batches; split on GO and run each batch
    batches = [b.strip() for b in sql_text.split("\nGO") if b.strip()]

    for i, batch in enumerate(batches, start=1):
        cursor.execute(batch)
        print(f"Executed batch {i}/{len(batches)} successfully.")

    cursor.close()
    conn.close()
    print("\nAll 5 tables and indexes created in payflow_db!")

if __name__ == "__main__":
    run_schema()