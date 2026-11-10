"""The drafting studio's catalogue: what each document is, the facts it asks for, and how to draft it.

A template is guidance, not a form letter: the model writes the document fresh from the lawyer's facts.
Every field is optional to the model; anything missing becomes a [placeholder] in the draft."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str = ""
    required: bool = False
    long: bool = False  # a paragraph rather than a line

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "hint": self.hint,
            "required": self.required,
            "long": self.long,
        }


@dataclass(frozen=True)
class Template:
    key: str
    category: str
    name: str
    description: str
    brief: str  # drafting guidance sent to the model
    fields: tuple[Field, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "name": self.name,
            "description": self.description,
            "fields": [f.as_dict() for f in self.fields],
        }


def _party(key: str, label: str, required: bool = True) -> Field:
    return Field(
        key, label, "Full name, description (individual, company, firm) and address", required, long=True
    )


JURISDICTION = Field("jurisdiction", "Governing law and courts", "e.g. Laws of India; courts at Mumbai")
DISPUTES = Field(
    "disputes", "Dispute resolution", "e.g. Arbitration, sole arbitrator, seat New Delhi; or courts only"
)
FACTS = Field("facts", "Facts", "What happened, with dates and amounts", required=True, long=True)

CONTRACTS = "Contracts"
NOTICES = "Notices"
DEEDS = "Affidavits and authorities"
ADVICE = "Advice and letters"
OTHER = "Any other document"

_EXECUTION = (
    "End with an execution block for each party (name, signature, designation where relevant, date and place) "
    "and two witnesses. Add a note that it is to be executed on stamp paper of the value required by the "
    "applicable Stamp Act."
)

_NOTICE_FORM = (
    "Lay it out in the customary Indian form: the advocate's name and address at the top, then Ref. and "
    "Date, the mode of dispatch (By Registered Post A.D. / Speed Post / E-mail), To and the "
    "addressee's name and address, a Sub: line, Sir/Madam, then paragraphs numbered simply 1., 2., 3. "
    "(no sub-numbers), opening with "
    '"Under instructions from and on behalf of my client ... I hereby serve upon you the following '
    "notice:\". End with the advocate's signature block and a line that a copy is retained for further "
    "action. "
)

TEMPLATES: tuple[Template, ...] = (
    Template(
        "nda",
        CONTRACTS,
        "Non-disclosure agreement",
        "Mutual or one-way confidentiality agreement before sharing business information.",
        "a non-disclosure (confidentiality) agreement. Cover: parties and recitals, definition of "
        "Confidential Information, exclusions (public, already known, independently developed, received "
        "lawfully from a third party), obligations and permitted use, disclosure required by law or a "
        "regulator, return or destruction, no licence or warranty, term and survival of obligations, "
        "remedies including injunctive relief under the Specific Relief Act, 1963, no assignment, notices, "
        "entire agreement, governing law, jurisdiction and dispute resolution. " + _EXECUTION,
        (
            Field("nda_type", "Mutual or one-way", "Mutual / one-way (who discloses?)"),
            _party("party_a", "First party"),
            _party("party_b", "Second party"),
            Field(
                "purpose", "Purpose", "Why the information is shared, e.g. evaluating a joint venture", True
            ),
            Field("term", "Term", "e.g. 2 years; confidentiality survives 3 years after"),
            JURISDICTION,
            DISPUTES,
        ),
    ),
    Template(
        "service_agreement",
        CONTRACTS,
        "Service agreement",
        "A provider supplies services to a client for fees.",
        "a service agreement. Cover: parties and recitals, definitions, scope of services (a schedule), "
        "service standards, client obligations, fees, invoicing, GST and TDS, payment terms and late payment, "
        "term and renewal, termination for convenience and for breach with cure period, consequences of "
        "termination, confidentiality, intellectual property, data protection under the Digital Personal "
        "Data Protection Act, 2023 where personal data is handled, warranties, indemnities, limitation of "
        "liability, force majeure, non-solicitation, independent contractor, assignment, notices, "
        "governing law, jurisdiction and dispute resolution. " + _EXECUTION,
        (
            _party("provider", "Service provider"),
            _party("client", "Client"),
            Field("services", "Services", "What will be done, deliverables, timelines", True, long=True),
            Field("fees", "Fees and payment", "Amount, milestones or monthly, GST, payment days"),
            Field("term", "Term", "Start date and duration"),
            JURISDICTION,
            DISPUTES,
        ),
    ),
    Template(
        "employment",
        CONTRACTS,
        "Employment agreement",
        "Appointment terms for an employee.",
        "an employment agreement. Cover: appointment and designation, date of joining, place of work and "
        "transfer, probation and confirmation, duties, working hours, remuneration (CTC broken down in a "
        "schedule), statutory benefits under the applicable labour laws (provident fund, ESI, gratuity, where they apply), leave, "
        "confidentiality, intellectual property assignment, conflict of interest, non-solicitation, "
        "a post-employment restraint limited to what Section 27 of the Indian Contract Act, 1872 allows, "
        "code of conduct and POSH policy, notice period and termination, return of property, full and final "
        "settlement, governing law and jurisdiction. End with signatures for the employer and the employee.",
        (
            _party("employer", "Employer"),
            _party("employee", "Employee"),
            Field(
                "designation",
                "Designation and reporting",
                "e.g. Senior Associate, reporting to the CFO",
                True,
            ),
            Field("ctc", "Remuneration", "Annual CTC and components"),
            Field("start", "Joining date and place of work"),
            Field("probation", "Probation and notice period", "e.g. 6 months probation; 60 days notice"),
            JURISDICTION,
        ),
    ),
    Template(
        "consultancy",
        CONTRACTS,
        "Consultancy agreement",
        "An independent consultant or contractor engaged for a project or retainer.",
        "a consultancy agreement with an independent consultant. Cover: engagement and scope, deliverables, "
        "independent contractor status (no employment relationship), fees, expenses, invoicing, GST and TDS, "
        "term and termination, confidentiality, ownership and assignment of work product, warranties, "
        "indemnity, limitation of liability, non-solicitation, conflicts, assignment and subcontracting, "
        "notices, governing law, jurisdiction and dispute resolution. " + _EXECUTION,
        (
            _party("company", "Company"),
            _party("consultant", "Consultant"),
            Field("scope", "Scope of work", "Services, deliverables, time commitment", True, long=True),
            Field("fees", "Fees", "Retainer or per deliverable, expenses, GST"),
            Field("term", "Term"),
            JURISDICTION,
            DISPUTES,
        ),
    ),
    Template(
        "leave_licence",
        CONTRACTS,
        "Leave and licence agreement",
        "Residential or commercial premises given on licence.",
        "a leave and licence agreement for premises. Cover: parties, description of the premises with "
        "fixtures in a schedule, grant of licence only (no tenancy or interest in the premises is created), "
        "period and lock-in, licence fee and due date, escalation, security deposit and its refund, "
        "maintenance, society charges, utilities and taxes, permitted use and occupants, no subletting, "
        "repairs, the licensor's right of inspection, termination and notice, handing back vacant "
        "possession, police verification, registration and stamp duty, notices, governing law and "
        "jurisdiction. Where the premises are in Maharashtra, provide for registration as required by "
        "Section 55 of the Maharashtra Rent Control Act, 1999. " + _EXECUTION,
        (
            _party("licensor", "Licensor (owner)"),
            _party("licensee", "Licensee"),
            Field("premises", "Premises", "Full address, area, furnished or not", True, long=True),
            Field("fee", "Licence fee and deposit", "Monthly fee, due date, deposit, escalation"),
            Field("period", "Period and lock-in", "e.g. 11 months from 1 December 2026, 6 months lock-in"),
            Field("use", "Permitted use", "Residential for family / office use"),
        ),
    ),
    Template(
        "mou",
        CONTRACTS,
        "Memorandum of understanding",
        "Record of the parties' understanding before a definitive agreement.",
        "a memorandum of understanding. State clearly which clauses are binding (confidentiality, "
        "exclusivity if any, costs, governing law) and that the rest records intent only. Cover: background, "
        "purpose, roles and contributions of each party, timeline and next steps towards a definitive "
        "agreement, exclusivity, confidentiality, costs, term and termination, governing law and "
        "jurisdiction. " + _EXECUTION,
        (
            _party("party_a", "First party"),
            _party("party_b", "Second party"),
            Field(
                "purpose",
                "What the parties intend",
                "The proposed transaction or collaboration",
                True,
                long=True,
            ),
            Field("binding", "Binding clauses and term", "Which clauses bind; how long the MoU lasts"),
            JURISDICTION,
        ),
    ),
    Template(
        "partnership_deed",
        CONTRACTS,
        "Partnership deed",
        "Formation of a partnership firm under the Indian Partnership Act, 1932.",
        "a partnership deed under the Indian Partnership Act, 1932. Cover: name and principal place of "
        "business, nature of business, commencement and duration (at will or fixed), capital contributions, "
        "profit and loss sharing ratio, interest on capital and remuneration to working partners within "
        "the limits the income-tax law allows for deduction, bank accounts and operation, books of "
        "account, duties and restrictions on partners, admission, retirement and death of a partner, "
        "dissolution and settlement of accounts, arbitration, and registration with the Registrar of Firms. "
        + _EXECUTION,
        (
            Field("partners", "Partners", "Names, addresses, ages of each partner", True, long=True),
            Field("firm", "Firm name and place of business", required=True),
            Field("business", "Nature of business"),
            Field("capital", "Capital and profit sharing", "Contribution of each; profit/loss ratio"),
            Field("start", "Commencement date"),
        ),
    ),
    Template(
        "loan_agreement",
        CONTRACTS,
        "Loan agreement",
        "Loan between individuals or companies, with repayment terms.",
        "a loan agreement. Cover: parties, amount and disbursement, purpose, interest (or interest-free), "
        "repayment schedule, prepayment, default and its consequences, security or post-dated cheques if "
        "any, representations, costs and stamp duty, notices, governing law and jurisdiction. Where the "
        "borrower is a company, note the need to comply with the Companies Act, 2013 on accepting loans and "
        "deposits. " + _EXECUTION,
        (
            _party("lender", "Lender"),
            _party("borrower", "Borrower"),
            Field("amount", "Loan amount and interest", "Amount, interest rate, how disbursed", True),
            Field("repayment", "Repayment", "Instalments, dates, final repayment date"),
            Field("security", "Security", "Guarantee, cheques, pledge, or none"),
            JURISDICTION,
        ),
    ),
    Template(
        "notice_recovery",
        NOTICES,
        "Legal notice: recovery of money",
        "Demand for payment of an outstanding amount (invoices, loan, deposit).",
        "a legal notice for recovery of money, sent by an advocate on behalf of our client. "
        + _NOTICE_FORM
        + "Include: the facts in numbered paragraphs, the amount due with interest, the "
        "demand to pay within a stated number of days, the consequence (civil suit for recovery, or "
        "proceedings under the Insolvency and Bankruptcy Code, 2016 where it applies), a costs demand, and "
        "the advocate's signature block. Keep the tone firm and formal.",
        (
            _party("client", "Our client (sender)"),
            _party("addressee", "Addressee (to whom)"),
            FACTS,
            Field("amount", "Amount due", "Principal, interest rate and from when", True),
            Field("days", "Time to comply", "e.g. 15 days from receipt"),
            Field("advocate", "Advocate", "Name, enrolment no., address, contact"),
        ),
    ),
    Template(
        "notice_138",
        NOTICES,
        "Cheque bounce notice (Section 138, NI Act)",
        "Statutory demand notice after a cheque is dishonoured.",
        "a statutory demand notice under Section 138 of the Negotiable Instruments Act, 1881, sent by an "
        "advocate on behalf of the payee. It must: identify the cheque (number, date, amount, drawee bank and "
        "branch), the legally enforceable debt or liability for which it was issued, presentation and the "
        "date and reason of dishonour as stated in the return memo, the date the payee received that "
        "information, and demand payment of the cheque amount within 15 days of receipt of the notice, "
        "failing which a complaint under Sections 138 and 142 will be filed. Head it LEGAL NOTICE UNDER "
        "SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881. The notice must be sent within 30 days of the payee receiving information of dishonour; if the dates given show that period has "
        "passed or is close, say so in the drafting notes with the last date. Demand the cheque amount "
        "separately from any costs. " + _NOTICE_FORM,
        (
            _party("payee", "Payee (our client)"),
            _party("drawer", "Drawer (who issued the cheque)"),
            Field("cheque", "Cheque details", "Number, date, amount, bank and branch", True),
            Field("liability", "Debt or liability", "Why the cheque was given", True, long=True),
            Field("dishonour", "Dishonour", "Date presented, date returned, reason on the memo", True),
            Field("informed", "Date the return memo was received", "Starts the 30-day period"),
            Field("advocate", "Advocate", "Name, enrolment no., address, contact"),
        ),
    ),
    Template(
        "notice_general",
        NOTICES,
        "Legal notice: general",
        "Any grievance: breach of contract, eviction, defamation, consumer, employment, property.",
        "a legal notice sent by an advocate on behalf of our client. "
        + _NOTICE_FORM
        + "Include: the facts in numbered paragraphs, the legal basis "
        "of the claim (name the statute and section only where you are sure), what the addressee must do "
        "and by when, the consequence of failing to comply, a costs demand, a reservation of rights, and "
        "the advocate's signature block.",
        (
            _party("client", "Our client (sender)"),
            _party("addressee", "Addressee (to whom)"),
            Field("subject", "Subject", "e.g. Breach of the supply agreement dated 3 March 2026", True),
            FACTS,
            Field("demand", "What we demand", "Pay, vacate, retract, perform, stop...", True),
            Field("days", "Time to comply", "e.g. 15 days from receipt"),
            Field("advocate", "Advocate", "Name, enrolment no., address, contact"),
        ),
    ),
    Template(
        "notice_reply",
        NOTICES,
        "Reply to a legal notice",
        "Point-by-point reply to a notice our client received.",
        "a reply to a legal notice, sent by an advocate on behalf of our client. Refer to the notice (date, "
        "sender, advocate). Deny the allegations that are disputed, paragraph by paragraph, admitting only "
        "what our client admits. Set out our client's version of the facts, the legal position, any "
        "counter-claim or demand, and state that all other allegations are denied as if specifically "
        "traversed. Reserve all rights. " + _NOTICE_FORM,
        (
            _party("client", "Our client (who received the notice)"),
            Field(
                "notice",
                "The notice received",
                "Date, sender, their advocate, what it alleges and demands",
                True,
                long=True,
            ),
            Field(
                "version",
                "Our client's version",
                "What actually happened; what is admitted or denied",
                True,
                long=True,
            ),
            Field("counter", "Counter-demand", "Anything we demand in return (optional)"),
            Field("advocate", "Advocate", "Name, enrolment no., address, contact"),
        ),
    ),
    Template(
        "affidavit",
        DEEDS,
        "Affidavit",
        "Sworn statement for a court, authority, bank or other purpose.",
        "an affidavit. Include the court or authority heading if given, the deponent's full description "
        '("I, [name], son/daughter/wife of [name], aged about [age] years, residing at [address], do hereby '
        'solemnly affirm and state as under:"), the statements in numbered paragraphs, the deponent '
        "signature, and a verification clause stating which paragraphs are true to personal knowledge and "
        "which are based on records or information believed to be true, with place and date. Add a line for "
        "attestation by a notary or oath commissioner. In the drafting notes, remind the lawyer that an "
        "affidavit is sworn on non-judicial stamp paper of the value fixed by the State's Stamp Act.",
        (
            Field(
                "deponent", "Deponent", "Name, parent/spouse name, age, address, occupation", True, long=True
            ),
            Field(
                "purpose",
                "Purpose or court",
                "e.g. Name change; or In the High Court of Delhi, W.P.(C) No. ...",
                True,
            ),
            Field("statements", "Statements to affirm", "Each fact the deponent states", True, long=True),
            Field("place", "Place of swearing"),
        ),
    ),
    Template(
        "power_of_attorney",
        DEEDS,
        "Power of attorney",
        "General or special power of attorney to act on someone's behalf.",
        "a power of attorney (general or special as stated). Cover: the principal's description, the "
        "attorney's description, recitals of why it is given, the specific powers in numbered clauses "
        "(limited to what the lawyer lists for a special power), ratification of acts done under it, "
        "whether it is revocable, and its duration. End with the principal's signature, two witnesses and a "
        "line for notarisation; note that a power of attorney concerning immovable property generally needs "
        "registration and stamp duty under the applicable law.",
        (
            Field("kind", "General or special", "General / special (for what)"),
            _party("principal", "Principal (who gives the power)"),
            _party("attorney", "Attorney (who receives the power)"),
            Field(
                "powers",
                "Powers",
                "What the attorney may do: property, bank, litigation, sale...",
                True,
                long=True,
            ),
            Field("duration", "Duration and revocability"),
        ),
    ),
    Template(
        "legal_opinion",
        ADVICE,
        "Legal opinion",
        "Written opinion or memo on a legal question.",
        "a legal opinion. Structure it as: addressee and date, the question(s) asked, the facts relied on "
        "and assumptions made, the applicable law (statutes by name and section where you are sure; no case "
        "law unless supplied), analysis of each question, conclusions, recommended next steps, and "
        "qualifications (based only on the facts given and Indian law as of the date of the opinion).",
        (
            Field("to", "Addressed to", "Client or partner"),
            Field("question", "Question", "What we are asked to advise on", True, long=True),
            FACTS,
            Field(
                "view", "Our preliminary view (optional)", "Any position the opinion should take", long=True
            ),
        ),
    ),
    Template(
        "letter",
        ADVICE,
        "Letter or e-mail",
        "Client update, letter to the other side, authority, bank or regulator.",
        "a professional letter or e-mail from our firm. Include the date, addressee, a subject line, a "
        "clear opening stating the purpose, the body in short paragraphs, any request or next step with a "
        "date, and a sign-off. Write it as an e-mail without a postal address block if the lawyer asks "
        "for an e-mail.",
        (
            Field("to", "To", "Name, designation, organisation", True),
            Field("from", "From", "Sender and firm"),
            Field("subject", "Subject", required=True),
            Field("points", "What it must say", "Key points, in any order", True, long=True),
            Field("tone", "Tone", "e.g. formal, firm, friendly, without prejudice"),
        ),
    ),
    Template(
        "custom",
        OTHER,
        "Any other document",
        "Describe the document you need: deed, undertaking, resolution, indemnity, petition outline...",
        "the document described in the facts and instructions, in the form customary for it in Indian "
        "practice, complete from title to signature or execution block.",
        (
            Field("document", "Document type", "e.g. Board resolution, indemnity bond, gift deed", True),
            Field("parties", "Parties", "Who is involved", long=True),
            Field("details", "Details", "Facts, terms and anything it must include", True, long=True),
        ),
    ),
)

BY_KEY = {t.key: t for t in TEMPLATES}
