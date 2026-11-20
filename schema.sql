-- 1. Create the database if it doesn't exist
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'payflow_db')
BEGIN
    CREATE DATABASE payflow_db;
END
GO

USE payflow_db;
GO

-- Drop tables in reverse order if re-running from scratch
DROP TABLE IF EXISTS ledger_entries;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS wallets;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS tenants;
GO

-- 2. TENANTS (Companies)
CREATE TABLE tenants (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    plan_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (plan_tier IN ('FREE', 'PRO', 'ENTERPRISE')),
    created_at DATETIME2 DEFAULT SYSDATETIME()
);

-- 3. USERS (Staff belonging to a company)
CREATE TABLE users (
    id INT IDENTITY(1,1) PRIMARY KEY,
    tenant_id INT NOT NULL,
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'EMPLOYEE'
        CHECK (role IN ('EMPLOYEE', 'MANAGER', 'FINANCE')),
    created_at DATETIME2 DEFAULT SYSDATETIME(),

    CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT uq_tenant_email UNIQUE (tenant_id, email)
);

-- 4. WALLETS (1 money account per company)
CREATE TABLE wallets (
    id INT IDENTITY(1,1) PRIMARY KEY,
    tenant_id INT NOT NULL UNIQUE,
    balance DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    currency CHAR(3) NOT NULL DEFAULT 'INR',
    version INT NOT NULL DEFAULT 1, -- Protects against race conditions later
    updated_at DATETIME2 DEFAULT SYSDATETIME(),

    CONSTRAINT fk_wallets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT chk_non_negative_balance CHECK (balance >= 0.00)
);

-- 5. INVOICES (Bills waiting for approval/payment)
CREATE TABLE invoices (
    id INT IDENTITY(1,1) PRIMARY KEY,
    tenant_id INT NOT NULL,
    submitted_by INT NOT NULL,
    approved_by INT NULL,
    invoice_number VARCHAR(50) NOT NULL,
    vendor_name VARCHAR(150) NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'PAID')),
    version INT NOT NULL DEFAULT 1,
    created_at DATETIME2 DEFAULT SYSDATETIME(),

    CONSTRAINT fk_invoices_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    CONSTRAINT fk_invoices_submitter FOREIGN KEY (submitted_by) REFERENCES users(id),
    CONSTRAINT fk_invoices_approver FOREIGN KEY (approved_by) REFERENCES users(id),

    -- Same company cannot repeat an invoice number, different companies can
    CONSTRAINT uq_tenant_invoice_num UNIQUE (tenant_id, invoice_number),
    CONSTRAINT chk_positive_amount CHECK (amount > 0.00)
);

-- COMPOSITE INDEX: Makes filtering by company + status + newest first super fast
CREATE INDEX idx_invoices_tenant_status_created
ON invoices (tenant_id, status, created_at DESC);

-- 6. LEDGER ENTRIES (Audit trail of money movement)
CREATE TABLE ledger_entries (
    id INT IDENTITY(1,1) PRIMARY KEY,
    tenant_id INT NOT NULL,
    wallet_id INT NOT NULL,
    invoice_id INT NULL,
    entry_type VARCHAR(10) NOT NULL CHECK (entry_type IN ('CREDIT', 'DEBIT')),
    amount DECIMAL(15, 2) NOT NULL,
    balance_after DECIMAL(15, 2) NOT NULL,
    created_at DATETIME2 DEFAULT SYSDATETIME(),

    CONSTRAINT fk_ledger_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    CONSTRAINT fk_ledger_wallet FOREIGN KEY (wallet_id) REFERENCES wallets(id),
    CONSTRAINT fk_ledger_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id)
);
GO