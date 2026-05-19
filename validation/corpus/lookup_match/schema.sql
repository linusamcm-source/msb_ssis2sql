-- lookup_match: exercises LOOKUP with match, no-match, multiple-match, and NULL-key inputs.

IF OBJECT_ID('dbo.src_orders', 'U') IS NOT NULL DROP TABLE dbo.src_orders;
GO

CREATE TABLE dbo.src_orders (
    id        int             NOT NULL,
    order_id  int             NULL,
    amount    decimal(18,4)   NOT NULL
);
GO

IF OBJECT_ID('dbo.ref_customers', 'U') IS NOT NULL DROP TABLE dbo.ref_customers;
GO

CREATE TABLE dbo.ref_customers (
    customer_id    int             NOT NULL,
    customer_name  nvarchar(100)   NOT NULL,
    region         nvarchar(50)    NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_matched', 'U') IS NOT NULL DROP TABLE dbo.dst_matched;
GO

CREATE TABLE dbo.dst_matched (
    id             int             NOT NULL,
    order_id       int             NULL,
    amount         decimal(18,4)   NOT NULL,
    customer_name  nvarchar(100)   NULL,
    region         nvarchar(50)    NULL
);
GO

IF OBJECT_ID('dbo.dst_unmatched', 'U') IS NOT NULL DROP TABLE dbo.dst_unmatched;
GO

CREATE TABLE dbo.dst_unmatched (
    id        int             NOT NULL,
    order_id  int             NULL,
    amount    decimal(18,4)   NOT NULL
);
GO
