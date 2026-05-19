-- merge_join: exercises MERGE_JOIN (INNER JOIN on a shared key column).

IF OBJECT_ID('dbo.src_employees', 'U') IS NOT NULL DROP TABLE dbo.src_employees;
GO

CREATE TABLE dbo.src_employees (
    emp_id   int            NOT NULL,
    dept_id  int            NULL,
    name     nvarchar(100)  NOT NULL
);
GO

IF OBJECT_ID('dbo.src_departments', 'U') IS NOT NULL DROP TABLE dbo.src_departments;
GO

CREATE TABLE dbo.src_departments (
    dept_id    int            NOT NULL,
    dept_name  nvarchar(100)  NOT NULL
);
GO

IF OBJECT_ID('dbo.dst_emp_dept', 'U') IS NOT NULL DROP TABLE dbo.dst_emp_dept;
GO

CREATE TABLE dbo.dst_emp_dept (
    emp_id     int            NOT NULL,
    dept_id    int            NULL,
    name       nvarchar(100)  NOT NULL,
    dept_name  nvarchar(100)  NOT NULL
);
GO
