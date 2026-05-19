-- conditional_split: exercises CONDITIONAL_SPLIT (multi-branch + default).

IF OBJECT_ID('dbo.src_scores', 'U') IS NOT NULL DROP TABLE dbo.src_scores;
GO

CREATE TABLE dbo.src_scores (
    id       int             NOT NULL,
    score    decimal(10,2)   NULL,
    category nvarchar(50)    NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_high', 'U') IS NOT NULL DROP TABLE dbo.dst_high;
GO

CREATE TABLE dbo.dst_high (
    id       int            NOT NULL,
    score    decimal(10,2)  NULL,
    category nvarchar(50)   NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_medium', 'U') IS NOT NULL DROP TABLE dbo.dst_medium;
GO

CREATE TABLE dbo.dst_medium (
    id       int            NOT NULL,
    score    decimal(10,2)  NULL,
    category nvarchar(50)   NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_low', 'U') IS NOT NULL DROP TABLE dbo.dst_low;
GO

CREATE TABLE dbo.dst_low (
    id       int            NOT NULL,
    score    decimal(10,2)  NULL,
    category nvarchar(50)   NOT NULL
);
GO
