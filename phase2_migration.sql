USE payflow_db;
GO

-- Add password_hash column if it doesn't already exist
IF COL_LENGTH('users', 'password_hash') IS NULL
BEGIN
    ALTER TABLE users
    ADD password_hash VARCHAR(255) NULL;
END
GO