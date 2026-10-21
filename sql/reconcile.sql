-- Kanakku three-way reconciliation, executed inside Exasol.
-- Placeholders in {braces} are filled by kanakku/reconciliation.py with validated
-- numbers/dates only. This script reads ORDERS, GATEWAY_TRANSACTIONS,
-- BANK_TRANSACTIONS and FEE_RATE_CARD. It never reads the evaluation answer key.
--
-- Stages, in priority order:
--   1 EXACT       order reference + amount equal (after trimming/upper-casing the ref)
--   2 TOLERANCE   order reference equal, amount within {tol} paise
--   3 BATCH       settlement batches: by reference in the bank narration (BATCH_REF), or,
--                 for credits without a reference, a unique amount + date-window match
--                 (BATCH_CONSTRAINED)
--   4 FUZZY       payments with no usable order reference: unique same-amount,
--                 same-method payment within {fuzzy_window_sec} seconds of the order
-- Every entity gets exactly one result row per run, so nothing is double counted.

-- ============================================================ normalise sources
CREATE OR REPLACE TABLE WRK_GW AS
SELECT g.GATEWAY_TXN_ID, g.ORDER_REF, UPPER(TRIM(g.ORDER_REF)) AS NORM_REF, g.TXN_TYPE, g.TXN_TS,
       g.GROSS_AMOUNT_MINOR, g.FEE_MINOR, g.TAX_MINOR, g.NET_AMOUNT_MINOR, g.PAYMENT_METHOD,
       g.SETTLEMENT_MODE, g.SETTLEMENT_BATCH_ID, g.EXPECTED_SETTLEMENT_DATE,
       CASE WHEN g.TXN_TYPE = 'PAYMENT' THEN g.NET_AMOUNT_MINOR ELSE -g.NET_AMOUNT_MINOR END AS SIGNED_NET
FROM GATEWAY_TRANSACTIONS g
WHERE g.TXN_STATUS = 'CAPTURED';

CREATE OR REPLACE TABLE WRK_BANK AS
SELECT b.BANK_TXN_ID, b.VALUE_DATE, b.POSTED_TS, b.AMOUNT_MINOR, b.DIRECTION, b.NARRATION, b.BANK_REFERENCE,
       CASE WHEN b.DIRECTION = 'CR' THEN b.AMOUNT_MINOR ELSE -b.AMOUNT_MINOR END AS SIGNED_AMOUNT,
       REGEXP_SUBSTR(b.NARRATION, 'STL-[0-9]{8}-[0-9]{2}') AS BATCH_REF,
       REGEXP_SUBSTR(b.NARRATION, 'pay_[A-Za-z0-9]{14}') AS PAY_REF
FROM BANK_TRANSACTIONS b;

CREATE OR REPLACE TABLE WRK_BANK_RANKED AS
SELECT b.*,
       ROW_NUMBER() OVER (PARTITION BY COALESCE(b.BATCH_REF, b.PAY_REF, b.BANK_TXN_ID)
                          ORDER BY b.VALUE_DATE, b.POSTED_TS, b.BANK_TXN_ID) AS REF_RANK,
       FIRST_VALUE(b.BANK_TXN_ID) OVER (PARTITION BY COALESCE(b.BATCH_REF, b.PAY_REF, b.BANK_TXN_ID)
                          ORDER BY b.VALUE_DATE, b.POSTED_TS, b.BANK_TXN_ID) AS FIRST_TXN_ID,
       FIRST_VALUE(b.SIGNED_AMOUNT) OVER (PARTITION BY COALESCE(b.BATCH_REF, b.PAY_REF, b.BANK_TXN_ID)
                          ORDER BY b.VALUE_DATE, b.POSTED_TS, b.BANK_TXN_ID) AS FIRST_AMOUNT
FROM WRK_BANK b;

-- ============================================================ stage 1/2: order <-> gateway by reference
CREATE OR REPLACE TABLE WRK_PAY AS
SELECT p.GATEWAY_TXN_ID, p.ORDER_REF, p.NORM_REF, p.TXN_TS, p.GROSS_AMOUNT_MINOR, p.FEE_MINOR, p.TAX_MINOR,
       p.NET_AMOUNT_MINOR, p.PAYMENT_METHOD, p.SETTLEMENT_MODE, p.SETTLEMENT_BATCH_ID, p.EXPECTED_SETTLEMENT_DATE,
       CAST(o.ORDER_ID AS VARCHAR(40)) AS LINKED_ORDER_ID,
       CAST(CASE WHEN o.ORDER_ID IS NOT NULL THEN 'KEY' END AS VARCHAR(10)) AS LINK_METHOD,
       CAST(NULL AS DECIMAL(18,0)) AS FUZZY_GAP_SEC
FROM WRK_GW p
LEFT JOIN ORDERS o ON o.ORDER_ID = p.NORM_REF
WHERE p.TXN_TYPE = 'PAYMENT';

-- ============================================================ stage 4: conservative fuzzy link
-- Only for orders with no payment by key and payments with no valid order key.
-- A pair is accepted only if it is the single candidate for BOTH sides.
CREATE OR REPLACE TABLE WRK_FUZZY AS
SELECT ORDER_ID, GATEWAY_TXN_ID, GAP_SEC FROM (
    SELECT o.ORDER_ID, p.GATEWAY_TXN_ID,
           ABS(SECONDS_BETWEEN(p.TXN_TS, o.ORDER_TS)) AS GAP_SEC,
           COUNT(*) OVER (PARTITION BY o.ORDER_ID) AS N_FOR_ORDER,
           COUNT(*) OVER (PARTITION BY p.GATEWAY_TXN_ID) AS N_FOR_PAY
    FROM ORDERS o
    JOIN WRK_PAY p
      ON p.LINKED_ORDER_ID IS NULL
     AND p.GROSS_AMOUNT_MINOR = o.AMOUNT_MINOR
     AND (p.PAYMENT_METHOD = o.PAYMENT_METHOD OR p.PAYMENT_METHOD IS NULL OR o.PAYMENT_METHOD IS NULL)
     AND ABS(SECONDS_BETWEEN(p.TXN_TS, o.ORDER_TS)) <= {fuzzy_window_sec}
    WHERE {fuzzy_enabled}
      AND NOT EXISTS (SELECT 1 FROM WRK_PAY k WHERE k.LINKED_ORDER_ID = o.ORDER_ID)
) WHERE N_FOR_ORDER = 1 AND N_FOR_PAY = 1;

