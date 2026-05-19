-- etl_full: rich pipeline exercising SOURCE, DERIVED_COLUMN, LOOKUP,
--           CONDITIONAL_SPLIT, UNION_ALL, SORT, and DESTINATION.

IF OBJECT_ID('dbo.src_transactions', 'U') IS NOT NULL DROP TABLE dbo.src_transactions;
GO

CREATE TABLE dbo.src_transactions (
    txn_id   int             NOT NULL,
    cust_id  int             NOT NULL,
    amount   decimal(18,4)   NOT NULL,
    region   nvarchar(50)    NOT NULL
);
GO

IF OBJECT_ID('dbo.ref_customer_tiers', 'U') IS NOT NULL DROP TABLE dbo.ref_customer_tiers;
GO

CREATE TABLE dbo.ref_customer_tiers (
    cust_id  int             NOT NULL,
    tier     nvarchar(20)    NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_enriched', 'U') IS NOT NULL DROP TABLE dbo.dst_enriched;
GO

CREATE TABLE dbo.dst_enriched (
    txn_id        int             NOT NULL,
    cust_id       int             NOT NULL,
    amount        decimal(18,4)   NOT NULL,
    region        nvarchar(50)    NOT NULL,
    net_amount    decimal(18,4)   NOT NULL,
    region_clean  nvarchar(50)    NOT NULL,
    tier          nvarchar(20)    NULL,
    segment       nvarchar(10)    NOT NULL
);
GO
