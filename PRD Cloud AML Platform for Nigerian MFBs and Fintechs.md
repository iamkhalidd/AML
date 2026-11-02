# PRD: Cloud AML Platform for Nigerian MFBs and Fintechs

Nov 2, 2026 · @Khalid Adebiyi · Revision 2: findings from the circular text folded in (see the change log at the end)

## Overview

The platform is a cloud-hosted, browser-based automated AML system for Nigerian microfinance banks and small to mid-size fintechs. It gives their compliance teams the monitoring, investigation, reporting and evidence tools the CBN baseline standards for automated AML solutions expect an institution to operate, without building or buying an enterprise suite. The institution remains responsible for its own compliance. The working product name is to be decided.

**Problem.** CBN Circular BSD/DIR/PUB/LAB/019/002 of 10 March 2026, with the CBN guidance note CMD/DIR/PUB/CIR/001006 of 31 March 2026, requires regulated institutions to run automated AML systems covering customer identification, risk assessment, screening, transaction monitoring, case management, regulatory reporting, audit and data protection. Banks must comply by 10 September 2027. MFBs, fintechs, PSPs and other institutions must comply by 10 March 2028. Secondary sources describe many small institutions as relying on spreadsheets and paper-based reporting today, which is unverified and must be confirmed in customer interviews.

**Customers.**

- **Primary:** microfinance banks.
- **Secondary:** small and mid-size fintechs, payment service providers and mobile money operators.
- **Later:** commercial banks, after the product has references.

**What cloud-based means in this product.**

- Hosted and operated by us. Customers use a web browser and APIs, with nothing installed at the institution.
- Each institution gets an isolated environment with its own database, configuration and encryption keys.
- Hosted on infrastructure inside Nigeria. The standards require processing to respect Nigerian data sovereignty law, and a CBN circular of 15 June 2026 is reported to require local storage of payment transaction data from 1 January 2027. That circular comes from secondary sources and must be checked against the CBN original.
- API-first. The standards expect real or near-real-time integration with core banking and KYC systems, so the API is the default way to connect. Batch files remain as a fallback the institution must be able to justify.
- Updates are released by us, so customers never manage upgrades.
- On-premise installation is out of scope until a paying buyer requires it.

**Product principles.**

1. **Explain every alert.** Each alert shows the rule, its version and the data that triggered it.
2. **Evidence by default.** Every action is recorded, and the institution can export what an examiner asks for.
3. **Calibrated to the institution.** Thresholds and their written rationale are set per institution, not copied from a template.
4. **Built for small teams.** A compliance office of one to three people can run it, so every workflow minimises clicks and handoffs.
5. **Rules before models.** Machine learning is added only after real labelled data exists and explanations remain possible.
6. **No compliance claims.** The product supports an institution's controls and evidence. It never claims to make anyone compliant.

**Product statement (draft):** for MFB and small fintech compliance teams, the platform is the cloud AML system that builds the evidence a CBN examiner asks for as a by-product of daily work.

## Regulatory constraints on the product

These come from the full text of the circular and the guidance note, read on 2 November 2026 through an aggregator's copy. Confirm them against the CBN PDFs. The clause-by-clause map is in the [Engineering Specification Pack](https://claude.ai/artifact/9Mvf6L9LQntV4BQC2izUrL), under the tab CBN requirement map.

- **No compliance claims.** The CBN does not approve, certify or endorse AML solutions or vendors and does not recognise claims of full compliance or alignment. Compliance is assessed at the institution. Do not use compliance, approval or alignment wording in the product name, website, contracts or sales material.
- **Technology neutral.** Rules-based, machine learning and hybrid systems are all acceptable. The first releases stay rules-first.
- **Defensibility, governance and effectiveness.** The guidance note judges institutions on these three themes. They organise the product and the vendor pack.
- **Proportionality.** Depth and configuration are calibrated to the institution's volumes, products, customers, geography and documented risk assessment. Purely manual processes still fail.
- **Integration is central.** The system must link transactions to KYC and KYB data and customer risk profiles. Fragmented systems do not meet expectations, and higher-risk institutions may not rely on standalone transaction feeds.
- **Real time has two meanings.** Screening is completed before a transaction is authorised or onboarding completes. Monitoring evaluates transactions as they occur and can trigger pre-emptive action before funds settle irreversibly. Pre-emptive alerts and holds are expected where risk justifies them. AML monitoring does not need fraud-level speed.
- **KYB is in scope.** Business customers, beneficial owners, related parties and complex ownership structures must be recorded, with manual escalation where the system falls short.
- **Fraud monitoring is required but may sit elsewhere.** No single AML and fraud system is mandated. A separate fraud system is acceptable if it exchanges risk signals with the AML system.
- **Notification duties.** Institutions must tell the CBN about the AML solutions they use and their roles, at deployment, after material change and on request, and about any automated alert closure practice and threshold.
- **Shared services.** Shared or centralised arrangements need prior CBN approval. Whether a vendor-hosted platform serving several institutions counts is an open legal question.
- **Vendor due diligence.** Institutions must apply heightened due diligence to AML vendors and their cloud providers. A vendor due diligence pack is a required deliverable.

## Goals, non-goals and success metrics

The product succeeds if two to three real institutions run it in 2027 and at least one pays for it before the first buyer deadline arrives.

**Goals**

1. **G1:** Ship a working monitoring, alert and case workflow that a compliance practitioner calls credible, by 19 March 2027.
2. **G2:** Support every functional area the CBN baseline lists, in phases, before the 10 March 2028 deadline for the primary segment.
3. **G3:** Make an institution's AML decisions defensible, with an examiner-ready record of what was flagged, why, who reviewed it and what was decided.
4. **G4:** Keep a compliance office of one to three people able to run the product without a dedicated analyst team.
5. **G5:** Onboard a new institution in under one working day by the end of August 2027.

**Non-goals for the first three releases**