MERGE INTO WRK_PAY p USING WRK_FUZZY f ON p.GATEWAY_TXN_ID = f.GATEWAY_TXN_ID
WHEN MATCHED THEN UPDATE SET p.LINKED_ORDER_ID = f.ORDER_ID, p.LINK_METHOD = 'FUZZY', p.FUZZY_GAP_SEC = f.GAP_SEC;

-- the earliest captured payment per order is the primary; later ones are possible duplicates
CREATE OR REPLACE TABLE WRK_PAY_RANKED AS
SELECT p.*,
       ROW_NUMBER() OVER (PARTITION BY p.LINKED_ORDER_ID ORDER BY p.TXN_TS, p.GATEWAY_TXN_ID) AS PAY_RANK,
       COUNT(*) OVER (PARTITION BY p.LINKED_ORDER_ID) AS PAY_COUNT,
       FIRST_VALUE(p.GATEWAY_TXN_ID) OVER (PARTITION BY p.LINKED_ORDER_ID ORDER BY p.TXN_TS, p.GATEWAY_TXN_ID) AS PRIMARY_TXN_ID,
       FIRST_VALUE(p.TXN_TS) OVER (PARTITION BY p.LINKED_ORDER_ID ORDER BY p.TXN_TS, p.GATEWAY_TXN_ID) AS PRIMARY_TS,
       FIRST_VALUE(p.GROSS_AMOUNT_MINOR) OVER (PARTITION BY p.LINKED_ORDER_ID ORDER BY p.TXN_TS, p.GATEWAY_TXN_ID) AS PRIMARY_GROSS
FROM WRK_PAY p
WHERE p.LINKED_ORDER_ID IS NOT NULL;

CREATE OR REPLACE TABLE WRK_REVERSALS AS
SELECT o.ORDER_ID,
       SUM(CASE WHEN g.TXN_TYPE = 'REFUND' THEN g.GROSS_AMOUNT_MINOR ELSE 0 END) AS REFUND_MINOR,
       SUM(CASE WHEN g.TXN_TYPE = 'CHARGEBACK' THEN g.GROSS_AMOUNT_MINOR ELSE 0 END) AS CHARGEBACK_MINOR,
       MAX(CASE WHEN g.TXN_TYPE = 'REFUND' THEN g.GATEWAY_TXN_ID END) AS REFUND_TXN_ID,
       MAX(CASE WHEN g.TXN_TYPE = 'CHARGEBACK' THEN g.GATEWAY_TXN_ID END) AS CHARGEBACK_TXN_ID
FROM WRK_GW g
JOIN ORDERS o ON o.ORDER_ID = g.NORM_REF
WHERE g.TXN_TYPE IN ('REFUND', 'CHARGEBACK')
GROUP BY o.ORDER_ID;

-- ============================================================ stage 3: settlement batches
CREATE OR REPLACE TABLE WRK_BATCH AS
SELECT g.SETTLEMENT_BATCH_ID AS BATCH_ID,
       MAX(g.EXPECTED_SETTLEMENT_DATE) AS EXPECTED_DATE,
       COUNT(*) AS TXN_COUNT,
       SUM(CASE WHEN g.TXN_TYPE = 'PAYMENT' THEN g.GROSS_AMOUNT_MINOR ELSE 0 END) AS GROSS_MINOR,
       SUM(CASE WHEN g.TXN_TYPE = 'PAYMENT' THEN g.FEE_MINOR + g.TAX_MINOR ELSE 0 END) AS FEES_MINOR,
       SUM(CASE WHEN g.TXN_TYPE <> 'PAYMENT' THEN g.NET_AMOUNT_MINOR ELSE 0 END) AS REVERSALS_MINOR,
       SUM(g.SIGNED_NET) AS EXPECTED_NET
FROM WRK_GW g
WHERE g.SETTLEMENT_MODE = 'BATCH' AND g.SETTLEMENT_BATCH_ID IS NOT NULL
GROUP BY g.SETTLEMENT_BATCH_ID;

CREATE OR REPLACE TABLE WRK_BATCH_LINK AS
SELECT bt.BATCH_ID, bk.BANK_TXN_ID, CAST('BATCH_REF' AS VARCHAR(20)) AS MATCH_STAGE
FROM WRK_BATCH bt
JOIN WRK_BANK_RANKED bk ON bk.BATCH_REF = bt.BATCH_ID AND bk.REF_RANK = 1;

-- constrained: a credit with no reference is linked to a batch only when the amount is
-- identical, the value date falls in [expected, expected + window] and the pair is unique
INSERT INTO WRK_BATCH_LINK
SELECT BATCH_ID, BANK_TXN_ID, 'BATCH_CONSTRAINED' FROM (
    SELECT bt.BATCH_ID, bk.BANK_TXN_ID,
           COUNT(*) OVER (PARTITION BY bt.BATCH_ID) AS N_FOR_BATCH,
           COUNT(*) OVER (PARTITION BY bk.BANK_TXN_ID) AS N_FOR_BANK
    FROM WRK_BATCH bt
    JOIN WRK_BANK bk
      ON bk.BATCH_REF IS NULL AND bk.PAY_REF IS NULL
     AND bk.SIGNED_AMOUNT = bt.EXPECTED_NET
     AND bk.VALUE_DATE BETWEEN bt.EXPECTED_DATE AND ADD_DAYS(bt.EXPECTED_DATE, {batch_window_days})
    WHERE NOT EXISTS (SELECT 1 FROM WRK_BATCH_LINK l WHERE l.BATCH_ID = bt.BATCH_ID)
) WHERE N_FOR_BATCH = 1 AND N_FOR_BANK = 1;

