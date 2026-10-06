# Source and metric mapping guide

A source is evidence, not a financial interpretation. Each import requires explicit entity, branch, register kind, worksheet(s), header row, mappings and reviewed field meanings. A canonical field is mapped to one source column. Amounts, identifiers and dates are not guessed. Download register-specific templates in Imports & Settings; matching files are in `templates/`.

## Source authorities

| Source/register | Authority | Never infer |
|---|---|---|
| Journal | Categorised debits/credits and voucher balance; period P&L | Cash receipts as revenue; bills as extra GL income |
| Trial balance | One compatible snapshot per branch/account; explicit opening/movement/closing and movement-period start | Combining TB and journals; period flow from cumulative closing alone |
| Opening balance | Latest reviewed snapshot per branch/account plus subsequent GL movements | Adding multiple snapshots |
| Billing | Final signed net patient liability/operational billing | Pharmacy inclusion, package allocation or earned ledger revenue |
| Services | Performed activity, explicit attributable charge/cost | Zero charge means free service; package lines are incremental revenue |
| Pharmacy | Explicit signed sales/return and actual COGS | MRP or purchase rate automatically equals consumed cost |
| Claims | Workflow status, approval/deduction, expected date | Approval is collection; deduction reduces AR without a separate reviewed credit |
| Receipt/payment | Signed cash evidence and allocations to obligations | Unallocated advance is earned revenue/expense; transfer or principal is P&L |
| AR/AP adjustment | Explicit signed opening/credit/writeoff/reversal and already-in-document flag | Subtracting a credit twice; authority for a write-off |
| PO/GRN/invoice | Ordered/received/invoiced quantities, rates, commitments and liabilities | All purchasing is consumption; payment is invoice amount |
| Bank/cashbook | Signed movements and separately tagged opening/closing/count balances | Sum of balance snapshots; bank movement is an accounting cash-flow statement |
| Expense/payroll/consultant | Incurred amount, defined contract base, evidence and settlement allocations | Paid equals incurred; billed equals collected; universal payout/tax rates |
| Inventory | Latest opening plus signed movements by item/store/batch; counts separately | MRP valuation; counts are receipts; unknown opening is zero |
| Assets/loans | Recorded asset cost/depreciation, explicit policy estimates; principal/interest/schedule events | Loan proceeds revenue; principal expense; unsupported lease/covenant accounting |
| Statutory | Authorised liability/payment/credit, due date and challan | Legal rates, tax treatment or legal deadlines |
| Budget/scenario/forecast | Separate approved or explicit assumptions | Overwriting historical actuals or pretending estimates are actual profit |

## Identifiers, signs and cutoffs

Keep UHID, admission, invoice, account and reference keys as strings. XLSX zero-padded numeric display formats are preserved; CSV identifiers must already contain their leading zeros. Indian day/month dates and ISO dates are supported. Specify debit/credit separately in journal lines, including explicit zeros. Other monetary values are signed INR strings rounded to paise; quantity/rate accept six decimal places. Parentheses indicate a negative amount. Missing/dash/NA are unknown, not zero.

Cash movement deposit is positive, withdrawal negative. Refunds and reversals require explicit evidence and sign. Inventory issues/sales/transfer-outs normally have negative quantity and cost; enter the reviewed signed movement. Loan and statutory event types define their rollforward meaning; positive principal repayment reduces principal.

Flows use posting/document dates within From–To. Balances and allocations use all available eligible records and cash dates up to To. Opening snapshots denote a balance immediately before their date; subsequent events on/after that date enter the rollforward. `OPENING` is the document target for scoped AR/AP snapshots. Retain detailed prior invoices for matching and evidence, but do not add them again to an opening balance that already includes them. Source cutoff and completeness must be reconciled manually before use.

## Key formulas and completeness

| Metric | Formula / basis | Missing-data guard |
|---|---|---|
| Accounting revenue | Mapped revenue credits − debits, or compatible TB period movements | Ledger source required; source coverage shown |
| EBITDA | Revenue − direct cost − operating expense | Reviewed complete, mapped, balanced books required |
| Net result | EBITDA − depreciation − finance cost − tax | Same books/coverage guard; not audited certification |
| Operational billing | Sum final signed net bills in period | No bills = unknown; never add to GL or pharmacy totals |
| Receivables | Opening + valid subsequent bills − allocated eligible receipts − separately applicable credits/write-offs | Known document/snapshot scope required; approvals alone never settle |
| Payables | Opening + valid subsequent invoices − settlement allocations − applicable credits | Vendor advances remain separately unallocated |
| Recorded cash | Latest supplied closing/count per account | Unknown if missing; date and account coverage remain visible |
| Stock | Latest opening quantity/value + subsequent signed recorded movements | Missing opening/cost keeps closing unknown; count compared, never added |
| Loan principal | Latest opening + later drawdowns − principal repayments | Missing explicit opening makes closing unknown; interest remains separate |
| Pharmacy known-cost profit | Eligible signed sales − eligible recorded COGS | Excludes cost-missing lines and shows coverage; zero sales margin is N/A |
| DSO | Compatible closing AR / verified period credit revenue × period days | Reviewed credit-revenue completeness and nonzero denominator required |
| Revenue/discharge | Discharge-cohort final bills / unique branch/admission count | Valid discharge dates/admission keys required |
| Occupancy | Valid occupied bed-days / available bed-days | Both complete, denominator positive |
| Budget variance | Compatible branch/entity/month/category actual − budget | Journal basis only; percentage N/A for zero budget |
| Scenario contribution | Volume × price × (1 − deductions%) − volume × variable cost − explicit fixed/payroll/consultant/other consumption | All required assumptions explicit; excludes unstated interest/tax/other costs |
| Scenario break-even | Explicit fixed costs / positive per-patient contribution | Non-positive/unknown unit contribution returns N/A |
| Shared cost | Explicit pool × reviewed driver share; reconcile cents | No driver = unallocated, never silently equal split |
| 13-week forecast | Opening cash snapshot + dated supported inflows − dated obligations | Missing cash leaves closing unknown; no guessed overdue payment date; explicitly linked obligations deduplicated |

## Import review and replacement

Inspect accepted/rejected/excluded rows with worksheet and source row. Repeated headers and total markers without dates are excluded with reasons; report financial total comparability is not automatically certified. Totals may contain taxes, different cohort dates or a different basis. Compare actual source totals using the same financial meaning before approving completeness.

Identical file hashes do not create duplicate imports. For overlapping same-register/entity/branch periods choose retain-with-warning or reviewed replacement. Replacement deactivates prior records; it does not delete original files. Linked allocations/matches and locked periods block unsafe replacement/rollback. Reprocessing stages a new mapping version. Raw originals remain available for audit.

No actual source exports accompanied this build. Hospital-specific formulas, package/pharmacy inclusion, report-total semantics, currency/unit conventions and completeness require validation after actual reports are supplied.
