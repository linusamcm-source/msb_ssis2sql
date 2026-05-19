-- Fixture schema for validation/tests/test_provisioning.py.
-- This is a throwaway test-data package — NOT a real corpus member.
--
-- GO batch separators are intentional: provisioning.py must split on them
-- before calling pyodbc, which rejects a batch containing GO.

CREATE TABLE dbo.src_widgets (
    id          INT             NOT NULL,
    name        NVARCHAR(50)    NOT NULL,
    description NVARCHAR(50)    NULL,        -- string nullable: empty field -> '', \N -> NULL
    quantity    INT             NULL,         -- non-string nullable: empty field -> NULL
    price       DECIMAL(18, 4)  NOT NULL,
    created_at  DATETIME2       NOT NULL,
    active      BIT             NOT NULL
);
GO

CREATE TABLE dbo.dst_widgets (
    id          INT             NOT NULL,
    name        NVARCHAR(50)    NOT NULL,
    description NVARCHAR(50)    NULL,
    quantity    INT             NULL,
    price       DECIMAL(18, 4)  NOT NULL,
    created_at  DATETIME2       NOT NULL,
    active      BIT             NOT NULL
);
GO