-- ============================================================ results (one row per entity)
CREATE OR REPLACE TABLE WRK_RESULTS (
    ENTITY_LEVEL VARCHAR(20), ENTITY_ID VARCHAR(60), ISSUE_CODE VARCHAR(40), MATCH_STAGE VARCHAR(20),
    EVIDENCE_TYPE VARCHAR(10), ORDER_ID VARCHAR(40), GATEWAY_TXN_ID VARCHAR(40), RELATED_TXN_ID VARCHAR(40),
    BANK_TXN_ID VARCHAR(40), SETTLEMENT_BATCH_ID VARCHAR(40), PAYMENT_METHOD VARCHAR(20),
    ORDER_AMOUNT_MINOR DECIMAL(18,0), GATEWAY_GROSS_MINOR DECIMAL(18,0), GATEWAY_FEE_MINOR DECIMAL(18,0),
    GATEWAY_NET_MINOR DECIMAL(18,0), BANK_AMOUNT_MINOR DECIMAL(18,0), EXPECTED_AMOUNT_MINOR DECIMAL(18,0),
    ACTUAL_AMOUNT_MINOR DECIMAL(18,0), DISCREPANCY_MINOR DECIMAL(18,0), EXPOSURE_MINOR DECIMAL(18,0),
    ORDER_TS TIMESTAMP, GATEWAY_TS TIMESTAMP, BANK_VALUE_DATE DATE, EXPECTED_SETTLEMENT_DATE DATE,
    REFERENCE_DATE DATE, EXPLANATION VARCHAR(2000), EVIDENCE VARCHAR(2000)
);

-- ------------------------------------------------------------ ORDER level
CREATE OR REPLACE TABLE WRK_ORDER_EVAL AS
SELECT x.*,
       x.EXP_FEE + FLOOR((x.EXP_FEE * x.GST_PCT + 50) / 100) AS EXP_FEE_TOTAL
FROM (
    SELECT o.ORDER_ID, o.ORDER_TS, o.AMOUNT_MINOR, o.ORDER_STATUS, o.PAYMENT_METHOD,
           p.GATEWAY_TXN_ID, p.ORDER_REF, p.TXN_TS, p.GROSS_AMOUNT_MINOR, p.FEE_MINOR + p.TAX_MINOR AS GW_FEE,
           p.NET_AMOUNT_MINOR, p.SETTLEMENT_MODE, p.SETTLEMENT_BATCH_ID, p.EXPECTED_SETTLEMENT_DATE,
           p.LINK_METHOD, p.FUZZY_GAP_SEC, COALESCE(p.PAY_COUNT, 0) AS PAY_COUNT,
           p.GROSS_AMOUNT_MINOR - o.AMOUNT_MINOR AS GW_DIFF,
           FLOOR((o.AMOUNT_MINOR * COALESCE(rc.FEE_BPS, 0) + 5000) / 10000) AS EXP_FEE,
           COALESCE(rc.FEE_BPS, 0) AS FEE_BPS, COALESCE(rc.GST_PCT, 0) AS GST_PCT,
           COALESCE(r.REFUND_MINOR, 0) AS REFUND_MINOR, COALESCE(r.CHARGEBACK_MINOR, 0) AS CHARGEBACK_MINOR,
           r.REFUND_TXN_ID, r.CHARGEBACK_TXN_ID,
           ib.BANK_TXN_ID AS INSTANT_BANK_TXN_ID, ib.VALUE_DATE AS INSTANT_VALUE_DATE,
           ib.SIGNED_AMOUNT AS INSTANT_BANK_AMOUNT
    FROM ORDERS o
    LEFT JOIN WRK_PAY_RANKED p ON p.LINKED_ORDER_ID = o.ORDER_ID AND p.PAY_RANK = 1
    LEFT JOIN WRK_REVERSALS r ON r.ORDER_ID = o.ORDER_ID
    LEFT JOIN FEE_RATE_CARD rc ON rc.PAYMENT_METHOD = o.PAYMENT_METHOD
    LEFT JOIN WRK_BANK_RANKED ib
           ON ib.PAY_REF = p.GATEWAY_TXN_ID AND ib.REF_RANK = 1 AND p.SETTLEMENT_MODE = 'INSTANT'
) x;

CREATE OR REPLACE TABLE WRK_ORDER_CLASS AS
SELECT e.*,
    CAST(CASE
        WHEN e.GATEWAY_TXN_ID IS NULL AND e.ORDER_STATUS = 'CANCELLED' THEN 'MATCHED'
        WHEN e.GATEWAY_TXN_ID IS NULL THEN 'MISSING_FROM_GATEWAY'
        WHEN e.ORDER_STATUS = 'CANCELLED' AND e.REFUND_MINOR < e.GROSS_AMOUNT_MINOR THEN 'REVERSAL_OR_CANCELLATION'
        WHEN e.CHARGEBACK_MINOR > 0 THEN 'REVERSAL_OR_CANCELLATION'
        WHEN e.ORDER_STATUS = 'REFUNDED' AND ABS(e.REFUND_MINOR - e.AMOUNT_MINOR) > {tol} THEN 'REVERSAL_OR_CANCELLATION'
        WHEN ABS(e.GW_DIFF) > {tol} AND e.EXP_FEE_TOTAL > 0 AND ABS(-e.GW_DIFF - e.EXP_FEE_TOTAL) <= {tol}
             THEN 'POSSIBLE_FEE_DIFFERENCE'
        WHEN ABS(e.GW_DIFF) > {tol} THEN 'AMOUNT_MISMATCH'
        WHEN e.SETTLEMENT_MODE = 'INSTANT' AND e.INSTANT_BANK_TXN_ID IS NULL
             AND DAYS_BETWEEN({as_of}, e.EXPECTED_SETTLEMENT_DATE) > {delay_days} THEN 'MISSING_FROM_BANK'
        WHEN e.SETTLEMENT_MODE = 'INSTANT' AND e.INSTANT_BANK_TXN_ID IS NULL THEN 'AWAITING_SETTLEMENT'
        WHEN e.SETTLEMENT_MODE = 'INSTANT' AND ABS(e.INSTANT_BANK_AMOUNT - e.NET_AMOUNT_MINOR) > {tol} THEN 'AMOUNT_MISMATCH'
        WHEN e.SETTLEMENT_MODE = 'INSTANT'
             AND DAYS_BETWEEN(e.INSTANT_VALUE_DATE, e.EXPECTED_SETTLEMENT_DATE) > {delay_days} THEN 'DELAYED_SETTLEMENT'
        ELSE 'MATCHED'
    END AS VARCHAR(40)) AS ISSUE_CODE,
    CAST(CASE
        WHEN e.GATEWAY_TXN_ID IS NULL AND e.ORDER_STATUS = 'CANCELLED' THEN 'EXACT'
        WHEN e.GATEWAY_TXN_ID IS NULL THEN 'NONE'
        WHEN e.LINK_METHOD = 'FUZZY' THEN 'FUZZY'
        WHEN e.GW_DIFF = 0 THEN 'EXACT'
        WHEN ABS(e.GW_DIFF) <= {tol} THEN 'TOLERANCE'
        ELSE 'KEY_ONLY'
    END AS VARCHAR(20)) AS MATCH_STAGE
