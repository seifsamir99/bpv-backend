-- Supabase Postgres schema for the BPV app (replaces Google Sheets).
-- Applied idempotently by migrate_sheets_to_supabase.py.
--
-- Conventions:
--  * Date-like fields are TEXT: the sheets stored display strings (e.g.
--    "15/07/2026") and the frontend echoes them verbatim; converting to DATE
--    would change the API JSON.
--  * Tables are deliberately flat/human-readable — the user hand-edits them
--    in the Supabase Table Editor.

-- BPV vouchers: one row per line item (mirrors the old BPV tab).
-- Grouping into vouchers by bpv_no happens in Python.
create table if not exists bpv_line_items (
  id          bigint generated always as identity primary key,
  bpv_no      text not null,
  date        text not null default '',
  type        text not null default 'PDC',   -- PDC | CDC
  sr_no       text not null default '',
  description text not null default '',
  company     text not null default '',
  cheque_no   text not null default '',
  cheque_date text not null default '',
  debit       numeric not null default 0,
  credit      numeric not null default 0
);
create index if not exists idx_bpv_line_items_bpv_no on bpv_line_items (bpv_no);

create table if not exists pdc_status (
  cheque_no  text primary key,
  status     text not null,
  updated_at timestamptz not null default now()
);

create table if not exists cdc_status (
  cheque_no  text primary key,
  status     text not null,
  updated_at timestamptz not null default now()
);

create table if not exists manual_pdc (
  id          bigint generated always as identity primary key,
  company     text not null default '',
  cheque_no   text not null default '',
  cheque_date text not null default '',
  amount      numeric not null default 0,
  description text not null default '',
  status      text not null default 'Not Released',
  created_at  timestamptz not null default now()
);

-- Employees: labour + staff unified (was two tabs with different layouts).
-- rate_per_hour / ot_hours are labour-only; deductions is staff-only.
create table if not exists employees (
  id            text primary key,             -- numeric-string employee IDs
  name          text not null,
  designation   text not null default '',
  rate_per_day  numeric not null default 0,
  rate_per_hour numeric not null default 0,
  ot_hours      numeric not null default 0,
  deductions    numeric not null default 0,
  net_salary    numeric not null default 0,
  type          text not null default 'Labour',  -- Labour | Staff
  -- Month-End manual adjustments (current-month values, like ot_hours). These
  -- feed payroll: net = base + ot + bonus - month_end_deduction.
  bonus               numeric not null default 0,
  bonus_reason        text    not null default '',
  month_end_deduction numeric not null default 0,
  deduction_reason    text    not null default '',   -- category (dropdown)
  deduction_note      text    not null default '',    -- free-text why
  -- Unguessable token for the employee's personal check-in link (/checkin/<token>).
  -- Generated once per employee (see time_entries_service.generate_token).
  checkin_token       text    unique,
  -- Whether this employee is one of the site engineers using Time Log
  -- check-in/check-out. Everyone gets a checkin_token, but only flagged
  -- employees show up in the Time Log roster/dropdown.
  time_log_enabled    boolean not null default false
);

-- Attendance: grid layout, one row per employee per month — mirrors the
-- monthly sheet tabs the user hand-edits. month is 1-12.
-- Day cells hold statuses like Present/Absent/Leave/Off/Sick/Joined/Holiday
-- (or '' = unmarked).
create table if not exists attendance (
  employee_id text not null,
  year        int  not null,
  month       int  not null check (month between 1 and 12),
  type        text not null default 'labour',   -- labour | staff
  name        text not null default '',
  designation text not null default '',
  day1  text not null default '',
  day2  text not null default '',
  day3  text not null default '',
  day4  text not null default '',
  day5  text not null default '',
  day6  text not null default '',
  day7  text not null default '',
  day8  text not null default '',
  day9  text not null default '',
  day10 text not null default '',
  day11 text not null default '',
  day12 text not null default '',
  day13 text not null default '',
  day14 text not null default '',
  day15 text not null default '',
  day16 text not null default '',
  day17 text not null default '',
  day18 text not null default '',
  day19 text not null default '',
  day20 text not null default '',
  day21 text not null default '',
  day22 text not null default '',
  day23 text not null default '',
  day24 text not null default '',
  day25 text not null default '',
  day26 text not null default '',
  day27 text not null default '',
  day28 text not null default '',
  day29 text not null default '',
  day30 text not null default '',
  day31 text not null default '',
  primary key (employee_id, year, month)
);

