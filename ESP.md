# Engineering Specification Pack: Cloud AML Platform

Nov 2, 2026 · @Khalid Adebiyi

## What changed after reading the circular

On 2 November 2026 I read the full text of CBN Circular BSD/DIR/PUB/LAB/019/002 of 10 March 2026 and the CBN guidance note CMD/DIR/PUB/CIR/001006 of 31 March 2026, as reproduced on [RegAlert](https://regalert.today/document/b956b444-15d4-4c0d-9157-adc8dca01a79/text) and its [guidance note page](https://regalert.today/document/8c89fd78-3ed5-4b1f-bcc7-e415ddcf04a9/text). RegAlert links the CBN originals but is an aggregator, so compare against the CBN PDFs before any legal reliance. This replaces the secondary sources the earlier plan and PRD relied on, and several findings change the product.

| # | Finding from the primary text | What it means for the product |
| --- | --- | --- |
| 1 | The CBN does not approve, certify or endorse AML solutions or vendors, and does not recognise vendor claims of full compliance or alignment with CBN requirements. Compliance is assessed at the institution level (guidance note, sections 1, 3 and 6) | Never describe the product as CBN-compliant, approved or aligned in marketing, contracts or the product name. Sell evidence, governance support and measurable effectiveness instead |
| 2 | The standards are technology neutral. Rules-based, machine learning and hybrid systems are all allowed, and AI is not mandated (guidance note, section 2). If AI or ML is used, models need independent validation at least annually (section 5.5 b) | Rules-first is safe. No AI work is needed for compliance, which removes a major scope risk |
| 3 | Compliance means defensibility (audit trails, explainable decisions, traceability), governance (ownership, model validation, change control, structured investigations) and effectiveness (credible detection, timely investigation, measurable outcomes including false positive management) (guidance note, section 4) | These become the three organising themes of the product and the vendor pack |
| 4 | A proportionality principle is explicit: depth and configuration are calibrated to volumes, products, customers, geography and the institution's documented risk assessment (section 4; guidance note, section 8). Purely manual processes still fail | Calibration evidence is a real requirement, not only a differentiator. Small institutions need less, but never nothing |
| 5 | Integration is central. The system must link to KYC and KYB data and customer risk profiles. Fragmented or partly integrated systems will not meet expectations. Higher-risk institutions may not rely on standalone transaction feeds. Secure two-way integration with core banking and standards-based APIs are required (sections 4, 5.2, 5.10; guidance note, section 5) | A CSV file overlay is the weakest form of delivery. Design for API integration and customer context first. Keep batch files only as a fallback that buyers can justify |
| 6 | Real-time has two definitions: for screening, evaluate before a transaction is authorised or onboarding completes; for monitoring, evaluate as transactions occur and be able to trigger pre-emptive action before funds irreversibly settle. Pre-emptive alerts and holds are needed where justified by risk (glossary; section 5.5 a ii). Section 4(j) says AML monitoring does not require fraud-level real-time speed | Add a synchronous check API for pre-transaction screening and optional holds. Batch-only buyers will need to justify their approach. Update RUL-6 |
| 7 | KYB and beneficial ownership are in scope: record complex ownership and control structures, escalating manually where the system falls short (section 5.2 b iii) | The PRD marked KYB as a gap. Add business customers, beneficial owners and related parties to the data model |
| 8 | Institutions must notify the CBN supervisory department of all AML solutions in use and their roles, at deployment, after material change and on request (section 5.2 b ii). Shared services or centralised arrangements need prior CBN approval (section 5.10 c) | Generate a solution notification pack. Ask a lawyer whether a vendor-hosted platform serving several institutions counts as a centralised arrangement needing approval |
| 9 | Automated alert closure is tightly defined. A clearly low-risk scenario needs all of: a rule approved by the governance committee, closure based on both transaction and current KYC or KYB data, a Low customer risk rating unchanged in the review period, and no unresolved alerts or open cases. Back-testing, annual review by internal audit and CBN notification of the practice and threshold are also required (section 5.5 b vii and glossary) | Specify ALT-7 exactly, keep it off by default, and build the evidence the institution must show |
| 10 | No single AML and fraud system is mandated, but automated fraud monitoring is required, either on the same platform or a separate one that exchanges risk signals (sections 4 j, 5.6) | The non-goal on fraud still stands. Add an API to receive and send risk signals |
| 11 | Institutions must document ML, terrorism financing and proliferation financing risk assessments, justify thresholds and tuning decisions at least annually, set false positive and false negative thresholds, approve internal service-level standards for high-risk alerts, and report to the chief compliance officer, senior management and the Board (sections 5.4, 5.5, 5.8) | Add an annual tuning review workflow, service-level timers, a board report and the STR conversion rate metric |
| 12 | Audit trails must be tamper-proof and immutable, retrievable without disrupting operations, and retained for the regulatory period. Recovery time and recovery point objectives come from a business impact analysis. Data processing must respect Nigerian data sovereignty law (sections 5.9, 5.11) | Confirms the append-only audit design. The proposed 24-hour data loss limit is too loose to promise as a default and must come from each customer's analysis |
| 13 | Institutions must apply heightened due diligence to AML vendors, including the ability to support institution-specific configuration and give access to audit and validation data. Cloud providers must meet the same standards (section 6) | A vendor due diligence pack is now a required deliverable. It is in the legal and customer pack |
| 14 | The CBN roadmap template, due 10 June 2026, asked for current and target architecture, data integration plans, governance plans and effectiveness metrics: alert volumes, false positive rate, STR conversion rate and investigation turnaround time | Customers have already declared an approach. Ask what they wrote. Report those four metrics in the product and mirror the template in the readiness checklist |
| 15 | The capability count dispute is settled. Section 5.1 lists nine functional areas, and Section 5 has twelve subsections, 5.1 to 5.12. The earlier figures of seven, eight and twelve counted different groupings | The requirement map in this pack uses the circular's own numbering |

**Corrections to earlier statements.** I told you examiners worry about black box AI, quoting a vendor article. The circular does stress explainability where AI is used, but it is technology neutral. I also earlier listed hosting questions as open, and they remain open. The 12 March 2026 mobile banking and instant payment circular and the 15 June 2026 data localisation circular were not in the primary text I read here and remain from secondary sources.

**What needs your decision.**

- [ ] Rename positioning to drop any compliance or alignment claim, and keep the product name neutral.
- [ ] Add a synchronous check API to the pilot release and make API-first integration the default offer.
- [ ] Add KYB and beneficial ownership to the MVP data model.
- [ ] Ask a Nigerian lawyer whether a vendor-hosted, multi-institution platform needs CBN approval as a shared or centralised arrangement.

## Document index

Eleven documents sit under this page. Every one is a draft written from the PRD and the primary text of the circular, and none has been reviewed by an AML practitioner, a lawyer or a pilot customer.

| Document | What it settles | Status | Blocks | Needs outside input |
| --- | --- | --- | --- | --- |
| CBN requirement map | Clause by clause what the standards require and which requirement serves it | Draft | Scope of every other document | Check against the CBN PDFs; lawyer for the listed questions |
| Rule specifications | Logic, parameters, wording and tests for 13 rules and the screening match | Draft | Rules engine coding | AML advisor review; thresholds calibrated with real data |
| Data contract | Exact fields and error codes for data the institution sends | Draft v0.1 | Ingestion coding | A real export file from a core banking system |
| Architecture decisions | Thirteen decisions that are costly to reverse | Proposed | All coding | Your acceptance; hosting quotes for ADR-002 |
| Data model | Tables and PostgreSQL schema for one institution | Draft | First migration | Confirmation of the data contract |
| Infrastructure plan | Environments, network, deployment, backups, secrets, monitoring | Draft | Staging and pilot | The hosting decision |
| API specification | Integration API, check API, webhooks and errors | Draft | Integration work | Review by a pilot institution's developer |
| Threat model | 24 threats with mitigations and a testing plan | Draft | Security review | Your review and ratings |
| Test strategy | Test levels, generator, 31 acceptance tests, exit criteria | Draft | CI setup | None |
| MVP backlog | 65 days of stories mapped to requirement IDs | Draft | Planning | Your decision on date or scope |
| Screen specifications | Five core screens and the rules every screen follows | Draft | Frontend work | Review by the advisor and one analyst |

## Build order and what is still missing

**Do these before the first line of product code.**

- [ ] Read the CBN originals and confirm the requirement map matches them. Rebuild any row that differs.
- [ ] Accept, change or reject each architecture decision. ADR-001 and ADR-009 matter most.
- [ ] Decide between moving the MVP date and cutting scope, using the backlog.
- [ ] Put the legal questions to a Nigerian lawyer, above all the shared-services approval question.
- [ ] Ask for hosting quotes against the ADR-002 criteria from at least two Nigerian providers.
- [ ] Ask your first interviewees for an anonymised sample export, and meanwhile use the generator in the test strategy.

**Then build in this order:** stories 0.1 to 0.6, then ingestion, then the rules engine, then alerts and cases, then reporting, then hardening. The backlog gives dependencies.

**What I could not do, and why.**

- I read the circular and guidance note through an aggregator's copy, not the CBN's own file.
- I could not verify Nigerian hosting options, prices or provider claims.
- I could not obtain NFIU report formats, retention periods or the required sanctions lists, because the circular does not give them.
- I cannot give legal advice. The legal and customer pack contains drafts for a lawyer to rewrite.
- Rule thresholds are illustrative. They need an AML practitioner and real data.

**Two things to keep in mind while building.** The CBN says it does not approve vendors and does not recognise compliance claims, so never describe the product as compliant. And the standards judge institutions on defensibility, governance and effectiveness, so build evidence into every workflow, not only features.
