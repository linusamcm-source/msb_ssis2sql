-- derived_and_convert: exercises DERIVED_COLUMN, DATA_CONVERSION, COPY_COLUMN.

IF OBJECT_ID('dbo.src_products', 'U') IS NOT NULL DROP TABLE dbo.src_products;
GO

CREATE TABLE dbo.src_products (
    id          int            NOT NULL,
    price       decimal(18,4)  NOT NULL,
    qty         int            NOT NULL,
    label       nvarchar(200)  NULL,
    event_dt    datetime2      NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_products', 'U') IS NOT NULL DROP TABLE dbo.dst_products;
GO

CREATE TABLE dbo.dst_products (
    id             int            NOT NULL,
    price_flt      float          NOT NULL,
    id_str         nvarchar(20)   NOT NULL,
    total_value    decimal(18,4)  NOT NULL,
    label_clean    nvarchar(200)  NOT NULL,
    event_year     int            NOT NULL,
    price_category nvarchar(10)   NOT NULL,
    label_safe     nvarchar(200)  NOT NULL,
    qty_copy       int            NOT NULL
);
GO