-- Time Log: site check-in/check-out. One row per employee per calendar day,
-- populated by the employee tapping Check In / Check Out on their personal
-- link (/checkin/<employees.checkin_token>) from their own phone. Times are
-- always the server clock; lat/lng are best-effort (null if location denied).
create table if not exists time_entries (
  id            bigserial primary key,
  employee_id   text not null references employees(id) on delete cascade,
  entry_date    date not null,
  check_in_at   timestamptz,
  check_in_lat  double precision,
  check_in_lng  double precision,
  check_out_at  timestamptz,
  check_out_lat double precision,
  check_out_lng double precision,
  -- Self-reported by the engineer (or set by the office) when they won't be
  -- checking in at all: 'absent' | 'sick' | null (normal). Independent of
  -- check_in_at/check_out_at, which stay null when a status is set.
  status        text,
  -- Free-text reason attached to status, or to an early leave at checkout.
  note          text,
  -- Set at checkout when the engineer flags they're leaving before end of shift.
  early_leave   boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (employee_id, entry_date)
);
create index if not exists time_entries_date_idx on time_entries (entry_date);

-- Month-End manual entries per employee per month (OT hours + bonus/deduction
-- adjustments with reasons). Feeds the payroll calc for that month.
-- month is 1-12. Replaces the old single-value employees.ot_hours/bonus/... flow
-- (those columns are legacy since 2026-07-23).
create table if not exists month_end (
  employee_id      text not null,
  year             int  not null,
  month            int  not null check (month between 1 and 12),
  ot_hours         numeric not null default 0,
  bonus            numeric not null default 0,
  bonus_reason     text    not null default '',
  deduction_amount numeric not null default 0,
  deduction_reason text    not null default '',
  deduction_note   text    not null default '',
  -- Where ot_hours came from: 'log' = auto-summed from labour_day_log (the site
  -- engineers' daily entries), 'manual' = the office typed it on the Month-End
  -- page. Once manual, the log stops overwriting it — office edits win.
  ot_source        text    not null default 'manual',
  primary key (employee_id, year, month)
);

-- Labour daily site log: the digital version of the paper TIME SHEET ATTENDANCE
-- (one row per labour per day — site(s) worked, OT hours, Present/Absent).
-- Filled by the site engineers on their phones at /crew/<checkin_token>, which
-- is the same token as the Time Log check-in link.
--
-- unique (employee_id, entry_date) is deliberate: engineers pick from the full
-- labour list, so two of them can mark the same man on the same day. The second
-- save overwrites the first and sets conflict = true so the office can review it.
create table if not exists labour_day_log (
  id           bigserial primary key,
  employee_id  text not null references employees(id) on delete cascade,  -- the labour
  entry_date   date not null,
  status       text    not null default 'Present',   -- Present | Absent
  ot_hours     numeric not null default 0,
  sites        text    not null default '',          -- comma-joined lpo_sites names, e.g. 'G+13, G+4'
  note         text    not null default '',
  marked_by    text    references employees(id) on delete set null,  -- the engineer who saved it; null once he is deleted
  conflict     boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (employee_id, entry_date)
);
create index if not exists labour_day_log_date_idx on labour_day_log (entry_date);

-- Project sites the labour works on (G+13, G+7, ...) — the chips on the
-- engineer's phone. Deliberately SEPARATE from lpo_sites: those are material
-- DELIVERY addresses for the LPO generator, which are not the same list as the
-- projects labour is assigned to. Managed by the office on the /site-log page.
create table if not exists labour_sites (
  id   bigint generated always as identity primary key,
  name text not null unique
);

-- Payroll: saved snapshots per (year, month, type) — replaces the monthly
-- payroll tabs that were cleared + rewritten on each save. month is 1-12.
-- salary_before_ot / ot_hours / ot_pay apply to labour only (null for staff).
-- The old "Cash <Mon>" tabs are derived data: payment_method = 'Cash'.
create table if not exists payroll (
  year             int  not null,
  month            int  not null check (month between 1 and 12),
  emp_type         text not null,              -- labour | staff
  employee_id      text not null,
  name             text not null default '',
  designation      text not null default '',
  deduction_amount numeric not null default 0,
  paid_days        numeric not null default 0,
  rate_per_hour    numeric not null default 0,
  salary_before_ot numeric,
  ot_hours         numeric,
  ot_pay           numeric,
  net_salary       numeric not null default 0,
  payment_method   text not null default 'Bank Transfer',
  bonus            numeric not null default 0,
  bonus_reason     text    not null default '',
  deduction_reason text    not null default '',
  deduction_note   text    not null default '',
  primary key (year, month, emp_type, employee_id)
);

-- Tax invoices received: a simple per-month log of supplier tax invoices for
-- VAT record-keeping. Independent of BPV/payroll. month is 1-12, derived from
-- invoice_date on save (for the month tabs). amount_with_vat is stored (not
-- computed) so odd invoices where VAT != 5% round-trip exactly as entered.
create table if not exists tax_invoices (
  id              bigint generated always as identity primary key,
  invoice_date    text not null default '',      -- MM/DD/YYYY, same convention as PDC
  year            int  not null,
  month           int  not null check (month between 1 and 12),
  company         text not null default '',
  amount_no_vat   numeric not null default 0,
  vat             numeric not null default 0,
  amount_with_vat numeric not null default 0,
  invoice_number  text not null default '',
  created_at      timestamptz not null default now()
);

-- LPO generator state (was the LPO_Store key/value tab).
create table if not exists lpo_counter (
  id    int primary key check (id = 1),        -- single row
  value int not null
);

create table if not exists lpo_sites (
  id   bigint generated always as identity primary key,
  name text not null unique
);

-- Job Order generator state (subcontractor work orders, separate from LPO's
-- material-supplier lpo_sites/lpo_counter).
create table if not exists job_order_counter (
  id    int primary key check (id = 1),
  value int not null
);

create table if not exists job_order_subcontractors (
  id   bigint generated always as identity primary key,
  name text not null unique
);

-- Sub-contractor payment certificates (the /payment-cert page). Newell issues
-- these DOWNWARD to its own subcontractors, the same instrument main contractors
-- issue to Newell. Unlike lpo_*/job_order_* -- stateless generators that persist
-- only a counter -- a payment certificate is CUMULATIVE: this month's figures are
-- only meaningful against what was already certified and paid, so both the
-- contract and every issued certificate are stored.
create table if not exists payment_cert_counter (
  id    int primary key check (id = 1),
  value int not null
);

-- One row per subcontract. Entered once; each month only produces a certificate.
-- contract_value is NET OF VAT -- a job order's headline "grand total with VAT"
-- must be divided down before it lands here, or the job over-certifies by the VAT.
create table if not exists payment_cert_contracts (
  id                bigint generated always as identity primary key,
  subcontractor     text not null,
  project           text not null default '',
  project_code      text not null default '',   -- 'S 108', feeds the ref number
  scope             text not null default '',   -- 'GAS SYSTEM'
  job_order_ref     text not null default '',   -- the JO this subcontract came from
  contract_value    numeric(14,2) not null,     -- NET of VAT
  retention_percent numeric(5,2)  not null default 10,
  vat_percent       numeric(5,2)  not null default 5,
  contact           text not null default '',
  phone             text not null default '',
  address           text not null default '',
  scope_items       jsonb not null default '[]',
  archived          boolean not null default false,
  created_at        timestamptz not null default now(),
  unique (subcontractor, project)
);

-- One row per monthly certificate. EVERY derived figure is stored rather than
-- recomputed on read: reprinting certificate 3 must show the numbers it was
-- issued with, even if the contract's retention % is edited afterwards.
-- cert_no is the per-contract sequence printed as "Bill No." on the face.
create table if not exists payment_certificates (
  id                   bigint generated always as identity primary key,
  contract_id          bigint not null references payment_cert_contracts(id) on delete cascade,
  cert_no              int not null,             -- Bill No., sequential per contract
  ref_number           text not null default '', -- PC/NEMW/{code} / {year}/{NNNNN}
  bill_month           text not null default '', -- 'May-26'
  invoice_date         date not null,
  bill_type            text not null default 'interim',  -- interim | final
  amount_entered       numeric(14,2) not null,   -- what the user typed = the cash paid
  amount_ex_vat        numeric(14,2) not null,   -- DERIVED backwards from the cash
  vat_portion          numeric(14,2) not null,   -- VAT on the full certified value
  retention_amount     numeric(14,2) not null,   -- retention% of amount_ex_vat
  other_deduction      numeric(14,2) not null default 0,
  other_deduction_note text not null default '',
  total_amount         numeric(14,2) not null,   -- ex_vat - retention - other
  vat_charged          numeric(14,2) not null,   -- vat% of total_amount (NOT vat_portion:
                                                 -- VAT on retained money is charged when
                                                 -- the retention is released)
  net_payable          numeric(14,2) not null,   -- == amount_entered, the typed figure
  scope_amounts        jsonb not null default '{}',
  created_at           timestamptz not null default now(),
  unique (contract_id, cert_no)
);

create index if not exists payment_certificates_contract_idx
  on payment_certificates (contract_id, cert_no);

-- Credit application form templates (the /credit-form page). Once a supplier's
-- blank form has been mapped, both the mapping and the blank PDF itself are
-- stored, so the next application for that supplier is pick -> review ->
-- generate with no re-upload and no AI call. The PDF lives here rather than on
-- disk so local and Railway share the same templates.
-- NOT loaded by migrate_sheets_to_supabase.py — it must never truncate this.
create table if not exists credit_form_templates (
  id            bigint generated always as identity primary key,
  supplier_name text not null unique,
  placements    jsonb not null,
  pdf_bytes     bytea not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- LPO history: one row per generated LPO, so past LPOs can be searched,
-- re-downloaded, edited, or deleted. No PDF bytes are stored — line_items
-- (and extra_terms) hold everything pdf_generator.generate_lpo() needs to
-- rebuild the exact PDF again on demand.
create table if not exists lpo_records (
  id               bigint generated always as identity primary key,
  lpo_number       text not null unique,
  lpo_date         date,
  quote_ref        text,
  project          text,
  supplier_name    text not null,
  supplier_contact text,
  supplier_email   text,
  supplier_phone   text,
  supplier_address text,
  supplier_trn     text,
  delivery_site    text,
  delivery_contact text,
  delivery_phone   text,
  payment_terms    text,
  discount         numeric not null default 0,
  vat_percent      numeric not null default 5,
  -- Lump-sum overrides typed on the /lpo page; NULL = derive from line items.
  -- subtotal_override is ex-VAT, total_override is VAT-inclusive and is worked
  -- backwards (see generate_lpo). Only one is ever set at a time.
  subtotal_override numeric,
  total_override    numeric,
  subtotal         numeric not null default 0,
  total_amount     numeric not null default 0,
  line_items       jsonb not null default '[]',
  extra_terms      jsonb not null default '[]',
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists idx_lpo_records_date on lpo_records (lpo_date desc);

-- Job Order history: same pattern as lpo_records, for generate_job_order().
create table if not exists job_order_records (
  id                     bigint generated always as identity primary key,
  ref_number             text not null unique,
  project_code           text,
  jo_date                date,
  project                text,
  sub_scope              text,
  quote_date             date,
  subcontractor_name     text not null,
  subcontractor_contact  text,
  subcontractor_phone    text,
  subcontractor_address  text,
  has_unit_pricing       boolean not null default false,
  discount               numeric not null default 0,
  vat_enabled            boolean not null default true,
  vat_percent            numeric not null default 5,
  -- false for a schedule-of-rates job order: the PDF omits the whole
  -- subtotal/discount/VAT/total block (no agreed quantities, so no total).
  show_totals            boolean not null default true,
  subtotal               numeric not null default 0,
  total_amount           numeric not null default 0,
  line_items             jsonb not null default '[]',
  terms_text             text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);
create index if not exists idx_job_order_records_date on job_order_records (jo_date desc);

-- Zoho Books contact mapping: local party name -> Zoho contact_id, so each
-- vendor/customer is looked up or created in Zoho only once. This is
-- independent integration state — NOT loaded by migrate_sheets_to_supabase.py
-- (it must never truncate this table). norm_name = lower(trim()) for matching.
create table if not exists zoho_contacts (
  id              bigint generated always as identity primary key,
  local_name      text not null,
  norm_name       text not null,
  contact_type    text not null,             -- vendor | customer
  zoho_contact_id text not null,
  created_at      timestamptz not null default now(),
  unique (norm_name, contact_type)
);

-- BPV -> Zoho push log: one row per pushed BPV line item, so a line is never
-- double-posted and can be un-synced (delete the Zoho expense + this row).
-- Also independent integration state — NOT touched by migrate_sheets_to_supabase.py.
-- Keyed by line_id (bpv_line_items.id): a voucher can repeat sr_no, so keying on
-- (bpv_no, sr_no) silently dropped lines — line_id is the guaranteed-unique key.
create table if not exists zoho_bpv_sync (
  id           bigint generated always as identity primary key,
  line_id      bigint unique,
  bpv_no       text not null,
  sr_no        text not null default '',
  cheque_no    text not null default '',
  category     text not null default '',
  account_name text not null default '',
  zoho_expense_id text not null,
  amount       numeric not null default 0,
  created_at   timestamptz not null default now()
);

-- BPV -> Zoho manual-journal push log (non-expense book-worthy lines that need a
-- two-sided journal instead of an expense — e.g. Eng. Hesham loan-ins:
-- Debit Petty Cash / Credit "Loan from Eng. Hesham"). Keyed on line_id, with a
-- `kind` discriminator ('loan', later 'advance', etc.). Un-sync = delete the Zoho
-- journal + this row. Independent integration state — NOT touched by the migrate script.
create table if not exists zoho_journal_sync (
  id              bigint generated always as identity primary key,
  line_id         bigint unique,
  bpv_no          text not null default '',
  cheque_no       text not null default '',
  kind            text not null default '',
  zoho_journal_id text not null,
  amount          numeric not null default 0,
  created_at      timestamptz not null default now()
);

-- Customer sales tax-invoices -> Zoho invoices (output VAT) push log. Source is
-- the "Tax invoices" Google Sheet (monthly tabs); each row -> one Zoho invoice at
-- 5% standard VAT. Keyed on row_key = sha1(tab|sl|customer|inv_no|net) so a re-run
-- never double-posts; un-sync = void+delete the Zoho invoice + drop this row. The
-- sheet's own invoice number is stored (inv_no) but Zoho assigns its own number.
-- Independent integration state — NOT touched by migrate_sheets_to_supabase.py.
create table if not exists zoho_invoice_sync (
  id                  bigint generated always as identity primary key,
  row_key             text unique,
  tab                 text not null default '',
  sl_no               text not null default '',
  customer            text not null default '',
  inv_no              text not null default '',
  invoice_date        text not null default '',
  net                 numeric not null default 0,
  vat                 numeric not null default 0,
  zoho_invoice_id     text not null,
  zoho_invoice_number text not null default '',
  created_at          timestamptz not null default now()
);

-- Supplier PURCHASE invoices -> Zoho vendor BILLS (input VAT) push log. Source is
-- the "Tax invoices" Google Sheet (which is actually purchases, not sales — the
-- sales invoices booked from it were reversed). Each row -> one Zoho bill at 5%
-- input VAT; the paired BPV 'materials' expense is reversed to avoid double-count.
-- Keyed on row_key (same sha1 as zoho_invoice_sync). Independent integration
-- state — NOT touched by migrate_sheets_to_supabase.py.
create table if not exists zoho_bill_sync (
  id               bigint generated always as identity primary key,
  row_key          text unique,
  tab              text not null default '',
  sl_no            text not null default '',
  supplier         text not null default '',
  inv_no           text not null default '',
  vendor_id        text not null default '',
  matched_name     text not null default '',
  net              numeric not null default 0,
  vat              numeric not null default 0,
  zoho_bill_id     text not null,
  zoho_bill_number text not null default '',
  created_at       timestamptz not null default now()
);

-- BPV 'materials' cheques -> Zoho vendor payments (from Petty Cash) applied to the
-- supplier bills above, so Accounts Payable shows each supplier's true unpaid
-- balance. Keyed on bpv_line_items.id (line_id). Independent integration state —
-- NOT touched by migrate_sheets_to_supabase.py.
create table if not exists zoho_vendorpayment_sync (
  id               bigint generated always as identity primary key,
  line_id          bigint unique,
  bpv_no           text not null default '',
  cheque_no        text not null default '',
  vendor_id        text not null default '',
  amount           numeric not null default 0,
  zoho_payment_id  text not null,
  created_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Column additions to tables that already exist in the live database.
-- Every `create table` above is `if not exists`, so it will NOT add a column to
-- a table that is already there. New columns on existing tables go here so the
-- whole file stays re-runnable against a live database.
-- ---------------------------------------------------------------------------

-- 2026-08-12: labour daily site log (see labour_day_log above).
alter table month_end add column if not exists ot_source text not null default 'manual';

-- 2026-08-12: who may fill the labour sheet at /crew/<token>. Separate from
-- time_log_enabled on purpose: that flag means "site engineer doing GPS
-- check-in", while this one means "allowed to mark the labour's attendance".
-- The two overlap but are not the same people (an electric foreman fills the
-- sheet without being on the engineers' check-in roster).
alter table employees add column if not exists crew_sheet_enabled boolean not null default false;
