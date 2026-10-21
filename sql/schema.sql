-- Kanakku schema for Exasol. Idempotent: safe to run any number of times.
-- Runs inside the schema opened by kanakku.db.connect (EXASOL_SCHEMA, default KANAKKU).
-- Money columns are DECIMAL(18,0) minor units (paise). No floats anywhere.

-- ---------------------------------------------------------------- source tables
CREATE TABLE IF NOT EXISTS ORDERS (
    ORDER_ID            VARCHAR(40)    NOT NULL,
    ORDER_TS            TIMESTAMP      NOT NULL,
    CUSTOMER_REF        VARCHAR(40),
    AMOUNT_MINOR        DECIMAL(18,0)  NOT NULL,
    CURRENCY            CHAR(3)        NOT NULL,
    ORDER_STATUS        VARCHAR(20)    NOT NULL,   -- COMPLETED | CANCELLED | REFUNDED
    PAYMENT_METHOD      VARCHAR(20),
    CHANNEL             VARCHAR(10),
    SOURCE_FILE         VARCHAR(200),
    LOAD_ID             DECIMAL(18,0),
    LOADED_AT           TIMESTAMP,
    UPDATED_AT          TIMESTAMP,
    CONSTRAINT PK_ORDERS PRIMARY KEY (ORDER_ID)
);

CREATE TABLE IF NOT EXISTS GATEWAY_TRANSACTIONS (
    GATEWAY_TXN_ID           VARCHAR(40)   NOT NULL,
    ORDER_REF                VARCHAR(40),             -- merchant order id as the gateway saw it
    TXN_TYPE                 VARCHAR(20)   NOT NULL,  -- PAYMENT | REFUND | CHARGEBACK
    TXN_STATUS               VARCHAR(20)   NOT NULL,  -- CAPTURED | FAILED
    TXN_TS                   TIMESTAMP     NOT NULL,
    GROSS_AMOUNT_MINOR       DECIMAL(18,0) NOT NULL,
    FEE_MINOR                DECIMAL(18,0) NOT NULL,
    TAX_MINOR                DECIMAL(18,0) NOT NULL,  -- GST on the fee
    NET_AMOUNT_MINOR         DECIMAL(18,0) NOT NULL,
    CURRENCY                 CHAR(3)       NOT NULL,
    PAYMENT_METHOD           VARCHAR(20),
    SETTLEMENT_MODE          VARCHAR(10),             -- BATCH | INSTANT
    SETTLEMENT_BATCH_ID      VARCHAR(40),
    EXPECTED_SETTLEMENT_DATE DATE,
    SOURCE_FILE              VARCHAR(200),
    LOAD_ID                  DECIMAL(18,0),
    LOADED_AT                TIMESTAMP,
    UPDATED_AT               TIMESTAMP,
    CONSTRAINT PK_GATEWAY_TRANSACTIONS PRIMARY KEY (GATEWAY_TXN_ID)
);

CREATE TABLE IF NOT EXISTS BANK_TRANSACTIONS (
    BANK_TXN_ID         VARCHAR(40)    NOT NULL,
    VALUE_DATE          DATE           NOT NULL,
    POSTED_TS           TIMESTAMP,
    AMOUNT_MINOR        DECIMAL(18,0)  NOT NULL,      -- always positive; see DIRECTION
    DIRECTION           CHAR(2)        NOT NULL,      -- CR | DR
    CURRENCY            CHAR(3)        NOT NULL,
    NARRATION           VARCHAR(400),
    BANK_REFERENCE      VARCHAR(40),                  -- UTR
    SOURCE_FILE         VARCHAR(200),
    LOAD_ID             DECIMAL(18,0),
    LOADED_AT           TIMESTAMP,
    UPDATED_AT          TIMESTAMP,
    CONSTRAINT PK_BANK_TRANSACTIONS PRIMARY KEY (BANK_TXN_ID)
);

