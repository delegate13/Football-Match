"""Convert normalized match reports into downloadable CSV and PDF documents."""

import csv
import io


def report_csv(report: dict) -> bytes:
    """Convert report matches into UTF-8 CSV bytes suitable for spreadsheet apps."""
    # StringIO lets csv.writer handle commas, quotes, and line breaks in names.
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "Date", "Competition", "Home team", "Home score", "Away team", "Away score",
        "Status", "Status detail", "Venue", "Match ID",
    ))
    for match in report["matches"]:
        writer.writerow((
            match.get("date", ""),
            match.get("league_name", ""),
            match.get("home_team", ""),
            match.get("home_score"),
            match.get("away_team", ""),
            match.get("away_score"),
            match.get("status", ""),
            match.get("status_detail", ""),
            match.get("venue", ""),
            match.get("id", ""),
        ))
    return output.getvalue().encode("utf-8-sig")


def report_pdf(report: dict) -> bytes:
    """Build a small printable PDF using only the Python standard library."""
    matches = report["matches"]
    report_date = report.get("date_from") or report.get("date_to") or "All dates"
    lines = [
        "Touchline - Football Match Report",
        f"Date: {report_date}    Matches: {len(matches)}",
        f"Generated: {report.get('generated_at') or 'Unknown'}",
        "",
    ]
    for match in matches:
        home_score = match.get("home_score")
        away_score = match.get("away_score")
        score = f"{home_score if home_score is not None else '-'} - {away_score if away_score is not None else '-'}"
        lines.extend((
            f"{match.get('league_name', 'Competition')} | {str(match.get('date', ''))[:16]}",
            f"{match.get('home_team', 'Home')}  {score}  {match.get('away_team', 'Away')}",
            f"{match.get('status_detail') or match.get('status', '')} | {match.get('venue') or 'Venue unavailable'}",
            "",
        ))
    if not matches:
        lines.append("No matches found for this report.")

    # PDF text is split into pages so large reports do not run off the page.
    encoded_lines = [_pdf_text(line) for line in lines]
    pages = [encoded_lines[index:index + 48] for index in range(0, len(encoded_lines), 48)]
    # A PDF is a list of numbered objects linked by a cross-reference table.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{4 + index * 2} 0 R".encode("ascii") for index in range(len(pages)))
        + f"] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for index, page_lines in enumerate(pages):
        # Each page points to one text stream and shares a standard font object.
        page_id = 4 + index * 2
        content_id = page_id + 1
        commands = ["BT", "/F1 9 Tf", "40 750 Td", "12 TL"]
        for line in page_lines:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("cp1252")
        objects.extend((
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii"),
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"\nendstream",
        ))

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    # Record byte positions as objects are written; PDF readers use them to find objects.
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _pdf_text(value: str) -> str:
    """Flatten line breaks and replace unsupported symbols for the built-in font."""
    cleaned = " ".join(str(value).split())
    return cleaned.encode("cp1252", errors="replace").decode("cp1252")


def _pdf_escape(value: str) -> str:
    """Escape characters that have special meaning inside PDF text parentheses."""
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
