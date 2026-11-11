#!/usr/bin/env python3
"""Build the dev-test report (.xlsx, .csv, summary.md) from results.json.

Standard library only. Usage:
    python3 build_report.py <results.json> [--out <dir>] [--check]

--check validates without writing anything. Exit code 1 on validation errors.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape

STATUSES = ["PASS", "FAIL", "BLOCKED", "NOT RUN"]
SEVERITIES = ["Blocker", "Major", "Minor"]
PRIORITIES = ["P0", "P1", "P2", "P3"]
VERDICTS = ["READY", "READY WITH RISKS", "NOT READY"]


# ---------------------------------------------------------------- validation

def as_list(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def as_text(value):
    if isinstance(value, list):
        if all(not isinstance(v, (list, dict)) for v in value):
            return "\n".join(f"{i}. {v}" for i, v in enumerate(value, 1)) if len(value) > 1 else "".join(map(str, value))
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def ac_coverage(data):
    """AC id -> (case ids, overall status)."""
    rows = {}
    for ac in data.get("acceptance_criteria", []):
        cases = [tc for tc in data.get("test_cases", []) if ac["id"] in as_list(tc.get("ac"))]
        statuses = {tc.get("status") for tc in cases}
        if not cases:
            overall = "NOT COVERED"
        elif "FAIL" in statuses:
            overall = "FAIL"
        elif "PASS" not in statuses:
            overall = "NOT VERIFIED"
        elif statuses - {"PASS"}:
            overall = "PARTIAL"
        else:
            overall = "PASS"
        rows[ac["id"]] = ([tc["id"] for tc in cases], overall)
    return rows


def lowest_allowed_verdict(data):
    """The best verdict the evidence allows, and why."""
    cases = data.get("test_cases", [])
    defects = data.get("defects", [])
    reasons = []
    for d in defects:
        if d.get("severity") == "Blocker":
            reasons.append(f"Blocker defect {d.get('id')}")
    for tc in cases:
        if tc.get("priority") == "P0" and tc.get("status") == "FAIL":
            reasons.append(f"P0 case {tc.get('id')} failed")
    for ac_id, (_, overall) in ac_coverage(data).items():
        if overall in ("NOT COVERED", "NOT VERIFIED"):
            reasons.append(f"{ac_id} has no passing case ({overall.lower()})")
    if reasons:
        return "NOT READY", reasons

    for d in defects:
        reasons.append(f"{d.get('severity')} defect {d.get('id')}")
    for tc in cases:
        if tc.get("priority") in ("P0", "P1") and tc.get("status") in ("BLOCKED", "NOT RUN"):
            reasons.append(f"{tc.get('priority')} case {tc.get('id')} {tc.get('status').lower()}")
    for r in data.get("compat_risks", []):
        if r.get("result") != "PASS":
            reasons.append(f"compat risk {r.get('id')} {str(r.get('result') or 'unverified').lower()}")
    for ac_id, (_, overall) in ac_coverage(data).items():
        if overall in ("FAIL", "PARTIAL"):
            reasons.append(f"{ac_id} {overall.lower()}")
    if reasons:
        return "READY WITH RISKS", reasons
    return "READY", []


def validate(data):
    errors, warnings = [], []
    for key in ("ticket", "test_cases"):
        if key not in data:
            errors.append(f"missing top-level '{key}'")
    if errors:
        return errors, warnings
    if not data["ticket"].get("key"):
        errors.append("ticket.key is required")

    ac_ids = {ac.get("id") for ac in data.get("acceptance_criteria", [])}
    seen = set()
    for tc in data["test_cases"]:
        tid = tc.get("id") or "<no id>"
        if tid in seen:
            errors.append(f"{tid}: duplicate test case id")
        seen.add(tid)
        status = tc.get("status")
        if status not in STATUSES:
            errors.append(f"{tid}: status '{status}' must be one of {STATUSES}")
        if status == "PASS" and not as_list(tc.get("evidence")):
            errors.append(f"{tid}: PASS without evidence")
        if status in ("PASS", "FAIL") and not as_text(tc.get("actual")).strip():
            errors.append(f"{tid}: {status} without an actual result")
        if status in ("BLOCKED", "NOT RUN") and not as_text(tc.get("notes")).strip():
            errors.append(f"{tid}: {status} needs a reason in notes")
        if tc.get("priority") and tc["priority"] not in PRIORITIES:
            errors.append(f"{tid}: priority '{tc['priority']}' must be one of {PRIORITIES}")
        for ac in as_list(tc.get("ac")):
            if ac not in ac_ids:
                errors.append(f"{tid}: refers to unknown {ac}")
        if status == "FAIL" and not any(d.get("tc") == tid for d in data.get("defects", [])):
            warnings.append(f"{tid}: FAIL with no defect recorded")

    for d in data.get("defects", []):
        if d.get("severity") not in SEVERITIES:
            errors.append(f"{d.get('id')}: severity '{d.get('severity')}' must be one of {SEVERITIES}")
        if d.get("tc") and d["tc"] not in seen:
            errors.append(f"{d.get('id')}: refers to unknown {d['tc']}")

    for ac_id, (_, overall) in ac_coverage(data).items():
        if overall == "NOT COVERED":
            warnings.append(f"{ac_id}: no test case covers it")

    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        errors.append(f"verdict '{verdict}' must be one of {VERDICTS}")
    else:
        allowed, reasons = lowest_allowed_verdict(data)
        if VERDICTS.index(verdict) < VERDICTS.index(allowed):
            errors.append(f"verdict '{verdict}' is better than the results allow ('{allowed}'): " + "; ".join(reasons))
    return errors, warnings


# ---------------------------------------------------------------- xlsx writer

STYLE = {"default": 0, "header": 1, "wrap": 2, "PASS": 3, "FAIL": 4, "BLOCKED": 5, "NOT RUN": 6, "label": 7}
STATUS_STYLE_WORDS = {
    "PASS": "PASS", "READY": "PASS",
    "FAIL": "FAIL", "NOT READY": "FAIL", "NOT COVERED": "FAIL", "Blocker": "FAIL",
    "BLOCKED": "BLOCKED", "READY WITH RISKS": "BLOCKED", "PARTIAL": "BLOCKED", "Major": "BLOCKED",
    "NOT RUN": "NOT RUN", "NOT VERIFIED": "NOT RUN", "Minor": "NOT RUN",
}

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFEB9C"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom></border></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="8">
<xf/>
<xf fontId="2" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf borderId="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf fontId="1" fillId="3" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf fontId="1" fillId="4" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf fontId="1" fillId="5" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf fontId="1" fillId="6" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf fontId="1" borderId="1" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell_xml(ref, value, style):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(ILLEGAL_XML.sub("", as_text(value)))[:32000]
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def sheet_xml(rows, widths, status_cols=(), header=True):
    """rows: list of lists. status_cols: 0-based column indexes coloured by value."""
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if header:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    out.append("<cols>" + "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + "</cols>")
    out.append("<sheetData>")
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            if header and r == 1:
                style = STYLE["header"]
            elif c in status_cols and as_text(value) in STATUS_STYLE_WORDS:
                style = STYLE[STATUS_STYLE_WORDS[as_text(value)]]
            elif not header and c == 0:
                style = STYLE["label"]
            else:
                style = STYLE["wrap"]
            cells.append(cell_xml(f"{col_letter(c + 1)}{r}", value, style))
        out.append(f'<row r="{r}">' + "".join(cells) + "</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(path, sheets):
    """sheets: list of (name, xml)."""
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    ct += [f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
           for i in range(1, len(sheets) + 1)]
    ct.append("</Types>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, _) in enumerate(sheets, 1))
                + "</sheets></workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, len(sheets) + 1))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               "</Relationships>")
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 "</Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", STYLES_XML)
        for i, (_, xml) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)


# ---------------------------------------------------------------- report

TC_COLUMNS = [("ID", "id", 9), ("AC", "ac", 10), ("Category", "category", 16), ("Priority", "priority", 8),
              ("Method", "method", 9), ("Title", "title", 32), ("Preconditions", "preconditions", 26),
              ("Test data", "test_data", 20), ("Steps", "steps", 40), ("Expected", "expected", 32),
              ("Actual", "actual", 32), ("Status", "status", 11), ("Evidence", "evidence", 28), ("Notes", "notes", 28)]


def counts(cases, key):
    out = {}
    for tc in cases:
        out[tc.get(key) or "-"] = out.get(tc.get(key) or "-", 0) + 1
    return out


def build(data, out_dir):
    ticket, run = data["ticket"], data.get("run", {})
    cases = data["test_cases"]
    date = run.get("date") or datetime.date.today().isoformat()
    by_status = counts(cases, "status")
    coverage = ac_coverage(data)

    summary = [["Field", "Value"],
               ["Ticket", ticket.get("key")], ["Summary", ticket.get("summary")], ["Link", ticket.get("url")],
               ["Repo", run.get("repo")], ["Branch", run.get("branch")], ["Compared with", run.get("base")],
               ["Environment", run.get("environment")], ["Base URL", run.get("base_url")],
               ["Tester", run.get("tester")], ["Date", date],
               ["Verdict", data.get("verdict")], ["Verdict reason", data.get("verdict_reason")],
               ["Total cases", len(cases)]]
    summary += [[s, by_status.get(s, 0)] for s in STATUSES]
    summary += [["Defects", len(data.get("defects", []))]]
    summary += [[f"Category: {k}", v] for k, v in sorted(counts(cases, "category").items())]
    summary += [["Automated: " + as_text(a.get("name")), f"{a.get('result')} - {as_text(a.get('details'))}"]
                for a in data.get("automated_checks", [])]
    if data.get("test_data_created"):
        summary.append(["Test data created", as_text(data["test_data_created"])])

    tc_rows = [[c for c, _, _ in TC_COLUMNS]] + [
        [", ".join(as_list(tc.get(k))) if k in ("ac", "evidence") else tc.get(k) for _, k, _ in TC_COLUMNS]
        for tc in cases]
    defect_rows = [["ID", "Test case", "Severity", "Title", "Steps", "Expected", "Actual", "Suspected location"]] + [
        [d.get("id"), d.get("tc"), d.get("severity"), d.get("title"), d.get("steps"), d.get("expected"),
         d.get("actual"), d.get("suspected_location")] for d in data.get("defects", [])]
    ac_text = {ac["id"]: ac.get("text") for ac in data.get("acceptance_criteria", [])}
    ac_rows = [["AC", "Criterion", "Test cases", "Status"]] + [
        [ac_id, ac_text[ac_id], ", ".join(ids), overall] for ac_id, (ids, overall) in coverage.items()]
    bc_rows = [["ID", "Risk", "Test cases", "Result"]] + [
        [r.get("id"), r.get("risk"), ", ".join(as_list(r.get("tc"))), r.get("result")]
        for r in data.get("compat_risks", [])]

    sheets = [
        ("Summary", sheet_xml(summary, [28, 80], status_cols=(1,))),
        ("Test Cases", sheet_xml(tc_rows, [w for _, _, w in TC_COLUMNS], status_cols=(11,))),
        ("Defects", sheet_xml(defect_rows, [9, 10, 10, 36, 40, 30, 30, 36], status_cols=(2,))),
        ("AC Coverage", sheet_xml(ac_rows, [8, 70, 24, 14], status_cols=(3,))),
        ("Backward Compatibility", sheet_xml(bc_rows, [8, 70, 24, 12], status_cols=(3,))),
    ]
    os.makedirs(out_dir, exist_ok=True)
    xlsx = os.path.join(out_dir, f"{ticket['key']}-dev-test-{date}.xlsx")
    write_xlsx(xlsx, sheets)

    with open(os.path.join(out_dir, "test-cases.csv"), "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows([[as_text(v) for v in row] for row in tc_rows])

    md = [f"## Dev test - {ticket['key']}: {ticket.get('summary', '')}", "",
          f"**Verdict: {data.get('verdict')}**" + (f" - {data['verdict_reason']}" if data.get("verdict_reason") else ""),
          "", f"Branch `{run.get('branch', '-')}` on {run.get('environment', '-')}, {date}.", "",
          "| Status | Cases |", "|---|---|"] + [f"| {s} | {by_status.get(s, 0)} |" for s in STATUSES]
    md += ["", "### Acceptance criteria", "", "| AC | Status | Cases |", "|---|---|---|"]
    md += [f"| {ac_id} {ac_text[ac_id]} | {overall} | {', '.join(ids) or '-'} |" for ac_id, (ids, overall) in coverage.items()]
    if data.get("defects"):
        md += ["", "### Defects", ""] + [f"- **{d.get('severity')}** {d.get('id')} ({d.get('tc')}): {d.get('title')}"
                                         for d in data["defects"]]
    open_cases = [tc for tc in cases if tc.get("status") in ("BLOCKED", "NOT RUN")]
    if open_cases:
        md += ["", "### Not run / blocked", ""] + [f"- {tc['id']} {tc.get('title')}: {as_text(tc.get('notes'))}"
                                                     for tc in open_cases]
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return xlsx


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results")
    p.add_argument("--out", help="output folder (default: folder of results.json)")
    p.add_argument("--check", action="store_true", help="validate only")
    args = p.parse_args()

    with open(args.results, encoding="utf-8") as f:
        data = json.load(f)
    errors, warnings = validate(data)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    allowed, reasons = lowest_allowed_verdict(data)
    if args.check:
        print(f"ok - best verdict the results allow: {allowed}" + (f" ({'; '.join(reasons)})" if reasons else ""))
        return
    out_dir = args.out or os.path.dirname(os.path.abspath(args.results))
    xlsx = build(data, out_dir)
    print(os.path.abspath(xlsx))


if __name__ == "__main__":
    main()