- Replacing or competing with core banking systems.
- Payment processing, wallets or any money movement.
- A customer-facing mobile app.
- Fraud detection rules, such as card fraud or device fingerprinting. The standards allow a separate fraud system, so the product exchanges risk signals with one (ING-10) instead.
- Machine learning scoring.
- On-premise deployment.
- Serving commercial banks before the product has pilot references.

**Success metrics.** Targets marked proposed are my starting points and must be confirmed after Phase 0 interviews.

| Metric | Target | When measured |
| --- | --- | --- |
| Pilot institutions live | 2 to 3 | By the end of May 2027 |
| Paid contract or signed letter of intent | At least 1 | By the end of May 2027 |
| Valid transaction records accepted at ingestion | 99% or more (proposed) | Every pilot, weekly |
| Rejected records visible with a reason | 100% | Every pilot |
| Time to clear an alert | Baseline recorded in pilots, improvement target set after | Pilots |
| Share of alerts closed as false positives | Baseline recorded in pilots, target set after | Pilots |
| STR conversion rate | Baseline recorded in pilots | Pilots |
| Investigation turnaround | Baseline recorded in pilots | Pilots |
| Time to produce an examiner export pack | Under 10 minutes (proposed), once the pack ships in V1 | Pilots |
| New institution onboarding time | Under 1 working day | By the end of August 2027 |
| Audit record gaps | Zero | Continuous |
| Monthly availability | 99.5% or more (proposed for MVP) | From first pilot |

## Users and personas

The pain points below are hypotheses from research, not interview findings. Phase 0 interviews confirm or replace them.

| Persona | Who they are | What they need | Likely pain today (hypothesis) | Permissions |
| --- | --- | --- | --- | --- |
| Compliance officer (MLRO) | Senior owner of AML at the institution and the likely buyer's champion | Confidence the institution passes an examination; oversight of alerts, cases and reports; control of rules | Manual evidence gathering, unclear which alerts matter, personal liability for missed reports | Approve rule changes, approve and submit reports, manage users, view everything |
| AML analyst | Reviews alerts and investigates cases; in a small institution this may be the compliance officer too | Fast triage, full customer context on one screen, simple closure with a reason | Alert volume, scattered customer data, paper or spreadsheet notes | Review alerts, open and work cases, draft reports, cannot approve their own work |
| Institution administrator | IT or operations staff who connect data and manage access | Simple data connection, clear errors, no surprises | Messy exports, unclear import failures | Manage integrations, users and settings, no access to case decisions |
| Internal or external auditor | Reviews controls; may be a CBN examiner's counterpart | Read-only access to rules, history, decisions and exports | Evidence scattered across files and systems | Read-only on rules, alerts, cases, reports and audit logs |
| Platform operator (us) | Our support and engineering staff | Diagnose issues without seeing customer data unnecessarily | Not applicable | Restricted, logged and time-limited support access under customer approval |

**Design rule for permissions:** no one approves their own work. Rule changes, case closures and report submissions require a second person, which is why a one-person compliance office needs a documented fallback approver, such as a board member or the managing director, before go-live. Because the fallback approver would sign off every case closure, Phase 0 interviews must test whether this is workable for the smallest institutions.

## Scope and release plan

Three releases follow the phases in the full plan. The MVP proves the core workflow on sample data, the pilot release adds the customer context and synchronous checks regulators expect, and version 1 makes the product repeatable to sell.

| Capability | MVP, 19 March 2027 | Pilot release, end of May 2027 | Version 1, end of August 2027 |
| --- | --- | --- | --- |
| Tenancy, authentication, roles | Yes | Single sign-on not planned | Self-service user management |
| Transaction ingestion | API, webhook, SFTP batch fallback, dead-letter handling | Synchronous check API, signed outbound webhooks, connectors for the cores pilot customers run | Risk signal exchange with fraud systems, connector library, mapping assistant |
| Rules engine | Versioned rules, starter library, per-institution thresholds | Customer-context rules, pre-emptive rules in the check API | Rule simulation on historical data, annual tuning review |
| Alerts | Queue, assignment, explanations | Priority scoring, service standards for high-risk alerts | SLA timers, bulk actions, governed automated closure |
| Case management | Notes, evidence, escalation, closure reasons, maker-checker | Case templates | Case analytics, periodic review of closed cases |
| Reporting | Draft STR and CTR from cases, effectiveness dashboard | STR, SAR, CTR and FTR; NFIU format once confirmed; board report | Scheduled reports, examiner export pack |
| Customer profile and risk rating | Manual risk rating, KYB data model, unified customer risk view | Automated risk scoring, KYB workflow, related parties, segments | Periodic risk re-rating |
| KYC integration | Store results supplied by the institution | BVN and NIN checks through a partner, as supporting evidence | Additional verification partners |
| Sanctions and PEP screening | Not included | Licensed lists, internal watchlists, match review, holds, refresh logs | Ongoing re-screening |
| Audit | Append-only record of every action, full traceability to source data | Audit export, retention settings, governance reports | Examiner pack |
| Administration | Basic configuration | Integration health view, CBN solution notification pack | Provisioning of a new institution in under a day, evidence library |
| Notifications | Email | Email digests | Email and in-app, optional messaging channel |
| Security evidence | Internal security review | Data protection impact assessment | Third-party penetration test |

**Release rule:** a capability moves to the next release only if it meets the acceptance criteria in the requirements section. Anything unfinished is cut from the release, not shipped half-built.

**Schedule (decided 2 November 2026).** The MVP moves from mid February to Friday 19 March 2027, and the full scope is kept, because the evidence features are the product. The MVP backlog in the Engineering Specification Pack totals 65 working days. The period from early November 2026 to 19 March 2027 holds about 85 to 90 working days once Christmas, Boxing Day, New Year and the Eid holidays are removed. The backlog already counts the Must requirements the circular added to the MVP (CUS-6 data model, CUS-8, REP-10 and AUD-7), so the remaining 20 to 25 days cover Phase 0 interviews alongside the foundation stories, and slack. The date sits before Good Friday on 26 March 2027 so the Easter holidays do not eat into the final week.