FROM WRK_ORDER_EVAL e;

INSERT INTO WRK_RESULTS
SELECT 'ORDER', c.ORDER_ID, c.ISSUE_CODE, c.MATCH_STAGE,
    CASE WHEN c.ISSUE_CODE = 'POSSIBLE_FEE_DIFFERENCE' OR c.MATCH_STAGE = 'FUZZY' THEN 'SUSPECTED' ELSE 'CONFIRMED' END,
    c.ORDER_ID, c.GATEWAY_TXN_ID, COALESCE(c.CHARGEBACK_TXN_ID, c.REFUND_TXN_ID), c.INSTANT_BANK_TXN_ID,
    c.SETTLEMENT_BATCH_ID, c.PAYMENT_METHOD,
    c.AMOUNT_MINOR, c.GROSS_AMOUNT_MINOR, c.GW_FEE, c.NET_AMOUNT_MINOR, c.INSTANT_BANK_AMOUNT,
    -- expected / actual / discrepancy (actual - expected) for the leg that failed
    CASE WHEN c.ISSUE_CODE IN ('MISSING_FROM_BANK', 'DELAYED_SETTLEMENT', 'AWAITING_SETTLEMENT')
              OR (c.ISSUE_CODE = 'AMOUNT_MISMATCH' AND c.MATCH_STAGE <> 'KEY_ONLY') THEN c.NET_AMOUNT_MINOR
         WHEN c.ORDER_STATUS = 'CANCELLED' THEN 0
         ELSE c.AMOUNT_MINOR END,
    CASE WHEN c.ISSUE_CODE = 'MISSING_FROM_GATEWAY' THEN 0
         WHEN c.ISSUE_CODE IN ('MISSING_FROM_BANK', 'AWAITING_SETTLEMENT') THEN 0
         WHEN c.ISSUE_CODE IN ('DELAYED_SETTLEMENT')
              OR (c.ISSUE_CODE = 'AMOUNT_MISMATCH' AND c.MATCH_STAGE <> 'KEY_ONLY') THEN c.INSTANT_BANK_AMOUNT
         WHEN c.ORDER_STATUS = 'CANCELLED' THEN COALESCE(c.GROSS_AMOUNT_MINOR, 0) - c.REFUND_MINOR
         WHEN c.ISSUE_CODE = 'REVERSAL_OR_CANCELLATION' AND c.CHARGEBACK_MINOR > 0 THEN c.AMOUNT_MINOR - c.CHARGEBACK_MINOR
         WHEN c.ISSUE_CODE = 'REVERSAL_OR_CANCELLATION' THEN c.REFUND_MINOR
         ELSE c.GROSS_AMOUNT_MINOR END,
    NULL, NULL,
    c.ORDER_TS, c.TXN_TS, c.INSTANT_VALUE_DATE, c.EXPECTED_SETTLEMENT_DATE,
    CASE WHEN c.ISSUE_CODE IN ('MISSING_FROM_BANK', 'DELAYED_SETTLEMENT', 'AWAITING_SETTLEMENT')
         THEN c.EXPECTED_SETTLEMENT_DATE ELSE CAST(c.ORDER_TS AS DATE) END,
    CASE c.ISSUE_CODE
      WHEN 'MISSING_FROM_GATEWAY' THEN
        'Confirmed: order ' || c.ORDER_ID || ' (' || FMT_INR(c.AMOUNT_MINOR) || ', ' || c.ORDER_STATUS
        || ') has no captured gateway payment with this order reference, and no unique payment of the same amount and method within '
        || {fuzzy_window_min} || ' minutes was found. Suspected causes: payment never completed, or the gateway record is missing.'
      WHEN 'REVERSAL_OR_CANCELLATION' THEN
        CASE WHEN c.ORDER_STATUS = 'CANCELLED' THEN
          'Confirmed: the ledger marks the order CANCELLED, but gateway payment ' || c.GATEWAY_TXN_ID || ' captured '
          || FMT_INR(c.GROSS_AMOUNT_MINOR) || ' and only ' || FMT_INR(c.REFUND_MINOR)
          || ' has been refunded. Suspected cause: refund not initiated for a cancelled order.'
        WHEN c.CHARGEBACK_MINOR > 0 THEN
          'Confirmed: gateway recorded chargeback ' || c.CHARGEBACK_TXN_ID || ' of ' || FMT_INR(c.CHARGEBACK_MINOR)
          || ' against this order and deducted it from settlement, while the ledger still shows ' || c.ORDER_STATUS || '.'
        ELSE
          'Confirmed: the ledger marks the order REFUNDED, but gateway refunds total ' || FMT_INR(c.REFUND_MINOR)
          || ' against the order amount of ' || FMT_INR(c.AMOUNT_MINOR) || '. Suspected cause: refund not processed at the gateway.'
        END
      WHEN 'POSSIBLE_FEE_DIFFERENCE' THEN
        'Suspected: gateway gross ' || FMT_INR(c.GROSS_AMOUNT_MINOR) || ' is ' || FMT_INR(-c.GW_DIFF)
        || ' below the order amount ' || FMT_INR(c.AMOUNT_MINOR) || ', which equals the expected ' || c.PAYMENT_METHOD
        || ' fee + GST of ' || FMT_INR(c.EXP_FEE_TOTAL) || ' (rate card ' || CAST(c.FEE_BPS / 100 AS DECIMAL(6,2)) || '% + ' || c.GST_PCT
        || '% GST). The gateway may have netted its fee at source without itemising it.'
      WHEN 'AMOUNT_MISMATCH' THEN
        CASE WHEN c.MATCH_STAGE = 'KEY_ONLY' THEN
          'Confirmed: gateway payment ' || c.GATEWAY_TXN_ID || ' captured ' || FMT_INR(c.GROSS_AMOUNT_MINOR)
          || ' for an order of ' || FMT_INR(c.AMOUNT_MINOR) || ' (difference ' || FMT_INR(c.GW_DIFF)
          || '). This exceeds the ' || FMT_INR({tol}) || ' tolerance and does not equal the expected fee of '
          || FMT_INR(c.EXP_FEE_TOTAL) || '.'
        ELSE
          'Confirmed: instant settlement credit ' || c.INSTANT_BANK_TXN_ID || ' of ' || FMT_INR(c.INSTANT_BANK_AMOUNT)
          || ' differs from the gateway net amount ' || FMT_INR(c.NET_AMOUNT_MINOR) || '.'
        END
      WHEN 'MISSING_FROM_BANK' THEN
        'Confirmed: instant settlement of ' || FMT_INR(c.NET_AMOUNT_MINOR) || ' for payment ' || c.GATEWAY_TXN_ID
        || ' was due on ' || TO_CHAR(c.EXPECTED_SETTLEMENT_DATE, 'YYYY-MM-DD') || ', and no bank line references it '
        || DAYS_BETWEEN({as_of}, c.EXPECTED_SETTLEMENT_DATE) || ' days later.'
      WHEN 'DELAYED_SETTLEMENT' THEN
        'Confirmed: instant settlement ' || c.INSTANT_BANK_TXN_ID || ' of ' || FMT_INR(c.INSTANT_BANK_AMOUNT)
        || ' arrived on ' || TO_CHAR(c.INSTANT_VALUE_DATE, 'YYYY-MM-DD') || ', '
        || DAYS_BETWEEN(c.INSTANT_VALUE_DATE, c.EXPECTED_SETTLEMENT_DATE) || ' days after the expected date (threshold '
        || {delay_days} || ' days). The amount is correct.'
      WHEN 'AWAITING_SETTLEMENT' THEN
        'Instant settlement of ' || FMT_INR(c.NET_AMOUNT_MINOR) || ' is not yet credited but is still within the '
        || {delay_days} || '-day grace period.'
      ELSE
        CASE WHEN c.GATEWAY_TXN_ID IS NULL THEN 'Cancelled order with no captured payment: nothing to settle.'
             WHEN c.MATCH_STAGE = 'FUZZY' THEN
               'Suspected match: the gateway order reference '
               || CASE WHEN c.ORDER_REF IS NULL THEN '(blank)' ELSE '''' || c.ORDER_REF || '''' END
               || ' matches no ledger order, but payment ' || c.GATEWAY_TXN_ID || ' is the only payment of '
               || FMT_INR(c.GROSS_AMOUNT_MINOR) || ' by ' || c.PAYMENT_METHOD || ' within ' || {fuzzy_window_min}
               || ' minutes (' || c.FUZZY_GAP_SEC || ' s after the order), and this order is its only candidate.'
             WHEN c.MATCH_STAGE = 'TOLERANCE' THEN
               'Matched within tolerance: gateway ' || FMT_INR(c.GROSS_AMOUNT_MINOR) || ' vs order '
               || FMT_INR(c.AMOUNT_MINOR) || ' (difference ' || FMT_INR(c.GW_DIFF) || ' <= ' || FMT_INR({tol}) || ').'
             WHEN c.ORDER_STATUS = 'REFUNDED' THEN 'Refunded order: payment and refund ' || c.REFUND_TXN_ID || ' both match.'
             ELSE 'Exact match on order reference and amount.'
        END
    END,
    'order=' || c.ORDER_ID || '; status=' || c.ORDER_STATUS || '; order_amount=' || FMT_INR(c.AMOUNT_MINOR)
    || '; method=' || COALESCE(c.PAYMENT_METHOD, '-')
    || '; gateway_txn=' || COALESCE(c.GATEWAY_TXN_ID, '-') || '; gateway_ref=' || COALESCE(c.ORDER_REF, '-')
    || '; link=' || COALESCE(c.LINK_METHOD, '-')
    || '; gross=' || FMT_INR(c.GROSS_AMOUNT_MINOR) || '; fee_gst=' || FMT_INR(c.GW_FEE) || '; net=' || FMT_INR(c.NET_AMOUNT_MINOR)
    || '; expected_fee_gst=' || FMT_INR(c.EXP_FEE_TOTAL)
    || '; captured_payments=' || c.PAY_COUNT || '; refunds=' || FMT_INR(c.REFUND_MINOR)
    || '; chargebacks=' || FMT_INR(c.CHARGEBACK_MINOR)
    || '; settlement=' || COALESCE(c.SETTLEMENT_MODE, '-') || ' ' || COALESCE(c.SETTLEMENT_BATCH_ID, '')
    || '; expected_settlement=' || COALESCE(TO_CHAR(c.EXPECTED_SETTLEMENT_DATE, 'YYYY-MM-DD'), '-')
    || '; bank_txn=' || COALESCE(c.INSTANT_BANK_TXN_ID, '-')
