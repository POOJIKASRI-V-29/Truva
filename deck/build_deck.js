// Builds deck/Kanakku_Pitch.pptx. Requires pptxgenjs (npm install pptxgenjs@3.12.0).
// Every figure below was measured on the bundled sample (seed 42, run 6) — see README "Measured results".
const path = require("path");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.APPLY_THEME || "./apply_theme.js");

const THEME = {
  name: "Kanakku Navy",
  headFontFace: "Calibri",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "0B1F3A", lt1: "FFFFFF", dk2: "1E3A8A", lt2: "EEF3FB",
    accent1: "1E3A8A", accent2: "3B82F6", accent3: "D97706", accent4: "64748B",
    accent5: "93C5FD", accent6: "B42318", hlink: "1E3A8A", folHlink: "64748B",
  },
};
const HEX = { navy: "0B1F3A", blue: "1E3A8A", sky: "3B82F6", ice: "EEF3FB", amber: "D97706", slate: "64748B",
              mist: "93C5FD", red: "B42318", grid: "E2E8F0" };

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Kanakku — pitch deck";
pres.author = "Kanakku";
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "KK_DARK",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 2.2, w: 7.2, h: 1.4, fontSize: 66, bold: true, align: "left",
                                color: C.background1, valign: "middle", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "KK_CONTENT",
  background: { color: C.background1 },
  margin: [0.5, 0.6, 0.6, 0.6],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.9, fontSize: 34, bold: true, align: "left",
                                color: C.text1, valign: "middle", margin: 0 }, text: "" } },
    { text: { text: "Kanakku · ExaThon India 2026 · synthetic data only",
              options: { x: 0.6, y: 7.0, w: 7, h: 0.3, fontSize: 10, color: C.accent4, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: HEX.slate, align: "right" },
});

const text = (slide, t, o) => slide.addText(t, { isTextBox: true, margin: 0, ...o });
const circle = (slide, x, y, d, fill, label, labelColor, size, name) => {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill }, objectName: name });
  if (label) text(slide, label, { x, y, w: d, h: d, align: "center", valign: "middle", fontSize: size, bold: true, color: labelColor });
};
const card = (slide, x, y, w, h, name) =>
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.12, fill: { color: C.background2 },
                                                  line: { color: C.background2 }, objectName: name });

// ---------------------------------------------------------------- 1 title
pres.addSection({ title: "Opening" });
let s = pres.addSlide({ masterName: "KK_DARK", sectionTitle: "Opening" });
s.addText("Kanakku", { placeholder: "title" });
text(s, "Every number tells a story.", { x: 0.7, y: 3.6, w: 7.2, h: 0.6, fontSize: 28, italic: true, color: C.accent5 });
text(s, "Three-way payment reconciliation with break explanations, built on Exasol Personal",
     { x: 0.7, y: 4.45, w: 6.6, h: 0.9, fontSize: 18, color: C.background1 });
text(s, "ExaThon India 2026  ·  Track 01 — Money, Risk & Regulation",
     { x: 0.7, y: 6.4, w: 8, h: 0.4, fontSize: 14, color: C.accent5 });
[["Ledger", 8.55, 1.55], ["Gateway", 10.25, 1.55], ["Bank", 9.4, 3.0]].forEach(([lbl, x, y], i) => {
  s.addShape(pres.shapes.OVAL, { x, y, w: 2.6, h: 2.6, fill: { color: [HEX.sky, HEX.mist, HEX.amber][i], transparency: 55 },
                                 line: { color: HEX.ice, width: 1.5 }, objectName: `venn-${lbl}` });
});
text(s, "Ledger", { x: 8.65, y: 2.15, w: 1.6, h: 0.5, fontSize: 16, bold: true, color: C.background1, align: "center" });
text(s, "Gateway", { x: 11.0, y: 2.15, w: 1.7, h: 0.5, fontSize: 16, bold: true, color: C.background1, align: "center" });
text(s, "Bank", { x: 9.9, y: 4.75, w: 1.6, h: 0.5, fontSize: 16, bold: true, color: C.background1, align: "center" });
text(s, "₹", { x: 10.2, y: 2.75, w: 1.0, h: 0.8, fontSize: 36, bold: true, color: C.background1, align: "center", valign: "middle" });
s.addNotes("An online merchant in India sees every sale three times: in its order ledger, in the payment gateway report and in the bank statement. Kanakku reconciles all three inside Exasol and explains every break.");

