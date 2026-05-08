from legal_agent.agents.drafts.cause_title import (
    CauseTitleData,
    LEADING_MARKER,
    Party,
    SENTINEL_END,
    SENTINEL_START,
    prepend_cause_title_to_draft,
    render_cause_title_html,
)


def _pune_stay_app_data() -> CauseTitleData:
    return CauseTitleData(
        court_name="SMALL CAUSES COURT, PUNE",
        court_seat="PUNE",
        case_type="Civil Suit",
        case_number="______",
        case_year="2022",
        document_title="Stay Application On Behalf of the Plaintiff",
        parties=[
            Party(
                full_name="Jagdish Panditrao Choudhari",
                age="50 yrs",
                occupation="Manager Mechanical, Raymonds Ltd. (Denim Division)",
                address_intro="R/o",
                address_lines=[
                    "41, Mahaveer Nagar, Part 2, Darwha Road",
                    "Yavatmal, Maharashtra - 445001",
                ],
                mobile="9373188011",
                role="Plaintiff",
            ),
            Party(
                full_name="Omprakash Keshavrao Devgavdkar",
                age="50 yrs",
                occupation="Business",
                address_intro="R/o",
                address_lines=[
                    "Gate no. 274, Lotus Nandanvan Apartment, Flat no. 426, B.no. 01, Moshi",
                    "District Pune - 412105",
                ],
                mobile="9769205996",
                role="Defendant",
            ),
        ],
    )


def test_pune_stay_application_renders_canonical_html():
    html = render_cause_title_html(_pune_stay_app_data())

    assert html.startswith(SENTINEL_START)
    assert html.endswith(SENTINEL_END)
    assert (
        '<p style="text-align:center;"><strong><u>'
        "IN THE HON'BLE SMALL CAUSES COURT, PUNE</u></strong></p>"
    ) in html
    assert (
        '<p style="text-align:center;"><strong><u>AT PUNE</u></strong></p>'
    ) in html
    assert (
        '<p style="text-align:right;"><strong>Civil Suit No. ______ / 2022</strong></p>'
    ) in html
    assert "<p><strong>Jagdish Panditrao Choudhari</strong></p>" in html
    assert "<p><strong>Omprakash Keshavrao Devgavdkar</strong></p>" in html
    # Mob+role rendered as a 1-row borderless 2-cell table so role tags sit
    # on the SAME line as the mobile, right-aligned.
    assert 'class="cause-title-row"' in html
    assert "Mob.no. 9373188011" in html
    assert "Mob.no. 9769205996" in html
    assert "<strong>Plaintiff</strong>" in html
    assert "<strong>Defendant</strong>" in html
    assert (
        '<p style="text-align:center;"><em><strong>Vs.</strong></em></p>' in html
    )
    assert (
        '<p style="text-align:center;"><strong><u>'
        "Stay Application On Behalf of the Plaintiff</u></strong></p>"
    ) in html
    assert html.index("Plaintiff") < html.index("Vs.") < html.index("Defendant")
    assert "float:right" not in html


def test_multiple_respondents_get_ordinal_labels():
    data = CauseTitleData(
        court_name="HIGH COURT OF BOMBAY",
        court_seat="MUMBAI",
        case_type="Writ Petition",
        case_number="123",
        case_year="2024",
        document_title="Application For Stay Pending Disposal",
        parties=[
            Party(full_name="Petitioner Inc.", role="Petitioner"),
            Party(full_name="State of Maharashtra", role="Respondent", ordinal=1),
            Party(full_name="Municipal Commissioner", role="Respondent", ordinal=2),
            Party(full_name="ACME Pvt. Ltd.", role="Respondent", ordinal=3),
        ],
    )
    html = render_cause_title_html(data)

    assert "<p><strong>Respondent No. 1</strong></p>" in html
    assert "<p><strong>Respondent No. 2</strong></p>" in html
    assert "<p><strong>Respondent No. 3</strong></p>" in html
    assert "<strong>State of Maharashtra</strong>" in html
    assert "<strong>ACME Pvt. Ltd.</strong>" in html
    assert html.count('<em><strong>Vs.</strong></em>') == 1
    assert html.index("Petitioner Inc.") < html.index("Vs.")
    assert html.index("Vs.") < html.index("State of Maharashtra")


def test_corporate_party_uses_office_intro_and_description():
    data = CauseTitleData(
        court_name="DISTRICT COURT, PUNE",
        court_seat="PUNE",
        case_type="Civil Suit",
        case_number="55",
        case_year="2025",
        document_title="Stay Application",
        parties=[
            Party(
                full_name="ACME Industries Pvt. Ltd.",
                honorific="M/s",
                description="a company incorporated under the Companies Act, 2013",
                address_intro="Having its office at",
                address_lines=["Plot 12, MIDC, Hinjewadi, Pune - 411057"],
                role="Plaintiff",
            ),
            Party(
                full_name="Sample Defendant",
                age="45 yrs",
                occupation="Service",
                address_lines=["Some address"],
                role="Defendant",
            ),
        ],
    )
    html = render_cause_title_html(data)

    assert "<strong>M/s ACME Industries Pvt. Ltd.</strong>" in html
    assert (
        "<p>a company incorporated under the Companies Act, 2013</p>" in html
    )
    assert (
        "<p>Having its office at: Plot 12, MIDC, Hinjewadi, Pune - 411057</p>"
        in html
    )