The later releases move by the same amount and keep their original spacing: the pilot release to the end of May 2027 and version 1 to the end of August 2027. Both stay well before the 10 March 2028 deadline for the primary segment. If the estimates prove too low after the first sprint, cut in the order the MVP backlog gives (SFTP gateway, attachments, email notifications first) before moving the date again.

**Explicitly later, after Version 1:** machine learning detection, network analysis and peer grouping (RUL-9), adverse media monitoring (SCR-6), enterprise risk assessment support (CUS-9), cross-institution intelligence sharing, on-premise deployment and bank-grade multi-entity group structures.

## Functional requirements

Each requirement has an ID, a priority (Must, Should or Could), acceptance criteria that a tester can verify, and the release it targets. MVP means 19 March 2027, Pilot means the end of May 2027, V1 means the end of August 2027 and Later means after V1. Performance figures marked as to be set depend on pilot volumes that are not yet known. Where acceptance criteria say "see the requirement map", the full criteria are in the CBN requirement map tab of the Engineering Specification Pack. IDs ALT-9, CAS-8 and NFR-18 are not assigned in this document; confirm against the requirement map that nothing was dropped.

### Tenancy and access

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| ACC-1 | Each institution is an isolated tenant with its own database | Must | No code path reads another tenant's data; verified by automated tests and a manual review | MVP |
| ACC-2 | Login with email and password plus a second factor for every user | Must | Login fails without the second factor; repeated failed attempts lock the account | MVP |
| ACC-3 | Role-based access for compliance officer, analyst, administrator and auditor | Must | Each role can do only what the personas table allows; a test per role confirms it | MVP |
| ACC-4 | Maker-checker approval for rule changes, case closure and report submission | Must | The creator of a change cannot approve it; a fallback approver can be named in settings | MVP |
| ACC-5 | Configurable idle timeout and administrator session revocation | Should | An idle session ends after the configured time; a revoked session stops at once | Pilot |
| ACC-6 | Support access for our staff that is customer-approved, time-limited and logged | Should | Support cannot open a tenant without an approval record; every access is in the audit log | Pilot |

### Transaction ingestion

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| ING-1 | Accept transactions and customers through an authenticated REST API with a documented schema; this is the default integration | Must | A sample client submits valid records and receives an accept or reject result for each | MVP |
| ING-2 | Accept CSV batch files through SFTP using a documented template, as a fallback for institutions that cannot integrate by API | Must | A batch file is processed without manual steps; processing time target to be set from pilot volumes | MVP |
| ING-3 | Validate every record; send rejected records to a dead-letter store with a plain-language reason | Must | Submitted count equals accepted plus rejected count; no record is dropped silently | MVP |
| ING-4 | Resubmitting the same record never creates a duplicate | Must | An idempotency test submits a record twice and finds one transaction | MVP |
| ING-5 | Store money as integer minor units with a currency code | Must | No floating point amounts anywhere in storage or calculation | MVP |
| ING-6 | Evaluate rules by transaction timestamp, not arrival order, so late records are handled | Should | A late record triggers the same alert it would have triggered on time | Pilot |
| ING-7 | Show ingestion health per source: last receipt, volume and rejection rate | Should | An administrator can see each source's status without asking support | Pilot |
| ING-8 | Provide mapping templates and connectors for the core banking systems pilot customers run | Should | A pilot institution's real export loads with documented, repeatable mapping | Pilot |
| ING-9 | Synchronous check API returning allow, review or hold before a transaction is authorised or onboarding completes | Should | See the requirement map | Pilot |
| ING-10 | Risk signal exchange with the institution's fraud system, in both directions | Should | See the requirement map | V1 |
| ING-11 | Signed outbound webhooks for alert, case and report events | Should | See the requirement map | Pilot |

### Rules engine

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| RUL-1 | Provide a starter library: high-value cash, structuring below thresholds, rapid pass-through, dormant account surge, velocity spike, round amounts, many-to-one, one-to-many, profile mismatch | Must | At least six rules work in the MVP and all nine by the pilot release; each has test data that triggers it | MVP |
| RUL-2 | Per-institution thresholds, severity and on or off state for every rule | Must | Two tenants configured differently produce different alerts from the same data | MVP |
| RUL-3 | Rules are versioned with an effective date; editing creates a new version | Must | History lists every version and who approved it; old versions are never overwritten | MVP |
| RUL-4 | Every alert records the rule version and a plain-language explanation with the triggering values | Must | An analyst can read why an alert fired without opening another tool | MVP |
| RUL-5 | Rule changes require a written rationale and maker-checker approval | Must | A change without a rationale or approval cannot take effect | MVP |
| RUL-6 | Evaluate events within a timeframe the institution documents; pre-emptive checks use ING-9 | Should | Proposed targets: minutes for batch-fed institutions, seconds for API-fed ones; the measured time is shown to the institution | MVP |
| RUL-7 | Simulate a changed rule on historical data before activating it | Should | The simulation reports how many alerts the change would have produced | V1 |
| RUL-8 | Context rules using KYC and KYB attributes and the customer risk rating | Must | See the requirement map | Pilot |
| RUL-9 | Network analysis and peer grouping | Could | See the requirement map | Later |
| RUL-10 | Pre-emptive rules evaluated inside the synchronous check | Should | See the requirement map | Pilot |
| RUL-11 | Annual tuning review workflow that records threshold justifications and false positive and false negative thresholds | Should | See the requirement map | V1 |

**Rule count.** RUL-1 lists nine rules. The rule specifications in the Engineering Specification Pack define 13 rules plus the screening match. Reconcile the two lists, including where RUL-8 and RUL-10 rules sit, before rules engine coding starts.