// ---------------------------------------------------------------- 2 problem
pres.addSection({ title: "Problem" });
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Problem" });
s.addText("Three records of every sale never agree", { placeholder: "title" });
const srcs = [
  ["L", "Order ledger", "ORD-0001234 · ₹1,499.00 · card · COMPLETED"],
  ["G", "Payment gateway", "Captured ₹1,499.00 · fee + GST ₹33.62 · net ₹1,465.38 · batch STL-…-02, T+2"],
  ["B", "Bank statement", "₹1,32,410.55 credited · “NEFT CR SETTLEMENT/STL-…-02” · ~90 payments in one line"],
];
srcs.forEach(([ic, h, d], i) => {
  const x = 0.6 + i * 4.1;
  card(s, x, 1.55, 3.85, 2.3, `source-${i}`);
  circle(s, x + 0.3, 1.8, 0.7, [HEX.blue, HEX.sky, HEX.amber][i], ic, "FFFFFF", 20, `source-icon-${i}`);
  text(s, h, { x: x + 1.15, y: 1.85, w: 2.5, h: 0.6, fontSize: 20, bold: true, color: C.text1, valign: "middle" });
  text(s, d, { x: x + 0.3, y: 2.7, w: 3.3, h: 1.0, fontSize: 14, color: C.text1, valign: "top" });
});
text(s, "What breaks the match", { x: 0.6, y: 4.2, w: 6, h: 0.45, fontSize: 20, bold: true, color: C.text2 });
const breaks = ["Fees & GST netted at source", "Batch settlements, T+2", "Late credits", "Double charges & double credits",
                "Missing orders, payments, batches", "Refunds, chargebacks, cancellations"];
breaks.forEach((b, i) => {
  const x = 0.6 + (i % 3) * 4.1, y = 4.8 + Math.floor(i / 3) * 0.75;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 3.85, h: 0.6, rectRadius: 0.3, fill: { color: C.background1 },
                                              line: { color: C.accent2, width: 1.25 }, objectName: `break-${i}` });
  text(s, b, { x: x + 0.2, y, w: 3.45, h: 0.6, fontSize: 14, color: C.text1, valign: "middle" });
});
text(s, "Today: month-end spreadsheets, late closes, refunds nobody chased, cash nobody can explain.",
     { x: 0.6, y: 6.35, w: 12.1, h: 0.45, fontSize: 15, italic: true, color: C.accent3 });
s.addNotes("Fees, GST, T+2 batch settlements, refunds, chargebacks and duplicates mean the three never agree. Finance teams reconcile by hand every month-end.");

// ---------------------------------------------------------------- 3 how it works
pres.addSection({ title: "Solution" });
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Solution" });
s.addText("How Kanakku works", { placeholder: "title" });
const steps = [
  ["Validate & load", "Row-level checks, reasons + line numbers, MERGE on business keys"],
  ["Match in Exasol", "Exact → tolerance → settlement batch → conservative fuzzy"],
  ["Explain", "Ids, amounts, dates, batch, stage — tagged Confirmed or Suspected"],
  ["Prioritise", "0–100 score: amount, age, severity, close impact"],
  ["Resolve", "Status + notes in an append-only log, carried to every rerun"],
];
steps.forEach(([h, d], i) => {
  const x = 0.6 + i * 2.48;
  circle(s, x + 0.75, 1.6, 0.85, i === 1 ? HEX.amber : HEX.blue, String(i + 1), "FFFFFF", 24, `step-${i}`);
  if (i < steps.length - 1)
    s.addShape(pres.shapes.LINE, { x: x + 1.7, y: 2.025, w: 1.5, h: 0, line: { color: HEX.mist, width: 2, endArrowType: "triangle" },
                                   objectName: `arrow-${i}` });
  text(s, h, { x, y: 2.6, w: 2.35, h: 0.5, fontSize: 18, bold: true, color: C.text1, align: "center" });
  text(s, d, { x: x + 0.05, y: 3.1, w: 2.25, h: 1.3, fontSize: 14, color: C.text1, align: "center", valign: "top" });
});
card(s, 0.6, 4.65, 12.1, 2.05, "stages-card");
text(s, "Four matching stages, one verdict per record", { x: 0.9, y: 4.8, w: 11.5, h: 0.45, fontSize: 18, bold: true, color: C.text2 });
const stages = [
  ["Exact", "order reference + amount"], ["Tolerance", "≤ ₹1.00 rounding (configurable)"],
  ["Batch", "narration ref, or unique amount + date window"], ["Fuzzy", "only the single same-amount payment ±15 min"],
];
stages.forEach(([h, d], i) => {
  const x = 0.9 + i * 2.95;
  text(s, [{ text: h, options: { bold: true, color: C.accent1, breakLine: true } }, { text: d, options: { color: C.text1 } }],
       { x, y: 5.35, w: 2.75, h: 1.1, fontSize: 14, valign: "top" });
});
s.addNotes("Ingestion validates every row and merges on business keys, so reloads are no-ops. Four SQL stages run inside Exasol; every non-exact link must be unique in both directions. Each record gets exactly one issue code, so nothing is double-counted.");

