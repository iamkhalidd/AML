# Legal, Security and Customer Documentation Pack

Nov 2, 2026 · @Khalid Adebiyi

## How to use this pack

These are working drafts for a platform operator that hosts an AML system for regulated Nigerian institutions. **They are not legal advice and no lawyer has reviewed them.** Treat every contractual and policy document as a first draft that a Nigerian lawyer with fintech and data protection experience must rewrite or approve before any customer sees it. Where I cite a law or regulator, I name it only at the level I could verify. I avoid section numbers I could not check.

**Placeholders.** Square brackets mark facts that only you can supply, such as \[Company name\], \[Registered address\], \[Contact email\], \[Hosting provider\] and \[Fee\]. Search for the opening bracket before sending anything.

**Who plays which role.** Under the Nigeria Data Protection Act 2023, the institution is normally the data controller and you are its data processor, because you process customer data on the institution's instructions. The documents are written that way. A lawyer should confirm it, because in some activities you may become a controller, for example when you use account usage data to improve the product.

**Three facts from the CBN texts that shape every document here.**

1. The CBN does not approve, certify or endorse AML vendors, and does not recognise vendor claims of full compliance. No document, website or sales material may say the product is compliant, approved or aligned with CBN standards. The agreement and the pilot terms say the same thing to the customer.
2. Institutions remain fully responsible for compliance even when they use a vendor. The agreement allocates responsibilities accordingly.
3. Institutions must apply heightened due diligence to AML vendors, including checking configuration support and access to audit and validation data, and cloud providers must meet the standards. The vendor due diligence pack answers that directly.

**Three kinds of document.**

| Kind | Documents | Audience |
| --- | --- | --- |
| Internal | Data protection impact assessment, security and access policy, breach response, backup and restore runbook | You and your auditors |
| Contractual | Data processing agreement, customer agreement, service level terms, pilot agreement | Customers, after legal review |
| Customer-facing | Privacy policy, vendor due diligence pack, integration guide, user guide, readiness checklist | Customers and prospects |

**Open legal questions that affect several documents** (also listed in the engineering pack):

- [ ] Does a vendor-hosted platform serving several institutions count as a shared or centralised arrangement needing prior CBN approval?
- [ ] What minimum retention periods apply to AML records and audit trails?
- [ ] Do you need to register with the Nigeria Data Protection Commission as a data processor, and does your size change the answer?
- [ ] Which governing law, court or arbitration clause suits you and your customers?

## Document index

Thirteen documents sit under this page. All are drafts. None has been reviewed by a lawyer, a data protection specialist or a customer.

| Document | Kind | Needed before | Reviewer |
| --- | --- | --- | --- |
| Data protection impact assessment | Internal, adopted by customers | First pilot with real data | Lawyer, data protection specialist, customer's data protection officer |
| Data processing agreement | Contractual | First pilot with real data | Lawyer |
| Privacy policy | Public | Website launch or first outreach | Lawyer |
| Customer agreement | Contractual | First paid contract | Lawyer |
| Service level terms | Contractual | First paid contract | Lawyer, and your honest view of your own capacity |
| Security and access policy | Internal | First pilot | You, and a security reviewer |
| Breach response | Internal | First pilot | Lawyer, then a tabletop exercise |
| Backup and restore runbook | Internal | First pilot | You, after a real restore drill |
| Pilot agreement | Contractual | First pilot | Lawyer |
| Vendor due diligence pack | Customer-facing | First serious sales conversation | You, and a lawyer for the regulatory statement |
| Integration guide | Customer-facing | First pilot | A pilot institution's developer |
| User guide | Customer-facing | First pilot | An analyst and your AML advisor |
| Readiness checklist | Customer-facing and sales tool | Whenever you start outreach | Your AML advisor |

## Order of work

- [ ] **Engage a Nigerian lawyer** with fintech and data protection experience. Give them this pack, the CBN circular and the guidance note. Ask for answers to the open legal questions first.
- [ ] **Adopt the security and access policy** only after you cut every statement you will not follow. Then start following it.
- [ ] **Before real data:** finalise the data protection impact assessment and the data processing agreement with the lawyer.
- [ ] **Before the pilot:** sign the pilot agreement, rehearse the breach procedure, and run the restore drill in staging and fill in the test log.
- [ ] **Before the first paid contract:** finalise the customer agreement and service level terms. Check every service level number against what you can actually deliver alone.
- [ ] **When the product exists:** update the integration guide and user guide with real screens and steps. Change rows in the vendor due diligence pack from Planned to Available only with evidence.
- [ ] **Whenever you start outreach:** use the readiness checklist as a conversation starter, after your AML advisor reviews it.

**Things only you can fill in.** Company name, address and registration number; hosting provider and locations; service targets you can meet; fees; insurance; support hours; the people named as incident lead, deputy and contacts; your retention periods once the lawyer confirms them.

**Two rules for every document in this pack.**

- Never state or imply that the product is compliant, approved, certified or aligned with CBN standards. The CBN has said it does not recognise such claims.
- Never promise a service level, security control or capability you cannot evidence on the day you sign.