### Alerts

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| ALT-1 | Alert queue filterable by status, severity, rule, assignee and date | Must | Filter combinations return exactly the matching alerts | MVP |
| ALT-2 | One screen shows the customer profile, recent transactions and earlier alerts | Must | An analyst can decide without opening another tool for data the platform holds | MVP |
| ALT-3 | Assign, reassign and track ownership of alerts | Must | Ownership history is visible and every change is in the audit log | MVP |
| ALT-4 | Close an alert with a mandatory reason from a configurable list plus a note | Must | An alert cannot be closed without a reason | MVP |
| ALT-5 | Priority score from rule severity and customer risk | Should | Queue can be sorted by priority | Pilot |
| ALT-6 | Group alerts for the same customer | Should | Related alerts appear together on the customer's alert view | Pilot |
| ALT-7 | Automated closure, off by default and governed as the standards require | Should | When on, every automated closure meets all four low-risk criteria: a rule approved by the governance committee; closure based on both transaction data and current KYC or KYB data; a Low customer risk rating unchanged in the review period; no unresolved alerts or open cases for the customer. Closures stay within the institution-defined share threshold. Back-test reports and an exportable CBN notification pack are available. Every automated closure is logged | V1 |
| ALT-8 | Bulk close alerts from one rule with a shared documented reason | Should | Bulk closure above a configurable size needs a second reviewer | V1 |
| ALT-10 | Service standards for high-risk alerts, with breach reporting | Should | See the requirement map | Pilot |

### Cases

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| CAS-1 | Escalate one or more alerts into a case | Must | The case links every source alert and keeps their explanations | MVP |
| CAS-2 | Case record with customer, linked alerts, notes, attachments and timeline | Must | All case activity appears in time order with author and time | MVP |
| CAS-3 | Case outcomes: closed with no suspicion, closed with a report, referred | Must | A case cannot close without an outcome and a written reason | MVP |
| CAS-4 | Maker-checker approval on case closure | Must | The investigator cannot approve their own closure | MVP |
| CAS-5 | Case timeline is immutable; corrections add new entries | Must | No edit or delete removes a past entry | MVP |
| CAS-6 | Attachments are scanned for malware and limited by type and size | Should | An infected or oversized file is rejected with a message | Pilot |
| CAS-7 | Case templates and service-level timers | Could | Overdue cases are highlighted | V1 |
| CAS-9 | Periodic review of a sample of closed cases | Could | See the requirement map | V1 |

### Reporting

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| REP-1 | Draft a suspicious transaction report from a case with fields prefilled | Must | The draft carries customer, transaction and narrative fields in an internal format; mapping to the NFIU format follows in REP-5 once the format is confirmed | MVP |
| REP-2 | Draft currency transaction reports for transactions above the configured threshold | Must | Threshold is set per institution and confirmed against regulation | MVP |
| REP-3 | Report approval by a second person with submission status tracking | Must | Status moves draft, approved, submitted; the platform records who and when | MVP |
| REP-4 | Management dashboard: alerts by rule, ageing, case outcomes, reports filed | Should | Figures reconcile to the underlying records | MVP |
| REP-5 | Export reports in the NFIU format, after the format is confirmed | Should | An export passes the NFIU or goAML validation steps once confirmed | Pilot |
| REP-6 | Examiner export pack with rules, versions, rationale, alert statistics, case summaries and audit extract | Should | A complete pack is produced in under 10 minutes | V1 |
| REP-7 | Scheduled management report for the compliance officer, senior management and Board | Should | See the requirement map | Pilot |
| REP-8 | Report types STR, SAR, CTR and FTR | Should | See the requirement map | Pilot |
| REP-9 | Configurable report schedules and formats | Could | See the requirement map | V1 |
| REP-10 | Effectiveness dashboard: alert volume, false positive rate, STR conversion rate, investigation turnaround | Must | Figures reconcile to the underlying records; see the requirement map | MVP |

### Customer profile and risk rating

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| CUS-1 | Customer record for individuals and businesses with attributes supplied by the institution, accounts and an expected activity profile | Must | Records can be created through the API or batch import | MVP |
| CUS-2 | Manual risk rating of low, medium or high with a rationale and date | Must | A rating cannot be saved without a rationale | MVP |
| CUS-3 | Automated risk scoring from configurable factors such as occupation, location, product and behaviour | Should | Score and contributing factors are visible and explainable | Pilot |
| CUS-4 | Compare actual activity with the expected activity profile | Should | The profile mismatch rule uses the stored profile | Pilot |
| CUS-5 | Periodic re-rating reminders | Should | Reminders appear for customers past their review date | V1 |
| CUS-6 | Business customers with beneficial owners and control structures, with manual escalation where a structure cannot be recorded | Must | Data model in MVP, workflow in Pilot; see the requirement map | MVP data model, Pilot workflow |
| CUS-7 | Related parties shown on alerts | Should | See the requirement map | Pilot |
| CUS-8 | Unified customer risk view joining KYC or KYB data, risk rating, transactions, alerts and cases | Must | See the requirement map | MVP |
| CUS-9 | Enterprise risk assessment support | Could | See the requirement map | Later |
| CUS-10 | Report of risk class changes with their drivers | Should | See the requirement map | Pilot |
| CUS-11 | Customer segments for rules and peer groups | Should | See the requirement map | Pilot |

### KYC integration

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| KYC-1 | Store verification status, date and source supplied by the institution | Must | Status is visible on the customer record and in alerts | MVP |
| KYC-2 | Check BVN and NIN through a partner provider and store the provider reference, as supporting evidence and not complete verification | Should | The record labels the result as supporting evidence; a provider outage never blocks monitoring | Pilot |
| KYC-3 | Do not store raw identity document images unless the institution enables it with a retention setting | Could | Default tenants hold no document images | V1 |

### Sanctions and PEP screening

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| SCR-1 | Screen customers at onboarding and when lists update against licensed sanctions and PEP lists | Must | Required lists confirmed before build | Pilot |
| SCR-2 | Match review workflow with score, reasons, decision and approval | Must | Every possible match ends in a recorded decision | Pilot |
| SCR-3 | Keep screening results with list version and date | Must | An examiner can see which list version was used on a given date | Pilot |
| SCR-4 | Ongoing re-screening when lists change | Should | A list update triggers re-screening without manual action | V1 |
| SCR-5 | Institution-maintained internal watchlists | Must | See the requirement map | Pilot |
| SCR-6 | Adverse media monitoring | Could | See the requirement map | Later |
| SCR-7 | Automated hold or block on a confirmed match, returned through the check API | Should | See the requirement map | Pilot |
| SCR-8 | Log every list refresh with source, version and time | Must | See the requirement map | Pilot |