// ---------------------------------------------------------------- 4 exasol
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Solution" });
s.addText("Exasol does the heavy lifting", { placeholder: "title" });
card(s, 0.6, 1.55, 3.9, 5.1, "exasol-stat");
text(s, "~1 s", { x: 0.9, y: 1.9, w: 3.3, h: 1.3, fontSize: 66, bold: true, color: C.accent1 });
text(s, "for all four matching stages, classification, explanations and scoring on 10,005 orders",
     { x: 0.9, y: 3.25, w: 3.3, h: 1.4, fontSize: 16, color: C.text1 });
text(s, "Measured: 998–1,322 ms per run, Exasol Personal 2026.2 in a local 4-vCPU / 6 GB VM on an Apple M4",
     { x: 0.9, y: 5.2, w: 3.3, h: 1.2, fontSize: 12, color: C.accent4 });
const feats = [
  ["MERGE", "Idempotent loads and status carry-forward"],
  ["Window functions", "Duplicate ranking; uniqueness checks for every non-exact link"],
  ["REGEXP_SUBSTR", "Batch and payment references pulled from bank narrations"],
  ["DAYS_ / SECONDS_BETWEEN", "Ageing, delay thresholds, fuzzy time windows"],
  ["SQL UDF  FMT_INR", "Rupee-formatted explanations written in SQL"],
  ["Transactions", "A failed run rolls back completely and is logged FAILED"],
];
feats.forEach(([h, d], i) => {
  const x = 4.8 + (i % 2) * 4.0, y = 1.55 + Math.floor(i / 2) * 1.75;
  card(s, x, y, 3.85, 1.55, `feature-${i}`);
  text(s, h, { x: x + 0.25, y: y + 0.2, w: 3.4, h: 0.45, fontSize: 17, bold: true, color: C.text2 });
  text(s, d, { x: x + 0.25, y: y + 0.7, w: 3.4, h: 0.75, fontSize: 14, color: C.text1, valign: "top" });
});
s.addNotes("All matching and aggregation is SQL in Exasol. Results, run history, ingestion audit and the resolution log all live in one Kanakku schema.");

// ---------------------------------------------------------------- 5 results
pres.addSection({ title: "Results" });
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Results" });
s.addText("Results on 10,005 synthetic orders", { placeholder: "title" });
const stats = [["94.70%", "orders matched (9,475)"], ["670", "exceptions, each explained"], ["₹22.8 L", "open discrepancy exposure"]];
stats.forEach(([v, l], i) => {
  const y = 1.55 + i * 1.75;
  text(s, v, { x: 0.6, y, w: 4.2, h: 1.0, fontSize: 54, bold: true, color: i === 2 ? C.accent3 : C.accent1 });
  text(s, l, { x: 0.6, y: y + 0.95, w: 4.2, h: 0.45, fontSize: 16, color: C.text1 });
});
const issues = ["Missing from gateway", "Amount mismatch", "Possible fee difference", "Reversal / cancellation",
                "Possible duplicate", "Unresolved", "Delayed settlement", "Missing from bank"];
const counts = [160, 123, 104, 100, 83, 40, 31, 29];
s.addChart(pres.charts.BAR, [{ name: "Exceptions", labels: issues.slice().reverse(), values: counts.slice().reverse() }], {
  x: 5.1, y: 1.45, w: 7.6, h: 5.3, barDir: "bar", chartColors: [HEX.blue],
  showTitle: true, title: "Exceptions by issue (run 6)", titleFontSize: 16, titleColor: HEX.navy, titleFontFace: "+mn-lt",
  showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: HEX.navy, dataLabelFontFace: "+mn-lt",
  catAxisLabelColor: HEX.navy, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt",
  valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 45,
});
text(s, "Orders: 9,224 exact · 200 within tolerance · 51 fuzzy (suspected). Batches: 96 / 113 matched (8 via constrained amount + date).",
     { x: 0.6, y: 6.45, w: 12.1, h: 0.4, fontSize: 12, color: C.accent4 });
