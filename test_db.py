import pyodbc

# Replace SERVER_NAME with your SSMS Server Name (e.g., 'localhost' or 'localhost\\SQLEXPRESS')
SERVER_NAME = "APAC-W-11208\SQLEXPRESS"

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={SERVER_NAME};"
    "DATABASE=master;"
    "Trusted_Connection=yes;"
)

try:
    conn = pyodbc.connect(conn_str, timeout=5)
    cursor = conn.cursor()
    cursor.execute("SELECT @@VERSION;")
    row = cursor.fetchone()
    print("Connected successfully!")
    print(row[0].splitlines()[0])
    conn.close()
except Exception as e:
    print("Connection failed:", e)
    print("Installed ODBC Drivers on your machine:", pyodbc.drivers())