### Audit

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| AUD-1 | Record every create, update, approval, closure, login, export and configuration change with user, time and before and after values | Must | A sampled action always has a matching audit entry | MVP |
| AUD-2 | Audit records are append-only, enforced by database permissions | Must | The application role and every ordinary database user can insert and read audit rows but cannot update or delete them | MVP |
| AUD-3 | Searchable audit view for the auditor role | Must | Search by user, object and date returns the correct records | MVP |
| AUD-4 | Audit export in CSV and PDF | Should | Export matches the on-screen records | Pilot |
| AUD-5 | Retention settings per tenant; records past the retention period are removed only by a separate retention process | Should | The purge runs under its own database role that the application cannot use, removes only records older than the configured period, and writes an audit entry recording the date range and count removed. Minimum retention period is confirmed from regulation before build | Pilot |
| AUD-6 | Automated audit and governance reports | Should | See the requirement map | Pilot |
| AUD-7 | Every alert, case and report traces to its source transactions, the customer record version and the rule version used | Must | See the requirement map | MVP |

### Administration and notifications

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| ADM-1 | Administrator manages users, roles and approvers | Must | Role changes take effect at once and are audited | MVP |
| ADM-2 | Institution settings for thresholds, categories, closure reasons and fallback approver | Must | Settings changes are versioned and audited | MVP |
| ADM-3 | Email notifications for assignments, pending approvals and ageing alerts | Should | Notifications can be turned off per user | MVP |
| ADM-4 | Daily digest for the compliance officer | Should | Digest lists open alerts, pending approvals and overdue cases | Pilot |
| ADM-5 | Provision a new institution from a script in under one working day | Must | A new tenant is live and tested in under one working day | V1 |
| ADM-6 | Export an institution's own data on request | Should | Export contains all records the institution owns | V1 |
| ADM-7 | CBN solution notification pack describing the solution, its role and configuration | Should | See the requirement map | Pilot |

### Governance

| ID | Requirement | Priority | Acceptance criteria | Release |
| --- | --- | --- | --- | --- |
| GOV-1 | Evidence library for governance documents such as risk assessments, policies and committee approvals | Should | See the requirement map | V1 |

## User flow: from alert to filed report

1. A transaction arrives through the API or a batch file and is evaluated by the active rule versions.
2. A rule fires and creates an alert with its explanation, rule version and triggering values.
3. An analyst reviews the alert with the full customer view. **Decision one:** close the alert with a reason, or escalate it to a case.
4. The investigator works the case and records an outcome. **Decision two:** a second person approves or returns the case closure.
5. If the outcome is a report, the platform drafts it from the case. **Decision three:** a second person approves or returns the report.
6. The approved report is submitted to the NFIU, or exported for manual upload, and its status is tracked.

Alerts close only with a recorded reason, any case closure or report needs a second person's approval, and every step is written to the audit log.

## Non-functional requirements

Targets marked proposed are starting values, because volumes, buyer expectations and hosting options are not yet known. Confirm each one in Phase 0 and in pilot contracts.

| ID | Area | Requirement | Target | Release |
| --- | --- | --- | --- | --- |
| NFR-1 | Hosting location | All customer data, backups and logs are stored on infrastructure in Nigeria | Required by the standards' data sovereignty expectations; the reported 1 January 2027 date from the CBN circular of 15 June 2026 must be checked against the original; hosting providers to be confirmed with first buyers | MVP |
| NFR-2 | Tenancy | One isolated environment and database per institution, with its own encryption keys | Isolation verified by tests and review | MVP |
| NFR-3 | Encryption | Encrypt data in transit and at rest | TLS 1.2 or higher in transit; AES-256 at rest (proposed). Encryption in use is not planned; the decision and compensating controls are recorded in the architecture decisions | MVP |
| NFR-4 | Secrets and credentials | No secrets in code; passwords stored with a modern slow hash; keys rotated on a schedule | Argon2id for passwords (proposed); rotation policy written before pilot | MVP |
| NFR-5 | Availability | Monthly uptime of the production service | 99.5% or higher for the MVP and pilot (proposed); revisit for V1 | Pilot |
| NFR-6 | Backup and recovery | Encrypted backups stored in Nigeria with a tested restore | Recovery objectives are set per customer from their business impact analysis. Internal default from the pilot: at most one hour of data loss (proposed), which needs continuous log archiving with point-in-time restore, not daily dumps; recovery within 8 hours (proposed); a restore test every quarter | Pilot |
| NFR-7 | Performance | Alert queue and case pages respond quickly under normal load | Pages load in under 2 seconds for typical queues (proposed); ingestion throughput sized from pilot volumes | Pilot |
| NFR-8 | Scalability | Handle the volume of the target segment without redesign | Target volumes gathered in Phase 0; scale vertically first, add components only when measured load requires it | Pilot |
| NFR-9 | Data protection | Comply with the Nigeria Data Protection Act 2023 | Impact assessment before real customer data; breach notification within 72 hours; documented retention and deletion | Pilot |
| NFR-10 | Audit integrity | Audit records cannot be altered or deleted by the application or any ordinary database user | Enforced by database permissions and tested; only the retention process in AUD-5 removes expired records, and each removal is logged | MVP |
| NFR-11 | Security testing | Find weaknesses before buyers do | Dependency and static scanning in every build; internal review before pilot; independent penetration test before wide sales | V1 |
| NFR-12 | Observability | Detect and diagnose problems without exposing customer data | Structured logs without sensitive fields; metrics and alerts on ingestion, errors and job queues | Pilot |
| NFR-13 | Deployment | Repeatable, automated releases | Infrastructure as code, separate test and production environments, automated tests gate every release | MVP |
| NFR-14 | Browser support | Works on current versions of Chrome, Edge and Firefox on a laptop screen | Layouts tested at 1366 pixels wide; phone layout not required in the first three releases | MVP |
| NFR-15 | Localisation | Fit Nigerian operations | Naira as the default currency, Africa/Lagos time zone for display, timestamps stored in UTC, English interface | MVP |
| NFR-16 | Support access | Staff can help without browsing customer data | Customer-approved, time-limited, logged access only | Pilot |
| NFR-17 | Service objectives per customer | Availability and recovery objectives are agreed per customer | See the requirement map | Pilot |
| NFR-19 | Query isolation | Audit and history queries must not slow monitoring | See the requirement map | Pilot |

