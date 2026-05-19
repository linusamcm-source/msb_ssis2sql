-- union_multicast: exercises MERGE, ROW_COUNT, UNION_ALL, MULTICAST, AUDIT.

IF OBJECT_ID('dbo.src_events_current', 'U') IS NOT NULL DROP TABLE dbo.src_events_current;
GO

CREATE TABLE dbo.src_events_current (
    id          int             NOT NULL,
    event_type  nvarchar(50)    NOT NULL,
    amount      decimal(18,4)   NOT NULL
);
GO

IF OBJECT_ID('dbo.src_events_prior', 'U') IS NOT NULL DROP TABLE dbo.src_events_prior;
GO

CREATE TABLE dbo.src_events_prior (
    id          int             NOT NULL,
    event_type  nvarchar(50)    NOT NULL,
    amount      decimal(18,4)   NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_all_events', 'U') IS NOT NULL DROP TABLE dbo.dst_all_events;
GO

CREATE TABLE dbo.dst_all_events (
    id          int             NOT NULL,
    event_type  nvarchar(50)    NOT NULL,
    amount      decimal(18,4)   NOT NULL,
    pkg_name    nvarchar(255)   NOT NULL,
    exec_time   datetime2       NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_events_backup', 'U') IS NOT NULL DROP TABLE dbo.dst_events_backup;
GO

CREATE TABLE dbo.dst_events_backup (
    id          int             NOT NULL,
    event_type  nvarchar(50)    NOT NULL,
    amount      decimal(18,4)   NOT NULL
);
GO