s.addNotes("94.7% of orders match. 670 exceptions remain with about 22.8 lakh rupees of exposure. Every number here comes from Exasol, not a mock.");

// ---------------------------------------------------------------- 6 explain & prioritise
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Results" });
s.addText("Every break explained, ranked and tracked", { placeholder: "title" });
card(s, 0.6, 1.55, 7.3, 5.1, "example-card");
s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.9, y: 1.8, w: 2.4, h: 0.45, rectRadius: 0.22, fill: { color: C.text1 },
                                            line: { color: C.text1 }, objectName: "chip-code" });
text(s, "MISSING_FROM_BANK", { x: 0.9, y: 1.8, w: 2.4, h: 0.45, fontSize: 12, bold: true, color: C.background1, align: "center", valign: "middle" });
s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 3.45, y: 1.8, w: 1.4, h: 0.45, rectRadius: 0.22, fill: { color: C.accent6 },
                                            line: { color: C.accent6 }, objectName: "chip-band" });
text(s, "HIGH · 100", { x: 3.45, y: 1.8, w: 1.4, h: 0.45, fontSize: 12, bold: true, color: C.background1, align: "center", valign: "middle" });
text(s, "Confirmed: settlement batch STL-20260909-01 (78 transactions, net ₹99,700.69) was due on 2026-09-09. No bank line carries its reference, and no unreferenced credit of exactly that amount arrived within 7 days.",
     { x: 0.9, y: 2.5, w: 6.7, h: 1.6, fontSize: 16, color: C.text1, valign: "top" });
const parts = [["Amount at risk", 40, 40], ["Ageing (33 days)", 25, 25], ["Severity", 25, 25], ["Close-period impact", 10, 10]];
parts.forEach(([l, v, max], i) => {
  const y = 4.3 + i * 0.55;
  text(s, l, { x: 0.9, y, w: 2.4, h: 0.4, fontSize: 14, color: C.text1, valign: "middle" });
  s.addShape(pres.shapes.RECTANGLE, { x: 3.4, y: y + 0.08, w: 3.6 * (v / 40), h: 0.26, fill: { color: C.accent1 },
                                      line: { color: C.accent1 }, objectName: `score-bar-${i}` });
  text(s, `+${v}`, { x: 3.5 + 3.6 * (v / 40), y, w: 0.6, h: 0.4, fontSize: 14, bold: true, color: C.text2, valign: "middle" });
});
s.addChart(pres.charts.DOUGHNUT, [{ name: "Exceptions", labels: ["High (≥70)", "Medium (50–69)", "Low (<50)"], values: [165, 364, 141] }], {
  x: 8.2, y: 1.45, w: 4.5, h: 4.3, holeSize: 58, chartColors: [HEX.red, HEX.amber, HEX.slate],
  showTitle: true, title: "Priority bands", titleFontSize: 16, titleColor: HEX.navy, titleFontFace: "+mn-lt",
  showValue: true, showPercent: false, dataLabelColor: "FFFFFF", dataLabelFontSize: 13, dataLabelFontFace: "+mn-lt",
  showLegend: true, legendPos: "b", legendFontSize: 12, legendColor: HEX.navy, legendFontFace: "+mn-lt",
});
text(s, "An investigation-priority heuristic with published weights — not a fraud probability.",
     { x: 8.2, y: 5.9, w: 4.5, h: 0.8, fontSize: 14, italic: true, color: C.accent4 });
s.addNotes("The top item: a 78-payment batch that never reached the bank. Confirmed, because the absence is visible in the records. The score breakdown is shown with every exception. Analysts set a status and note; it goes to an append-only log in Exasol and carries forward to future runs.");

