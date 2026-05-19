-- aggregate_group: exercises AGGREGATE (GROUP BY + SUM/AVG/MIN/MAX/COUNT/COUNT DISTINCT) + SORT.

IF OBJECT_ID('dbo.src_sales', 'U') IS NOT NULL DROP TABLE dbo.src_sales;
GO

CREATE TABLE dbo.src_sales (
    id       int             NOT NULL,
    region   nvarchar(50)    NULL,
    amount   decimal(18,4)   NOT NULL,
    qty      int             NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_region_totals', 'U') IS NOT NULL DROP TABLE dbo.dst_region_totals;
GO

CREATE TABLE dbo.dst_region_totals (
    region         nvarchar(50)    NULL,
    total_amount   decimal(18,4)   NOT NULL,
    avg_amount     decimal(18,4)   NOT NULL,
    min_amount     decimal(18,4)   NOT NULL,
    max_amount     decimal(18,4)   NOT NULL,
    row_count      bigint          NOT NULL,
    distinct_ids   bigint          NOT NULL
);
GO
