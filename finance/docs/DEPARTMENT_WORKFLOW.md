# Who fills what

Daily Work is the default landing page for department users. Each task has an entry form, upload shortcut and CSV template. Management and finance retain the executive dashboard and the original thirteen analysis modules.

| Team / role | Enters or uploads | Cadence | Boundary |
|---|---|---|---|
| Billing / TPA (`billing`) | Final patient bills, claim events, service activity, admission/discharge activity | Daily; each claim update | Views bills/receipts for follow-up; cannot create receipts or payments |
| Collections (`collections`) | Claim follow-up, billing corrections, receivable adjustments; proposes receipt allocations | Daily | Cannot record cash receipt; write-offs still require finance approval |
| Cashier (`cashier`) | Receipts, patient advances/refunds, recorded vendor payments and cashbook | Each transaction | Records transactions already made; the app does not execute payments |
| Purchase / stores (`procurement`, `stores`) | Purchase orders, goods receipts, supplier invoices and inventory movements/counts | At order, receipt, invoice and count | Cannot read or enter payments; accounts handles settlement |
| Pharmacy (`pharmacy`) | Main/Secondary pharmacy sales/returns, recorded costs, batches and stock movements | Daily | No payroll, accounting ledger or bank access |
| HR (`hr`) | Salary, benefits and reimbursement records; employee name requests | Monthly / approved reimbursement | Payroll register only; limited to assigned branches |
| Unit accounts (`accounts`) | Journals/TB, bank statements, expenses, consultant payouts, settlements and other finance registers | Daily reconciliation / monthly close | Can prepare records and reconciliations; cannot approve, change policy or administer users |
| Finance head (`finance`) | All finance registers, mappings, approved masters and rules; independent approvals | Daily review / monthly close | Cannot approve changes they made or imports they uploaded/mapped |
| Owner (`management`) | Executive review, staff/branch access, policies and finance approvals | Daily dashboard / periodic review | Same independent approval restriction |
| Reviewer (`reviewer`) | Inspection only, excluding payroll and consultant expense registers | As required | No entry, import submission or approval |

## Daily entry

1. Select the branch and relevant task in **Daily Work**.
2. Enter required fields first; dates use native calendar inputs and name lists come from active, authorised masters. Additional fields remain available below the main form. The app generates an internal line ID; keep source bill/voucher/claim IDs in their own fields.
3. Provide the supporting reference and submit. The entry appears in **My recent submissions** while pending.
4. Another management/finance user checks the evidence in **Imports & Settings → Approvals**. Only approval adds the row to financial analysis.

A department user can request the name types needed for their job. The inline **New vendor/panel/employee** action keeps the entry draft when returning. Finance approves the name before it appears in dropdowns. Use **Refresh approved names** in the pending draft to reload the list without losing entered values. Name requests cannot change bank details or mark a vendor approved. Finance manages reviewed vendor properties separately. Drafts are in browser memory, not saved across reloads.

## Bulk uploads

1. The department uploads CSV/XLSX into the real workspace with the correct entity, branch and register.
2. Review worksheet mappings, required fields, dates, signs, accepted samples, rejected rows and overlaps.
3. The uploader submits the frozen mapping and replacement/retention choices for approval. No rows or replaced batches change yet.
4. A finance/management user who is neither the uploader nor mapping reviewer inspects the frozen mapping and sampled rows, then approves or rejects with evidence. The server rechecks locks, overlaps and linked allocations immediately before committing.
5. Rejection returns the file to mapping review. Repeated submission while pending reuses the same approval request. Approved replacement keeps old rows inactive with their audit trail.

If the same person uploads and maps the file, one other finance checker suffices. If two different people upload and map it, a third independent checker is needed. Branch access applies to both maker and checker. Single-entry submissions also need a different checker.

## Start with real data

Create separate named staff accounts, assign their roles and branches, and establish the legal entity/branch and name masters. Validate the first actual hospital export against its source totals before wider use. The supplied demo data and UI preview do not validate any hospital report format or certify financial completeness.
