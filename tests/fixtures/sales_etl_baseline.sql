-- ------------------------------------------------------------------
-- Package variables referenced by SSIS expressions.
-- Confirm the data types and values before running.
-- ------------------------------------------------------------------
DECLARE @MinThreshold NVARCHAR(4000) = N'1000';  -- SSIS variable User::MinThreshold


-- ------------------------------------------------------------------
-- Control-flow Execute SQL Task(s), shown verbatim for completeness.
-- These are NOT data-flow transformations; their order is not modelled.
-- ------------------------------------------------------------------
-- [Execute SQL Task 1]
-- TRUNCATE TABLE dbo.FactSalesEnriched; TRUNCATE TABLE dbo.RegionSummary;


-- ======================================================================
-- Data Flow Task: DFT Customer Sales
-- ======================================================================

WITH [Sales_Orders_Source] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region]
    FROM (
        SELECT OrderID, CustomerID, OrderDate, Amount, Region FROM dbo.SalesOrders
    ) AS _src
),
[Enrich_Columns] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        DATEPART(year, [OrderDate]) AS [OrderYear],
        ([Amount] - ([Amount] * 0.10)) AS [NetAmount],
        UPPER(LTRIM(RTRIM([Region]))) AS [RegionClean]
    FROM [Sales_Orders_Source]
),
[Customer_Lookup_Ref] AS (
    SELECT
        [CustomerID],
        [CustomerName],
        [Tier]
    FROM (
        SELECT CustomerID, CustomerName, Tier FROM dbo.DimCustomer
    ) AS _ref
),
[Customer_Lookup_Match] AS (
    SELECT
        [L].[OrderID] AS [OrderID],
        [L].[CustomerID] AS [CustomerID],
        [L].[OrderDate] AS [OrderDate],
        [L].[Amount] AS [Amount],
        [L].[Region] AS [Region],
        [L].[OrderYear] AS [OrderYear],
        [L].[NetAmount] AS [NetAmount],
        [L].[RegionClean] AS [RegionClean],
        R.[CustomerName] AS [CustomerName],
        R.[Tier] AS [Tier]
    FROM [Enrich_Columns] AS L
    LEFT JOIN [Customer_Lookup_Ref] AS R ON L.[CustomerID] = R.[CustomerID]
),
[Route_By_Value_HighValue] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        [OrderYear],
        [NetAmount],
        [RegionClean],
        [CustomerName],
        [Tier]
    FROM [Customer_Lookup_Match]
    WHERE (([NetAmount] > @MinThreshold OR [Tier] = N'Gold'))
),
[Route_By_Value_LowValue_Default_Output] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        [OrderYear],
        [NetAmount],
        [RegionClean],
        [CustomerName],
        [Tier]
    FROM [Customer_Lookup_Match]
    WHERE NOT (([NetAmount] > @MinThreshold OR [Tier] = N'Gold'))
),
[Tag_High_Value] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        [OrderYear],
        [NetAmount],
        [RegionClean],
        [CustomerName],
        [Tier],
        N'HIGH' AS [Segment]
    FROM [Route_By_Value_HighValue]
),
[Tag_Low_Value] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        [OrderYear],
        [NetAmount],
        [RegionClean],
        [CustomerName],
        [Tier],
        N'LOW' AS [Segment]
    FROM [Route_By_Value_LowValue_Default_Output]
),
[Recombine_Branches] AS (
    SELECT
        [OrderID] AS [OrderID],
        [CustomerID] AS [CustomerID],
        [OrderDate] AS [OrderDate],
        [Amount] AS [Amount],
        [Region] AS [Region],
        [OrderYear] AS [OrderYear],
        [NetAmount] AS [NetAmount],
        [RegionClean] AS [RegionClean],
        [CustomerName] AS [CustomerName],
        [Tier] AS [Tier],
        [Segment] AS [Segment]
    FROM [Tag_High_Value]
    UNION ALL
    SELECT
        [OrderID] AS [OrderID],
        [CustomerID] AS [CustomerID],
        [OrderDate] AS [OrderDate],
        [Amount] AS [Amount],
        [Region] AS [Region],
        [OrderYear] AS [OrderYear],
        [NetAmount] AS [NetAmount],
        [RegionClean] AS [RegionClean],
        [CustomerName] AS [CustomerName],
        [Tier] AS [Tier],
        [Segment] AS [Segment]
    FROM [Tag_Low_Value]
),
[Sort_Output] AS (
    SELECT
        [OrderID],
        [CustomerID],
        [OrderDate],
        [Amount],
        [Region],
        [OrderYear],
        [NetAmount],
        [RegionClean],
        [CustomerName],
        [Tier],
        [Segment]
    FROM [Recombine_Branches]
)
INSERT INTO [dbo].[FactSalesEnriched] (
    [OrderID],
    [CustomerID],
    [OrderDate],
    [Amount],
    [Region],
    [OrderYear],
    [NetAmount],
    [RegionClean],
    [CustomerName],
    [Tier],
    [Segment]
)
SELECT
    [OrderID] AS [OrderID],
    [CustomerID] AS [CustomerID],
    [OrderDate] AS [OrderDate],
    [Amount] AS [Amount],
    [Region] AS [Region],
    [OrderYear] AS [OrderYear],
    [NetAmount] AS [NetAmount],
    [RegionClean] AS [RegionClean],
    [CustomerName] AS [CustomerName],
    [Tier] AS [Tier],
    [Segment] AS [Segment]
FROM [Sort_Output]
ORDER BY [RegionClean] ASC, [NetAmount] DESC;


-- ======================================================================
-- Data Flow Task: DFT Regional Summary
-- ======================================================================

WITH [Summary_Source] AS (
    SELECT
        [Region],
        [Amount],
        [OrderID]
    FROM (
        SELECT Region, Amount, OrderID FROM dbo.SalesOrders
    ) AS _src
),
[Aggregate_By_Region] AS (
    SELECT
        [Region],
        SUM([Amount]) AS [TotalAmount],
        COUNT([OrderID]) AS [OrderCount]
    FROM [Summary_Source]
    GROUP BY [Region]
)
INSERT INTO [dbo].[RegionSummary] (
    [Region],
    [TotalAmount],
    [OrderCount]
)
SELECT
    [Region] AS [Region],
    [TotalAmount] AS [TotalAmount],
    [OrderCount] AS [OrderCount]
FROM [Aggregate_By_Region];
