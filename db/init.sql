-- 1) Table brute pour l'import CSV (tout en texte)
CREATE TABLE raw_import (
    "BillNo"     TEXT,
    "Itemname"   TEXT,
    "Quantity"   TEXT,
    "Date"       TEXT,
    "Price"      TEXT,
    "CustomerID" TEXT,
    "Country"    TEXT
);

-- 2) Import du fichier CSV
COPY raw_import ("BillNo","Itemname","Quantity","Date","Price","CustomerID","Country")
FROM '/docker-entrypoint-initdb.d/dataset.csv'
WITH (
    FORMAT csv,
    HEADER true,
    DELIMITER ';'
);

-- 3) Table finale (BillNo et CustomerID sont TEXT)
CREATE TABLE transactions (
    id          SERIAL PRIMARY KEY,
    bill_no     TEXT,
    itemname    TEXT,
    quantity    INTEGER,
    date        TIMESTAMP,
    price       NUMERIC(10,2),
    customer_id TEXT,
    country     TEXT
);

-- 4) Insertion sans convertir BillNo / CustomerID en bigint
INSERT INTO transactions (bill_no, itemname, quantity, date, price, customer_id, country)
SELECT 
    "BillNo",
    "Itemname",
    NULLIF("Quantity", '')::INTEGER,
    to_timestamp("Date", 'DD.MM.YYYY HH24:MI'),
    REPLACE("Price", ',', '.')::NUMERIC(10,2),
    "CustomerID",
    "Country"
FROM raw_import;

DROP TABLE raw_import;