FROM WRK_ORDER_CLASS c;

-- ------------------------------------------------------------ GATEWAY_TXN level
-- later captured payments for an order that already has one
INSERT INTO WRK_RESULTS
SELECT 'GATEWAY_TXN', p.GATEWAY_TXN_ID, 'POSSIBLE_DUPLICATE', CAST(p.LINK_METHOD AS VARCHAR(20)), 'SUSPECTED',
    p.LINKED_ORDER_ID, p.GATEWAY_TXN_ID, p.PRIMARY_TXN_ID, NULL, p.SETTLEMENT_BATCH_ID, p.PAYMENT_METHOD,
    o.AMOUNT_MINOR, p.GROSS_AMOUNT_MINOR, p.FEE_MINOR + p.TAX_MINOR, p.NET_AMOUNT_MINOR, NULL,
    0, p.GROSS_AMOUNT_MINOR, NULL, NULL,
    o.ORDER_TS, p.TXN_TS, NULL, p.EXPECTED_SETTLEMENT_DATE, CAST(p.TXN_TS AS DATE),
    'Suspected: payment ' || p.GATEWAY_TXN_ID || ' is captured payment #' || p.PAY_RANK || ' of ' || p.PAY_COUNT
    || ' for order ' || p.LINKED_ORDER_ID || '. It was captured ' || ROUND(SECONDS_BETWEEN(p.TXN_TS, p.PRIMARY_TS) / 60, 1)
    || ' minutes after ' || p.PRIMARY_TXN_ID || ' for ' || FMT_INR(p.GROSS_AMOUNT_MINOR)
    || CASE WHEN p.GROSS_AMOUNT_MINOR = p.PRIMARY_GROSS THEN ' (same amount)' ELSE ' (primary was ' || FMT_INR(p.PRIMARY_GROSS) || ')' END
    || ' with no refund linked to it. The customer may have been charged twice.',
    'order=' || p.LINKED_ORDER_ID || '; primary_txn=' || p.PRIMARY_TXN_ID || '; duplicate_txn=' || p.GATEWAY_TXN_ID
    || '; gross=' || FMT_INR(p.GROSS_AMOUNT_MINOR) || '; captured_payments=' || p.PAY_COUNT
    || '; batch=' || COALESCE(p.SETTLEMENT_BATCH_ID, '-')