**Open hosting question:** I believe the major global cloud providers do not operate a Nigerian region, so in-country data centres or local providers are the realistic route. This comes from general knowledge and is unverified. Confirm which hosting options are available, which ones your first buyers accept, and whether managed Postgres with point-in-time restore is offered there, because the answer affects cost, operations and NFR-6. Cloud providers must also meet the standards' vendor due diligence expectations.

## Data model and integrations

The entities below are a first draft. Field lists are confirmed against real export files from pilot institutions, because each core banking system structures data differently. Each institution's data lives in its own database.

**Core entities**

| Entity | Purpose | Key fields (draft) |
| --- | --- | --- |
| Institution | The tenant | Name, licence type, settings version, fallback approver |
| User and role assignment | Who can do what | Email, role, status, second-factor state |
| Customer | A person or business the institution serves | External customer ID, name, type (individual or business), KYC or KYB status and date, risk rating, segment, expected activity profile, record version |
| Beneficial owner and related party | Ownership, control and relationships of a customer | Customer, party name, role, ownership share, control type, KYC status, escalation flag |
| Account | A customer's account or wallet | External account ID, customer, product, opened date, status |
| Transaction | One movement of money | External transaction ID, account, timestamp in UTC, amount in minor units, currency, direction, channel, counterparty name and account, narration, source and batch |
| Check request | A synchronous pre-authorisation check | Request ID, customer, proposed transaction or onboarding event, result (allow, review, hold), rules evaluated, response time |
| Ingestion batch | One delivery of records | Source, received time, counts accepted and rejected |
| Dead-letter record | A rejected record | Raw content, reason, batch, status |
| Rule and rule version | Detection logic and its history | Rule type, thresholds, severity, effective date, rationale, approver |
| Alert | A rule firing | Rule version, customer record version, source transactions, explanation, triggering values, status, assignee, closure reason, closure type (manual or automated) |
| Case | An investigation | Linked alerts, customer, timeline, outcome, approver |
| Report | A draft or submitted report | Type (STR, SAR, CTR, FTR), case, content, status, approver, submission reference |
| Risk rating | A customer's rating over time | Rating, factors, rationale, date, reviewer |
| Screening list, refresh and result | Sanctions, PEP and internal watchlist checks | List name, source and version, refresh time, match score, decision, approver |
| Risk signal | A signal exchanged with a fraud system | Direction, customer, type, score, source system, time |
| Webhook subscription and delivery | Outbound event notification | Endpoint, events, signing key reference, delivery status and attempts |
| Audit entry | Record of an action | User, time, action, object, before and after values |
| Setting version | Configuration history | Thresholds, categories, closure reasons, effective date |

**Data rules**

- Store only what monitoring and reporting need.
- Keep amounts as integer minor units with a currency code.
- Never edit history: rule versions, setting versions, customer record versions, risk ratings and audit entries are added to, not overwritten. Only the retention process in AUD-5 removes expired records.
- Keep raw identity document images out of the platform by default.

**Integrations**

| Integration | Purpose | Direction | Release | Status |
| --- | --- | --- | --- | --- |
| REST API | Submit transactions and customers; the default integration | Institution to platform | MVP | Documented schema to write |
| SFTP batch intake | Deliver CSV files where API integration is not possible | Institution to platform | MVP | Template to write |
| Synchronous check API | Allow, review or hold before authorisation or onboarding completion | Institution to platform and back | Pilot | Specified in the API specification draft |
| Signed webhooks | Notify institution systems of alert, case and report events | Platform to institution | Pilot | Specified in the API specification draft |
| Risk signal exchange | Share risk signals with the institution's fraud system | Both directions | V1 | To specify with a pilot institution |
| Email delivery service | Notifications and digests | Platform to users | MVP | Provider to choose, preferably with in-country handling of content |
| KYC verification provider | BVN and NIN checks | Platform to provider | Pilot | Provider to choose after interviews |
| Sanctions and PEP list provider | Licensed list data | Provider to platform | Pilot | Provider and required lists to confirm |
| NFIU reporting channel | Submit suspicious and currency transaction reports | Platform to NFIU, or export for manual upload | Pilot | Channel and format to confirm; one secondary source says the NFIU uses goAML |
| Core banking connectors | Load data from the cores pilot customers run | Core to platform | Pilot | Built only for the cores that real customers use |

## Regulatory traceability

This table links the main expectations of CBN Circular BSD/DIR/PUB/LAB/019/002 and the guidance note to the requirements in this document. Section 5.1 of the circular lists nine functional areas, and Section 5 has twelve subsections, 5.1 to 5.12. The authoritative clause-by-clause map, using the circular's own numbering, is the CBN requirement map in the Engineering Specification Pack. Section references below are from that reading and must be checked against the CBN PDFs.