def test_missing_mobile_renders_role_on_own_right_aligned_line():
    data = CauseTitleData(
        court_name="SMALL CAUSES COURT, PUNE",
        court_seat="PUNE",
        case_type="Civil Suit",
        case_number="1",
        case_year="2025",
        document_title="Stay Application",
        parties=[
            Party(
                full_name="A Plaintiff",
                age="40 yrs",
                occupation="Service",
                address_lines=["Pune"],
                mobile=None,
                role="Plaintiff",
            ),
            Party(
                full_name="A Defendant",
                age="40 yrs",
                occupation="Service",
                address_lines=["Pune"],
                mobile=None,
                role="Defendant",
            ),
        ],
    )
    html = render_cause_title_html(data)

    # No mobile → left cell is empty; role still renders on its right cell.
    assert "Mob.no." not in html
    assert 'class="cause-title-row"' in html
    assert "<strong>Plaintiff</strong>" in html
    assert "<strong>Defendant</strong>" in html


def test_missing_age_and_occupation_omits_line():
    data = CauseTitleData(
        court_name="X",
        court_seat="Y",
        case_type="Civil Suit",
        case_number="1",
        case_year="2025",
        document_title="App",
        parties=[
            Party(
                full_name="No Age Party",
                age=None,
                occupation=None,
                address_lines=["addr"],
                mobile="9999999999",
                role="Plaintiff",
            ),
            Party(full_name="Other", role="Defendant"),
        ],
    )
    html = render_cause_title_html(data)

    assert "Age:" not in html.split("Vs.")[0]
    assert "Occ:" not in html.split("Vs.")[0]


def test_all_fields_missing_emits_named_brackets():
    data = CauseTitleData()
    html = render_cause_title_html(data)

    assert "[Court Name]" in html
    assert "[Court Location]" in html
    assert "[Year]" in html
    assert "[Document Title]" in html
    assert "[Plaintiff Full Name]" in html
    assert "[Defendant Full Name]" in html
    assert "______" in html
    assert html.startswith(SENTINEL_START)
    assert html.endswith(SENTINEL_END)


def test_prepend_strips_leading_llm_cause_title_h2():
    data = _pune_stay_app_data()
    body = (
        "## CAUSE TITLE\n\nIN THE HON'BLE SMALL CAUSES COURT...\n"
        "**Vs.**\n\n"
        "## Stay Application On Behalf of the Plaintiff\n\n"
        "Some bad LLM-emitted preamble.\n\n"
        "## 1. BRIEF FACTS\n\n"
        "1.1 Real body content here that should survive."
    )
    out = prepend_cause_title_to_draft(body, data)

    assert out.startswith(LEADING_MARKER)
    assert SENTINEL_START in out
    body_after_sentinel = out.split(SENTINEL_END, 1)[1]
    assert "## CAUSE TITLE" not in body_after_sentinel
    assert "## Stay Application On Behalf of the Plaintiff" not in body_after_sentinel
    assert "Some bad LLM-emitted preamble." not in body_after_sentinel
    assert "## 1. BRIEF FACTS" in body_after_sentinel
    assert "1.1 Real body content here that should survive." in body_after_sentinel


def test_prepend_is_idempotent_when_body_already_starts_with_sentinel():
    data = _pune_stay_app_data()
    rendered = render_cause_title_html(data)
    body = rendered + "\n\n## 1. BRIEF FACTS\n\nbody"

    out = prepend_cause_title_to_draft(body, data)

    assert out == body
    assert out.count(SENTINEL_START) == 1


def test_prepend_preserves_clean_body_with_no_leading_cause_title():
    data = _pune_stay_app_data()
    body = "## 1. BRIEF FACTS\n\n1.1 The plaintiff submits…"

    out = prepend_cause_title_to_draft(body, data)

    assert out.startswith(LEADING_MARKER)
    assert SENTINEL_START in out
    assert out.endswith("1.1 The plaintiff submits…")
    assert SENTINEL_END in out
    assert out.index(SENTINEL_END) < out.index("## 1. BRIEF FACTS")


def test_prepend_output_first_char_is_not_angle_bracket():
    data = _pune_stay_app_data()
    body = "## 1. BRIEF FACTS\n\n1.1 The plaintiff submits…"

    out = prepend_cause_title_to_draft(body, data)

    assert out.lstrip()[0] != "<"
    assert out.lstrip().startswith("[")


def test_prepend_idempotent_when_body_already_has_marker():
    data = _pune_stay_app_data()
    once = prepend_cause_title_to_draft(
        "## 1. BRIEF FACTS\n\n1.1 The plaintiff submits…", data
    )

    twice = prepend_cause_title_to_draft(once, data)

    assert twice == once
    assert twice.count(LEADING_MARKER) == 1
    assert twice.count(SENTINEL_START) == 1


def test_postprocess_skips_placeholders_inside_sentinel_span():
    from legal_agent.utils.legal_postprocess import detect_placeholders

    body = (
        f"{SENTINEL_START}\n"
        '<p style="text-align:center;margin:0;"><strong><u>IN THE HON\'BLE [Court Name]</u></strong></p>\n'
        '<p style="text-align:right;margin:0;"><strong>Civil Suit No. ______ / [Year]</strong></p>\n'
        f"{SENTINEL_END}\n\n"
        "## 1. BRIEF FACTS\n\n1.1 Real body content."
    )
    placeholders = detect_placeholders(body)

    assert placeholders == []


def test_postprocess_still_flags_placeholders_outside_sentinel_span():
    from legal_agent.utils.legal_postprocess import detect_placeholders

    body = (
        f"{SENTINEL_START}\n[Court Name]\n{SENTINEL_END}\n\n"
        "## 1. BRIEF FACTS\n\n1.1 The amount is [Loan Amount] payable on [Due Date]."
    )
    placeholders = detect_placeholders(body)

    assert "[Loan Amount]" in placeholders
    assert "[Due Date]" in placeholders
    assert "[Court Name]" not in placeholders