// ---------------------------------------------------------------- 7 evaluation
s = pres.addSlide({ masterName: "KK_CONTENT", sectionTitle: "Results" });
s.addText("Measured against a hidden answer key", { placeholder: "title" });
const ev = [["97.0%", "precision"], ["100%", "recall"], ["98.5%", "F1"], ["111 / 111", "tests passing"]];
ev.forEach(([v, l], i) => {
  const x = 0.6 + i * 3.05;
  card(s, x, 1.55, 2.85, 1.9, `eval-${i}`);
  text(s, v, { x: x + 0.2, y: 1.7, w: 2.45, h: 1.0, fontSize: 44, bold: true, color: C.accent1, align: "center" });
  text(s, l, { x: x + 0.2, y: 2.75, w: 2.45, h: 0.45, fontSize: 16, color: C.text1, align: "center" });
});
const evRows = [
  ["How", "The generator writes ground-truth labels to a separate file and table. The matching SQL never reads them — a test fails if it does."],
  ["Errors", "650 true positives, 0 missed. All 20 false positives come from 5 deliberately ambiguous pairs (same amount and method, 2 minutes apart, no reference): Kanakku refuses to guess."],
  ["Caveat", "The generator and the rules share an author, so this shows consistency on known scenarios — not accuracy on real gateway or bank files."],
  ["Tests", "92 unit tests + 19 integration tests on real Exasol: tolerance boundary, batches, duplicates, fees, reversals, reruns, rollback, resolution carry-forward."],
];
evRows.forEach(([h, d], i) => {
  const y = 3.8 + i * 0.75;
  circle(s, 0.6, y + 0.05, 0.45, i === 2 ? HEX.amber : HEX.blue, "", "FFFFFF", 12, `eval-dot-${i}`);
  text(s, [{ text: h + "  ", options: { bold: true, color: C.text2 } }, { text: d, options: { color: C.text1 } }],
       { x: 1.25, y, w: 11.45, h: 0.6, fontSize: 14, valign: "middle" });
});
s.addNotes("Precision 97%, recall 100% on the bundled 10,000-order sample. The false positives are intentional ambiguity. Because I wrote both the generator and the rules, this is a consistency check, not a real-world accuracy claim.");

// ---------------------------------------------------------------- 8 close
pres.addSection({ title: "Close" });
s = pres.addSlide({ masterName: "KK_DARK", sectionTitle: "Close" });
text(s, "Where it stops today", { x: 0.7, y: 0.7, w: 5.8, h: 0.6, fontSize: 24, bold: true, color: C.accent5 });
text(s, [
  { text: "Synthetic, INR-only data; real gateway formats need column mappings", options: { bullet: true, breakLine: true } },
  { text: "Partial / multi-part settlements read as possible duplicates", options: { bullet: true, breakLine: true } },
  { text: "Static fee rate card; score weights not calibrated on real outcomes", options: { bullet: true, breakLine: true } },
  { text: "Single-analyst tool: no login or approvals", options: { bullet: true } },
], { x: 0.7, y: 1.4, w: 5.8, h: 2.6, fontSize: 16, color: C.background1, paraSpaceAfter: 8, valign: "top" });
text(s, "Next", { x: 7.0, y: 0.7, w: 5.6, h: 0.6, fontSize: 24, bold: true, color: C.accent5 });
text(s, [
  { text: "Adapters for real gateway and bank statement formats", options: { bullet: true, breakLine: true } },
  { text: "Partial settlements, multi-currency, per-merchant pricing", options: { bullet: true, breakLine: true } },
  { text: "Scheduled daily runs and maker-checker approvals", options: { bullet: true } },
], { x: 7.0, y: 1.4, w: 5.6, h: 2.6, fontSize: 16, color: C.background1, paraSpaceAfter: 8, valign: "top" });
text(s, "Kanakku", { x: 0.7, y: 4.5, w: 7, h: 1.2, fontSize: 60, bold: true, color: C.background1 });
text(s, "Every number tells a story.", { x: 0.7, y: 5.7, w: 7, h: 0.6, fontSize: 28, italic: true, color: C.accent5 });
text(s, "MIT licensed · python -m kanakku demo · Exasol Personal", { x: 0.7, y: 6.5, w: 8, h: 0.4, fontSize: 14, color: C.accent5 });
s.addNotes("Kanakku: Exasol does the heavy matching, every exception is explained with evidence, and analysts know what to look at first.");

(async () => {
  const out = path.join(__dirname, "Kanakku_Pitch.pptx");
  await pres.writeFile({ fileName: out });
  await applyTheme(out, THEME);
  console.log("wrote", out);
})();