| Expectation | Circular reference | Requirements that serve it | Status |
| --- | --- | --- | --- |
| Customer identification and verification, including KYB and beneficial ownership | 5.2 b iii | KYC-1, KYC-2, CUS-1, CUS-6, CUS-7 | KYC in MVP and Pilot; KYB data model in MVP, workflow in Pilot |
| Integration with KYC, KYB, risk profiles and core banking | 4, 5.2, 5.10 | ING-1, ING-9, ING-11, CUS-8, RUL-8 | API-first from MVP; synchronous checks in Pilot |
| Risk assessment and customer profiling | 5.4 | CUS-2, CUS-3, CUS-4, CUS-5, CUS-9, CUS-10, CUS-11 | Manual rating in MVP; automated scoring by Pilot |
| Sanctions, PEP and internal watchlist screening before authorisation or onboarding completion | Glossary | SCR-1 to SCR-8, ING-9 | Pilot and V1; required lists to confirm |
| Transaction monitoring with pre-emptive action where risk justifies it | Glossary, 5.5 a ii | ING-1 to ING-9, RUL-1 to RUL-11, ALT-1 to ALT-10 | Monitoring in MVP; pre-emptive checks in Pilot |
| Fraud monitoring on the same platform or a separate system exchanging risk signals | 4 j, 5.6 | ING-10 | Fraud rules are a non-goal; risk signal exchange in V1 |
| Threshold justification, tuning and false positive and false negative thresholds | 5.5, 5.8 | RUL-2, RUL-5, RUL-7, RUL-11, REP-10 | MVP to V1 |
| Automated alert closure only for clearly low-risk scenarios, with back-testing, internal audit review and CBN notification | 5.5 b vii, glossary | ALT-7, ADM-7 | V1, off by default |
| Case management and structured investigations | 5.5 | CAS-1 to CAS-9, ALT-10 | MVP to V1 |
| Suspicious and currency transaction reporting | 5.5 | REP-1, REP-2, REP-3, REP-5, REP-8 | Drafts in MVP; NFIU format by Pilot once confirmed |
| Reporting to the compliance officer, senior management and Board, and effectiveness metrics | 5.8; guidance note 4 | REP-4, REP-7, REP-10, AUD-6 | MVP and Pilot |
| Tamper-proof, immutable audit trails retrievable without disrupting operations and retained for the regulatory period | 5.9 | AUD-1 to AUD-7, ACC-4, CAS-5, NFR-10, NFR-19 | MVP and Pilot |
| Governance and documented decision-making | Guidance note 4 | ACC-4, RUL-5, CAS-4, ALT-7, GOV-1 | MVP to V1 |
| Data protection, data sovereignty and recovery objectives from a business impact analysis | 5.11 | NFR-1, NFR-3, NFR-6, NFR-9, NFR-17, KYC-3, AUD-5 | MVP and Pilot |
| Notifying the CBN of AML solutions in use | 5.2 b ii | ADM-7 | Pilot |
| Shared or centralised arrangements need prior CBN approval | 5.10 c | None; legal question | Open |
| Heightened due diligence on AML vendors and cloud providers | 6 | Vendor due diligence pack, REP-6, ADM-6 | Pack drafted in the legal and customer pack |
| Proportional calibration shown to an examiner | 4; guidance note 8 | RUL-2, RUL-5, REP-6 | Examiner pack by V1 |

**Still open after reading the circular**

- The NFIU report format and submission channel.
- The minimum record retention period.
- The required sanctions and watchlists, including any Nigerian lists.
- Whether a vendor-hosted platform serving several institutions needs prior CBN approval as a shared or centralised arrangement.

**Contractual note for sales:** the platform supports an institution's compliance, but the institution remains responsible for its own obligations. Do not claim the product makes a buyer compliant, or that it is approved by or aligned with the CBN.

## Analytics and success measurement

Two sets of measures: what each institution sees about its own monitoring, and what we track internally to learn whether the product works. Internal analytics hold counts and timings only, never customer personal data or transaction contents.

**Shown to each institution**

| Measure | Definition | Why it matters | Release |
| --- | --- | --- | --- |
| Alerts per 1,000 transactions | Alerts created divided by transactions evaluated, scaled | Shows whether thresholds are too tight or too loose | MVP |
| Alerts by rule | Count of alerts per rule over a period | Shows which rules produce noise | MVP |
| False positive share | Alerts closed with no suspicion divided by alerts closed | Guides calibration and gives the rationale for threshold changes | MVP |
| STR conversion rate | Reports filed divided by cases closed, over a period | One of the four metrics the CBN roadmap template asked for | MVP |
| Investigation turnaround | Median time from case opening to approved closure | One of the four metrics the CBN roadmap template asked for | MVP |
| Time to first review | Median time from alert creation to first analyst action | Shows whether the team keeps pace | MVP |
| Time to close | Median time from creation to closure, for alerts and cases | Shows workload and delays | MVP |
| Ageing | Open alerts and cases by age bucket | Shows what an examiner would flag as overdue | MVP |
| Case outcomes | Cases closed with no suspicion, closed with a report, or referred | Shows investigation yield | MVP |
| Reports filed | Reports drafted, approved and submitted over time | Shows reporting activity | MVP |
| Ingestion health | Records accepted and rejected per source with top rejection reasons | Shows data quality problems early | Pilot |
| Pending approvals | Rule changes, case closures and reports waiting for a second person | Shows bottlenecks | MVP |

**Tracked internally**

| Measure | Definition | Purpose |
| --- | --- | --- |
| Active institutions | Tenants with logins and ingested data in the last 30 days | Adoption |
| Weekly active users | Distinct users per week per tenant | Engagement |
| Time to first alert after go-live | Days from tenant creation to first alert reviewed | Onboarding quality |
| Feature use | Which modules each tenant uses | Prioritisation |
| Support requests | Count and cause by tenant | Product gaps and onboarding cost |
| Uptime and ingestion error rate | Service availability and failed deliveries | Reliability |
| Pilot to paid conversion | Pilots that sign a contract or letter of intent | Commercial validation |

**Rule for metrics:** a measure that cannot lead to a decision is not tracked. Review the list after each release and remove anything nobody used.

## Dependencies, assumptions and risks

**Dependencies**

- The CBN's own PDFs of Circular BSD/DIR/PUB/LAB/019/002 and the guidance note, to confirm the aggregator copy used for the requirement map, and the 15 June 2026 data localisation circular.
- Sample export files from at least two pilot institutions' core banking systems.
- An AML advisor with practical reporting-officer experience to review rules, workflows and report content.
- An in-country hosting provider that buyers accept and that passes vendor due diligence.
- A KYC verification partner and a sanctions and PEP list licence for the pilot release.
- A Nigerian lawyer to review the customer agreement, data processing terms and liability language before any paid pilot, and to answer the shared-services approval question before the first pilot.