FROM WRK_PAY_RANKED p
JOIN ORDERS o ON o.ORDER_ID = p.LINKED_ORDER_ID
WHERE p.PAY_RANK > 1;

-- payments, refunds and chargebacks that point at no ledger order
INSERT INTO WRK_RESULTS
SELECT 'GATEWAY_TXN', g.GATEWAY_TXN_ID, 'UNRESOLVED', 'NONE', 'SUSPECTED',
    NULL, g.GATEWAY_TXN_ID, NULL, NULL, g.SETTLEMENT_BATCH_ID, g.PAYMENT_METHOD,
    NULL, g.GROSS_AMOUNT_MINOR, g.FEE_MINOR + g.TAX_MINOR, g.NET_AMOUNT_MINOR, NULL,
    NULL, g.GROSS_AMOUNT_MINOR, NULL, NULL,
    NULL, g.TXN_TS, NULL, g.EXPECTED_SETTLEMENT_DATE, CAST(g.TXN_TS AS DATE),
    'Unresolved: gateway ' || LOWER(g.TXN_TYPE) || ' ' || g.GATEWAY_TXN_ID || ' of ' || FMT_INR(g.GROSS_AMOUNT_MINOR)
    || ' references ' || CASE WHEN g.ORDER_REF IS NULL THEN 'no order' ELSE 'order ''' || g.ORDER_REF || '''' END
    || ', which is not in the ledger, and no unique '
    || 'same-amount order was found within ' || {fuzzy_window_min} || ' minutes. Suspected causes: order missing '
    || 'from the ledger, or a mistyped reference.',
    'gateway_txn=' || g.GATEWAY_TXN_ID || '; type=' || g.TXN_TYPE || '; gateway_ref=' || COALESCE(g.ORDER_REF, '-')
    || '; gross=' || FMT_INR(g.GROSS_AMOUNT_MINOR) || '; method=' || COALESCE(g.PAYMENT_METHOD, '-')
    || '; batch=' || COALESCE(g.SETTLEMENT_BATCH_ID, '-')
FROM WRK_GW g
LEFT JOIN WRK_PAY p ON p.GATEWAY_TXN_ID = g.GATEWAY_TXN_ID
WHERE (g.TXN_TYPE = 'PAYMENT' AND p.LINKED_ORDER_ID IS NULL)
   OR (g.TXN_TYPE <> 'PAYMENT' AND NOT EXISTS (SELECT 1 FROM ORDERS o WHERE o.ORDER_ID = g.NORM_REF));

-- ------------------------------------------------------------ SETTLEMENT_BATCH level
CREATE OR REPLACE TABLE WRK_BATCH_CLASS AS
SELECT bt.*, l.BANK_TXN_ID, l.MATCH_STAGE AS LINK_STAGE, bk.VALUE_DATE, bk.SIGNED_AMOUNT AS BANK_AMOUNT,
    CAST(CASE
        WHEN l.BANK_TXN_ID IS NULL AND bt.EXPECTED_NET = 0 THEN 'MATCHED'
        WHEN l.BANK_TXN_ID IS NULL AND DAYS_BETWEEN({as_of}, bt.EXPECTED_DATE) > {delay_days} THEN 'MISSING_FROM_BANK'
        WHEN l.BANK_TXN_ID IS NULL THEN 'AWAITING_SETTLEMENT'
        WHEN ABS(bk.SIGNED_AMOUNT - bt.EXPECTED_NET) > {tol} AND bk.SIGNED_AMOUNT < bt.EXPECTED_NET
             AND (bt.EXPECTED_NET - bk.SIGNED_AMOUNT) * 100 <= bt.GROSS_MINOR * {fee_pct} THEN 'POSSIBLE_FEE_DIFFERENCE'
        WHEN ABS(bk.SIGNED_AMOUNT - bt.EXPECTED_NET) > {tol} THEN 'AMOUNT_MISMATCH'
        WHEN DAYS_BETWEEN(bk.VALUE_DATE, bt.EXPECTED_DATE) > {delay_days} THEN 'DELAYED_SETTLEMENT'
        ELSE 'MATCHED'
    END AS VARCHAR(40)) AS ISSUE_CODE
FROM WRK_BATCH bt
LEFT JOIN WRK_BATCH_LINK l ON l.BATCH_ID = bt.BATCH_ID
LEFT JOIN WRK_BANK bk ON bk.BANK_TXN_ID = l.BANK_TXN_ID;

INSERT INTO WRK_RESULTS
SELECT 'SETTLEMENT_BATCH', c.BATCH_ID, c.ISSUE_CODE, COALESCE(c.LINK_STAGE, 'NONE'),
    CASE WHEN c.ISSUE_CODE = 'POSSIBLE_FEE_DIFFERENCE' OR c.LINK_STAGE = 'BATCH_CONSTRAINED' THEN 'SUSPECTED' ELSE 'CONFIRMED' END,
    NULL, NULL, NULL, c.BANK_TXN_ID, c.BATCH_ID, NULL,
    NULL, c.GROSS_MINOR, c.FEES_MINOR, c.EXPECTED_NET, c.BANK_AMOUNT,
    c.EXPECTED_NET, COALESCE(c.BANK_AMOUNT, 0), NULL, NULL,
    NULL, NULL, c.VALUE_DATE, c.EXPECTED_DATE, c.EXPECTED_DATE,
    CASE c.ISSUE_CODE
      WHEN 'MISSING_FROM_BANK' THEN
        'Confirmed: settlement batch ' || c.BATCH_ID || ' (' || c.TXN_COUNT || ' transactions, net ' || FMT_INR(c.EXPECTED_NET)
        || ') was due on ' || TO_CHAR(c.EXPECTED_DATE, 'YYYY-MM-DD') || '. No bank line carries its reference, and no unreferenced '
        || 'credit of exactly that amount arrived within ' || {batch_window_days} || ' days.'
      WHEN 'AWAITING_SETTLEMENT' THEN
        'Batch ' || c.BATCH_ID || ' is not yet credited but is still within the ' || {delay_days} || '-day grace period.'
      WHEN 'POSSIBLE_FEE_DIFFERENCE' THEN
        'Suspected: bank credited ' || FMT_INR(c.BANK_AMOUNT) || ' for batch ' || c.BATCH_ID || ', '
        || FMT_INR(c.EXPECTED_NET - c.BANK_AMOUNT) || ' less than the gateway net of ' || FMT_INR(c.EXPECTED_NET) || ' ('
        || ROUND((c.EXPECTED_NET - c.BANK_AMOUNT) * 100 / NULLIF(c.GROSS_MINOR, 0), 2) || '% of gross, within the '
        || {fee_pct} || '% fee band). Suspected cause: an extra settlement fee or GST deducted that the gateway report does not show.'
      WHEN 'AMOUNT_MISMATCH' THEN
        'Confirmed: bank credited ' || FMT_INR(c.BANK_AMOUNT) || ' for batch ' || c.BATCH_ID || ' against a gateway net of '
        || FMT_INR(c.EXPECTED_NET) || ' (difference ' || FMT_INR(c.BANK_AMOUNT - c.EXPECTED_NET)
        || '). Too large, or in the wrong direction, to be a fee.'
      WHEN 'DELAYED_SETTLEMENT' THEN
        'Confirmed: batch ' || c.BATCH_ID || ' (' || FMT_INR(c.EXPECTED_NET) || ') was credited on '
        || TO_CHAR(c.VALUE_DATE, 'YYYY-MM-DD') || ', ' || DAYS_BETWEEN(c.VALUE_DATE, c.EXPECTED_DATE)
        || ' days after the expected ' || TO_CHAR(c.EXPECTED_DATE, 'YYYY-MM-DD') || ' (threshold ' || {delay_days}
        || ' days). The amount is correct.'
      ELSE
        CASE WHEN c.BANK_TXN_ID IS NULL THEN 'Batch nets to zero: nothing to settle.'
             WHEN c.LINK_STAGE = 'BATCH_CONSTRAINED' THEN
               'Suspected match: bank line ' || c.BANK_TXN_ID || ' has no batch reference, but it is the only credit of exactly '
               || FMT_INR(c.EXPECTED_NET) || ' within ' || {batch_window_days} || ' days of the expected date, and this is its only candidate batch.'
             ELSE 'Batch ' || c.BATCH_ID || ' credited in full by ' || c.BANK_TXN_ID || ' on ' || TO_CHAR(c.VALUE_DATE, 'YYYY-MM-DD') || '.'
        END
    END,
    'batch=' || c.BATCH_ID || '; transactions=' || c.TXN_COUNT || '; gross=' || FMT_INR(c.GROSS_MINOR)
    || '; fees_gst=' || FMT_INR(c.FEES_MINOR) || '; refunds_chargebacks=' || FMT_INR(c.REVERSALS_MINOR)
    || '; expected_net=' || FMT_INR(c.EXPECTED_NET) || '; expected_date=' || TO_CHAR(c.EXPECTED_DATE, 'YYYY-MM-DD')
    || '; bank_txn=' || COALESCE(c.BANK_TXN_ID, '-') || '; bank_amount=' || FMT_INR(c.BANK_AMOUNT)
    || '; value_date=' || COALESCE(TO_CHAR(c.VALUE_DATE, 'YYYY-MM-DD'), '-') || '; link=' || COALESCE(c.LINK_STAGE, '-')
FROM WRK_BATCH_CLASS c;

-- ------------------------------------------------------------ BANK_TXN level
CREATE OR REPLACE TABLE WRK_BANK_CLASS AS
SELECT b.*,
       COALESCE(cand.N, 0) AS CANDIDATES,
       kb.BATCH_ID IS NOT NULL AS KNOWN_BATCH,
       kp.GATEWAY_TXN_ID IS NOT NULL AS KNOWN_PAYMENT
FROM WRK_BANK_RANKED b
LEFT JOIN WRK_BATCH_LINK l ON l.BANK_TXN_ID = b.BANK_TXN_ID
LEFT JOIN WRK_BATCH kb ON kb.BATCH_ID = b.BATCH_REF
LEFT JOIN WRK_GW kp ON kp.GATEWAY_TXN_ID = b.PAY_REF
LEFT JOIN (
    SELECT bk.BANK_TXN_ID, COUNT(*) AS N
    FROM WRK_BANK bk
    JOIN WRK_BATCH bt
      ON bt.EXPECTED_NET = bk.SIGNED_AMOUNT
     AND bk.VALUE_DATE BETWEEN bt.EXPECTED_DATE AND ADD_DAYS(bt.EXPECTED_DATE, {batch_window_days})
    GROUP BY bk.BANK_TXN_ID
) cand ON cand.BANK_TXN_ID = b.BANK_TXN_ID
WHERE l.BANK_TXN_ID IS NULL
  -- the first line referencing an instant payment is that payment's settlement
  AND NOT (b.REF_RANK = 1 AND kp.SETTLEMENT_MODE IS NOT NULL AND kp.SETTLEMENT_MODE = 'INSTANT');

INSERT INTO WRK_RESULTS
SELECT 'BANK_TXN', b.BANK_TXN_ID,
    CASE WHEN b.REF_RANK > 1 THEN 'POSSIBLE_DUPLICATE' ELSE 'UNRESOLVED' END,
    'NONE', 'SUSPECTED',
    NULL, b.PAY_REF, CASE WHEN b.REF_RANK > 1 THEN b.FIRST_TXN_ID END, b.BANK_TXN_ID, b.BATCH_REF, NULL,
    NULL, NULL, NULL, NULL, b.SIGNED_AMOUNT,
    CASE WHEN b.REF_RANK > 1 THEN 0 END, b.SIGNED_AMOUNT, NULL, NULL,
    NULL, NULL, b.VALUE_DATE, NULL, b.VALUE_DATE,
    CASE WHEN b.REF_RANK > 1 THEN
        'Suspected: bank line ' || b.BANK_TXN_ID || ' (' || FMT_INR(b.SIGNED_AMOUNT) || ' on ' || TO_CHAR(b.VALUE_DATE, 'YYYY-MM-DD')
        || ') carries the same reference ' || COALESCE(b.BATCH_REF, b.PAY_REF) || ' as ' || b.FIRST_TXN_ID
        || CASE WHEN b.SIGNED_AMOUNT = b.FIRST_AMOUNT THEN ', for the same amount' ELSE ' (which was ' || FMT_INR(b.FIRST_AMOUNT) || ')' END
        || '. The settlement may have been credited twice.'
    WHEN b.BATCH_REF IS NOT NULL AND NOT b.KNOWN_BATCH THEN
        'Unresolved: bank line references batch ' || b.BATCH_REF || ', which does not exist in the gateway data.'
    WHEN b.PAY_REF IS NOT NULL AND NOT b.KNOWN_PAYMENT THEN
        'Unresolved: bank line references payment ' || b.PAY_REF || ', which does not exist in the gateway data.'
    ELSE
        'Unresolved: ' || CASE WHEN b.DIRECTION = 'CR' THEN 'credit' ELSE 'debit' END || ' of ' || FMT_INR(b.AMOUNT_MINOR)
        || ' on ' || TO_CHAR(b.VALUE_DATE, 'YYYY-MM-DD') || ' ("' || COALESCE(b.NARRATION, '') || '") has no settlement reference, and '
        || b.CANDIDATES || ' settlement batches have that exact amount within the date window (exactly 1 is needed to link it).'
    END,
    'bank_txn=' || b.BANK_TXN_ID || '; value_date=' || TO_CHAR(b.VALUE_DATE, 'YYYY-MM-DD') || '; amount=' || FMT_INR(b.SIGNED_AMOUNT)
    || '; narration=' || COALESCE(b.NARRATION, '-') || '; utr=' || COALESCE(b.BANK_REFERENCE, '-')
    || '; batch_ref=' || COALESCE(b.BATCH_REF, '-') || '; payment_ref=' || COALESCE(b.PAY_REF, '-')
    || '; candidate_batches=' || b.CANDIDATES
FROM WRK_BANK_CLASS b;

-- ============================================================ exposure, ageing, persist
UPDATE WRK_RESULTS SET DISCREPANCY_MINOR = ACTUAL_AMOUNT_MINOR - EXPECTED_AMOUNT_MINOR
WHERE ACTUAL_AMOUNT_MINOR IS NOT NULL AND EXPECTED_AMOUNT_MINOR IS NOT NULL;

UPDATE WRK_RESULTS SET EXPOSURE_MINOR = CASE
    WHEN ISSUE_CODE IN ('MATCHED', 'AWAITING_SETTLEMENT', 'DELAYED_SETTLEMENT') THEN 0
    WHEN ENTITY_LEVEL = 'GATEWAY_TXN' THEN ABS(GATEWAY_GROSS_MINOR)
    WHEN ENTITY_LEVEL = 'BANK_TXN' THEN ABS(BANK_AMOUNT_MINOR)
    ELSE ABS(COALESCE(DISCREPANCY_MINOR, 0)) END;

INSERT INTO RECONCILIATION_RESULTS (
    RESULT_ID, RUN_ID, ENTITY_LEVEL, ENTITY_ID, EXCEPTION_KEY, ISSUE_CODE, IS_EXCEPTION, MATCH_STAGE, EVIDENCE_TYPE,
    ORDER_ID, GATEWAY_TXN_ID, RELATED_TXN_ID, BANK_TXN_ID, SETTLEMENT_BATCH_ID, PAYMENT_METHOD,
    ORDER_AMOUNT_MINOR, GATEWAY_GROSS_MINOR, GATEWAY_FEE_MINOR, GATEWAY_NET_MINOR, BANK_AMOUNT_MINOR,
    EXPECTED_AMOUNT_MINOR, ACTUAL_AMOUNT_MINOR, DISCREPANCY_MINOR, EXPOSURE_MINOR,
    ORDER_TS, GATEWAY_TS, BANK_VALUE_DATE, EXPECTED_SETTLEMENT_DATE, REFERENCE_DATE, AGE_DAYS,
    EXPLANATION, EVIDENCE, RESOLUTION_STATUS, CREATED_AT)
SELECT {run_id} * 10000000 + ROW_NUMBER() OVER (ORDER BY r.ENTITY_LEVEL, r.ENTITY_ID),
    {run_id}, r.ENTITY_LEVEL, r.ENTITY_ID, r.ENTITY_LEVEL || ':' || r.ENTITY_ID || ':' || r.ISSUE_CODE,
    r.ISSUE_CODE, r.ISSUE_CODE NOT IN ('MATCHED', 'AWAITING_SETTLEMENT'), r.MATCH_STAGE, r.EVIDENCE_TYPE,
    r.ORDER_ID, r.GATEWAY_TXN_ID, r.RELATED_TXN_ID, r.BANK_TXN_ID, r.SETTLEMENT_BATCH_ID, r.PAYMENT_METHOD,
    r.ORDER_AMOUNT_MINOR, r.GATEWAY_GROSS_MINOR, r.GATEWAY_FEE_MINOR, r.GATEWAY_NET_MINOR, r.BANK_AMOUNT_MINOR,
    r.EXPECTED_AMOUNT_MINOR, r.ACTUAL_AMOUNT_MINOR, r.DISCREPANCY_MINOR, r.EXPOSURE_MINOR,
    r.ORDER_TS, r.GATEWAY_TS, r.BANK_VALUE_DATE, r.EXPECTED_SETTLEMENT_DATE, r.REFERENCE_DATE,
    GREATEST(0, DAYS_BETWEEN({as_of}, r.REFERENCE_DATE)),
    r.EXPLANATION, r.EVIDENCE,
    CASE WHEN r.ISSUE_CODE IN ('MATCHED', 'AWAITING_SETTLEMENT') THEN 'CLEARED' ELSE 'OPEN' END,
    CURRENT_TIMESTAMP
FROM WRK_RESULTS r;

-- carry analyst decisions forward: the latest RESOLUTION_LOG entry per exception key wins
MERGE INTO RECONCILIATION_RESULTS t
USING (
    SELECT EXCEPTION_KEY, NEW_STATUS FROM (
        SELECT EXCEPTION_KEY, NEW_STATUS,
               ROW_NUMBER() OVER (PARTITION BY EXCEPTION_KEY ORDER BY CHANGED_AT DESC, LOG_ID DESC) AS RN
        FROM RESOLUTION_LOG
    ) WHERE RN = 1
) s ON t.EXCEPTION_KEY = s.EXCEPTION_KEY AND t.RUN_ID = {run_id} AND t.IS_EXCEPTION
WHEN MATCHED THEN UPDATE SET t.RESOLUTION_STATUS = s.NEW_STATUS;