-- ---------------------------------------------------------------- staging (Kanakku-owned scratch)
CREATE TABLE IF NOT EXISTS STG_ORDERS (
    ORDER_ID VARCHAR(40), ORDER_TS TIMESTAMP, CUSTOMER_REF VARCHAR(40), AMOUNT_MINOR DECIMAL(18,0),
    CURRENCY CHAR(3), ORDER_STATUS VARCHAR(20), PAYMENT_METHOD VARCHAR(20), CHANNEL VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS STG_GATEWAY_TRANSACTIONS (
    GATEWAY_TXN_ID VARCHAR(40), ORDER_REF VARCHAR(40), TXN_TYPE VARCHAR(20), TXN_STATUS VARCHAR(20),
    TXN_TS TIMESTAMP, GROSS_AMOUNT_MINOR DECIMAL(18,0), FEE_MINOR DECIMAL(18,0), TAX_MINOR DECIMAL(18,0),
    NET_AMOUNT_MINOR DECIMAL(18,0), CURRENCY CHAR(3), PAYMENT_METHOD VARCHAR(20), SETTLEMENT_MODE VARCHAR(10),
    SETTLEMENT_BATCH_ID VARCHAR(40), EXPECTED_SETTLEMENT_DATE DATE
);

CREATE TABLE IF NOT EXISTS STG_BANK_TRANSACTIONS (
    BANK_TXN_ID VARCHAR(40), VALUE_DATE DATE, POSTED_TS TIMESTAMP, AMOUNT_MINOR DECIMAL(18,0),
    DIRECTION CHAR(2), CURRENCY CHAR(3), NARRATION VARCHAR(400), BANK_REFERENCE VARCHAR(40)
);

-- ---------------------------------------------------------------- ingestion audit
CREATE TABLE IF NOT EXISTS INGESTION_LOG (
    LOAD_ID                 DECIMAL(18,0) NOT NULL,
    SOURCE_NAME             VARCHAR(20)   NOT NULL,   -- ORDERS | GATEWAY | BANK
    FILE_NAME               VARCHAR(200),
    LOADED_AT               TIMESTAMP,
    ROWS_READ               DECIMAL(18,0),
    ROWS_VALID              DECIMAL(18,0),
    ROWS_REJECTED           DECIMAL(18,0),
    EXACT_DUPLICATES        DECIMAL(18,0),
    CONFLICTING_DUPLICATES  DECIMAL(18,0),
    ROWS_INSERTED           DECIMAL(18,0),
    ROWS_UPDATED            DECIMAL(18,0)
);

CREATE TABLE IF NOT EXISTS INGESTION_REJECTS (
    LOAD_ID        DECIMAL(18,0) NOT NULL,
    SOURCE_NAME    VARCHAR(20)   NOT NULL,
    LINE_NO        DECIMAL(18,0),
    RECORD_ID      VARCHAR(100),
    REJECT_REASON  VARCHAR(500),
    RAW_RECORD     VARCHAR(2000)
);

-- ---------------------------------------------------------------- reconciliation output
CREATE TABLE IF NOT EXISTS RECON_RUNS (
    RUN_ID              DECIMAL(18,0) NOT NULL,
    STARTED_AT          TIMESTAMP,
    FINISHED_AT         TIMESTAMP,
    RUN_STATUS          VARCHAR(20),                  -- RUNNING | COMPLETED | FAILED
    AS_OF_DATE          DATE,
    CLOSE_PERIOD_START  DATE,
    CLOSE_PERIOD_END    DATE,
    PARAMS              VARCHAR(2000),
    ORDERS_TOTAL        DECIMAL(18,0),
    ORDERS_MATCHED      DECIMAL(18,0),
    RESULTS_TOTAL       DECIMAL(18,0),
    EXCEPTIONS_TOTAL    DECIMAL(18,0),
    EXPOSURE_MINOR      DECIMAL(18,0),
    DURATION_MS         DECIMAL(18,0),
    ERROR_MESSAGE       VARCHAR(2000),
    CONSTRAINT PK_RECON_RUNS PRIMARY KEY (RUN_ID)
);

CREATE TABLE IF NOT EXISTS RECONCILIATION_RESULTS (
    RESULT_ID                 DECIMAL(18,0) NOT NULL,
    RUN_ID                    DECIMAL(18,0) NOT NULL,
    ENTITY_LEVEL              VARCHAR(20)   NOT NULL,  -- ORDER | GATEWAY_TXN | SETTLEMENT_BATCH | BANK_TXN
    ENTITY_ID                 VARCHAR(60)   NOT NULL,
    EXCEPTION_KEY             VARCHAR(140)  NOT NULL,  -- stable across runs: level:id:code
    ISSUE_CODE                VARCHAR(40)   NOT NULL,
    IS_EXCEPTION              BOOLEAN       NOT NULL,
    MATCH_STAGE               VARCHAR(20),             -- EXACT | TOLERANCE | BATCH_REF | BATCH_CONSTRAINED | FUZZY | NONE
    EVIDENCE_TYPE             VARCHAR(10),             -- CONFIRMED | SUSPECTED
    ORDER_ID                  VARCHAR(40),
    GATEWAY_TXN_ID            VARCHAR(40),
    RELATED_TXN_ID            VARCHAR(40),             -- e.g. the original of a duplicate
    BANK_TXN_ID               VARCHAR(40),
    SETTLEMENT_BATCH_ID       VARCHAR(40),
    PAYMENT_METHOD            VARCHAR(20),
    ORDER_AMOUNT_MINOR        DECIMAL(18,0),
    GATEWAY_GROSS_MINOR       DECIMAL(18,0),
    GATEWAY_FEE_MINOR         DECIMAL(18,0),           -- fee + GST
    GATEWAY_NET_MINOR         DECIMAL(18,0),
    BANK_AMOUNT_MINOR         DECIMAL(18,0),
    EXPECTED_AMOUNT_MINOR     DECIMAL(18,0),
    ACTUAL_AMOUNT_MINOR       DECIMAL(18,0),
    DISCREPANCY_MINOR         DECIMAL(18,0),           -- actual - expected
    EXPOSURE_MINOR            DECIMAL(18,0),           -- money at risk used for scoring
    ORDER_TS                  TIMESTAMP,
    GATEWAY_TS                TIMESTAMP,
    BANK_VALUE_DATE           DATE,
    EXPECTED_SETTLEMENT_DATE  DATE,
    REFERENCE_DATE            DATE,
    AGE_DAYS                  DECIMAL(9,0),
    EXPLANATION               VARCHAR(2000),
    EVIDENCE                  VARCHAR(2000),
    SCORE_AMOUNT              DECIMAL(3,0),
    SCORE_AGE                 DECIMAL(3,0),
    SCORE_SEVERITY            DECIMAL(3,0),
    SCORE_CLOSE               DECIMAL(3,0),
    PRIORITY_SCORE            DECIMAL(3,0),
    PRIORITY_BAND             VARCHAR(10),
    PRIORITY_REASON           VARCHAR(500),
    RESOLUTION_STATUS         VARCHAR(20),
    CREATED_AT                TIMESTAMP,
    CONSTRAINT PK_RECONCILIATION_RESULTS PRIMARY KEY (RESULT_ID)
);

CREATE TABLE IF NOT EXISTS RESOLUTION_LOG (
    LOG_ID            DECIMAL(18,0) IDENTITY,
    EXCEPTION_KEY     VARCHAR(140)  NOT NULL,
    RESULT_ID         DECIMAL(18,0),
    RUN_ID            DECIMAL(18,0),
    ISSUE_CODE        VARCHAR(40),
    PREVIOUS_STATUS   VARCHAR(20),
    NEW_STATUS        VARCHAR(20)   NOT NULL,
    NOTE              VARCHAR(2000),
    ANALYST           VARCHAR(100),
    CHANGED_AT        TIMESTAMP     NOT NULL
);

-- ---------------------------------------------------------------- evaluation only
-- Synthetic answer key. The reconciliation SQL never reads this table.
CREATE TABLE IF NOT EXISTS GROUND_TRUTH (
    ENTITY_LEVEL     VARCHAR(20) NOT NULL,
    ENTITY_ID        VARCHAR(60) NOT NULL,
    TRUE_ISSUE_CODE  VARCHAR(40) NOT NULL,
    SCENARIO         VARCHAR(60),
    CONSTRAINT PK_GROUND_TRUTH PRIMARY KEY (ENTITY_LEVEL, ENTITY_ID)
);

-- ---------------------------------------------------------------- reference data
-- Published gateway pricing. Seeded from kanakku/ratecard.py by kanakku.schema.
CREATE TABLE IF NOT EXISTS FEE_RATE_CARD (
    PAYMENT_METHOD  VARCHAR(20)  NOT NULL,
    FEE_BPS         DECIMAL(9,0) NOT NULL,
    GST_PCT         DECIMAL(5,0) NOT NULL,
    CONSTRAINT PK_FEE_RATE_CARD PRIMARY KEY (PAYMENT_METHOD)
);