**Assumptions**

- Buyers will accept a cloud-hosted service if their data stays in Nigeria.
- Small institutions can integrate by API, or can justify batch files as a fallback.
- A compliance office of one to three people is typical for the primary segment. This is unverified.
- A named fallback approver can approve case closures and reports in a one-person compliance office without becoming a bottleneck. This is unverified.
- Rules-based detection with clear explanations meets buyers' needs for the first three releases. The guidance note confirms rules-based systems are acceptable.
- Pilot institutions will share real or anonymised data under a signed agreement.

**Risks specific to the product**

| Risk | Effect | Mitigation | Owner |
| --- | --- | --- | --- |
| Requirement map built from an aggregator copy that differs from the CBN original | Product built to the wrong standard | Check against the CBN PDFs before build; advisor review | Khalid |
| MVP backlog exceeds the time before the MVP date | Missed MVP or half-built features | MVP moved to 19 March 2027 with 20 to 25 days of margin; re-estimate after the first sprint; apply the release rule | Khalid |
| External dependencies with long lead times (sanctions licence, KYC partner, NFIU format, legal review, shared-services question) | Pilot release slips past May 2027 | Start each request in November 2026; track a date for each | Khalid |
| Vendor-hosted platform needs prior CBN approval as a shared arrangement | Pilots blocked or delayed | Ask a lawyer before pilot contracts; design the notification pack to support any approval request | Khalid with legal advice |
| Data quality from core banking exports | Ingestion consumes the schedule | Request real samples before committing; build the dead-letter and mapping tools early | Khalid |
| Hosting options in Nigeria are limited or costly | Delays, higher cost or buyer rejection | Confirm options with first buyers before choosing; keep deployment automated so providers can change | Khalid |
| A missed alert creates liability | Contract disputes or reputational damage | Clear scope in contracts, liability cap, full decision logs, no claim of compliance | Khalid with legal advice |
| Compliance wording slips into sales or product material | Loss of credibility with buyers and examiners | Review all outward material against the no-claims rule | Khalid |
| Failing buyer due diligence | Lost deals | Vendor due diligence pack, pentest, data protection impact assessment, documented processes, pilot references | Khalid |
| Customers expect features beyond the MVP | Scope creep and delay | Release rule: unfinished capabilities are cut, not shipped half-built | Khalid |
| Time and runway spent before revenue | Building without a paying customer for months | Fund the plan with a stated runway; use consulting income; seek a paid pilot early | Khalid |
| Competitors with funding and references | Harder sales | Choose one segment, win pilots, collect references, and keep the product focused on explainable evidence | Khalid |
| Regulation changes after build starts | Rework | Keep rules and settings configurable; monitor CBN circulars | Khalid |

## Open questions

Resolve the first group before writing code, and the rest before the release that depends on them.

**Before build**

- [x] What does the circular text require, clause by clause, and how many capabilities does it list? Answered in the requirement map: nine functional areas in Section 5.1, twelve subsections in Section 5.
- [x] Does the baseline expect fraud monitoring and KYB inside the same system? KYB is required. Fraud monitoring is required but may run on a separate system that exchanges risk signals.
- [x] What counts as real-time monitoring under the circular? Defined in the glossary; see the regulatory constraints section.
- [ ] Does the CBN original match the aggregator copy the requirement map was built from?
- [x] Move the MVP date or cut scope? Moved to 19 March 2027 with full scope; see the schedule in the release plan.
- [x] Does the 65-day MVP backlog already count CUS-6, CUS-8, REP-10 and AUD-7? Yes: stories 1.1, 1.2, 3.1, 3.3 and 5.3 cover them.
- [ ] Which in-country hosting providers exist, what do they cost, do they offer point-in-time restore, and do first buyers accept them?
- [ ] What is the working product name and company entity? The name must not suggest compliance or CBN approval.
- [ ] Are you building solo and full time, and how many months of runway cover the work?

**Before the pilot release**

- [ ] Does a vendor-hosted platform serving several institutions need prior CBN approval as a shared or centralised arrangement?
- [ ] Which core banking systems do the first target MFBs run, and what do they export or expose by API?
- [ ] What NFIU report format and channel apply, and can submissions be automated?
- [ ] Which sanctions and PEP lists must be screened, and from which licensed provider?
- [ ] What minimum record retention period applies?
- [ ] Which KYC verification provider will you use for BVN and NIN checks?
- [ ] Can the smallest institutions name a fallback approver who will approve every case closure and report, or should approval of some outcomes be configurable?
- [ ] What did each target institution write in its CBN roadmap submission of 10 June 2026?

**Before wide sales**

- [ ] What price and payment structure do buyers expect?
- [ ] Which independent firm will run the penetration test?
- [ ] Which lawyer will review the customer agreement and liability language?
- [ ] Is a professional indemnity policy needed before the first paid contract?

## Change log

| Date | Change |
| --- | --- |
| 2 November 2026 | First draft from secondary sources |
| 2 November 2026 | Revision 2. Folded in the findings from the full circular text and guidance note: removed compliance claims from the overview and goals; added the regulatory constraints section; made integration API-first; added 29 requirements and changed RUL-6, ALT-7, NFR-3, NFR-6 and KYC-2; rebuilt the traceability table; settled answered questions. Also fixed issues found in review: the audit immutability and retention conflict (AUD-2, AUD-5, NFR-10), REP-1 depending on an unconfirmed NFIU format, the unverified data localisation source, the rule count difference, the schedule gap and the missing user flow |
| 2 November 2026 | MVP moved from mid February to 19 March 2027 with full scope. Pilot release moved from April to the end of May 2027 and version 1 from July to the end of August 2027. Pilot, paid contract and onboarding targets moved with them |
