-- passthrough_basic: simple source-to-destination passthrough.
-- Types: int, nvarchar, decimal, bit, datetime2.

IF OBJECT_ID('dbo.src_items', 'U') IS NOT NULL DROP TABLE dbo.src_items;
GO

CREATE TABLE dbo.src_items (
    id          int            NOT NULL,
    name        nvarchar(100)  NOT NULL,
    amount      decimal(18,4)  NOT NULL,
    active      bit            NOT NULL,
    loaded_at   datetime2      NULL
);
GO

IF OBJECT_ID('dbo.dst_items', 'U') IS NOT NULL DROP TABLE dbo.dst_items;
GO

CREATE TABLE dbo.dst_items (
    id          int            NOT NULL,
    name        nvarchar(100)  NOT NULL,
    amount      decimal(18,4)  NOT NULL,
    active      bit            NOT NULL,
    loaded_at   datetime2      NULL
);
GO
