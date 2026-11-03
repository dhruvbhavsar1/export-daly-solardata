"""Email the daily report (HTML + PDF attached) through any SMTP server (e.g. Gmail app password)."""
import os, smtplib, sys, json, datetime as dt
from email.message import EmailMessage
from pathlib import Path

def main(html_path: str, pdf_path: str | None) -> int:
    g = os.environ
    day = Path(html_path).stem.replace("solar_report_", "")
    client = g.get("CLIENT_NAME", "your plant")
    rows = [json.loads(l) for l in (Path("data/raw") / f"{day}.jsonl").read_text().splitlines()] if (Path("data/raw") / f"{day}.jsonl").exists() else []
    kwh = max((r["e_kwh"] for r in rows), default=None); peak = max((r["p_kw"] for r in rows), default=None)
    headline = "No data was received today." if kwh is None else f"{kwh:.1f} kWh generated · peak {peak:.2f} kW"
    msg = EmailMessage()
    msg["Subject"] = f"Solar report {day} · {client} · " + headline
    msg["From"] = g["MAIL_FROM"]; msg["To"] = g["MAIL_TO"]
    msg.set_content(f"Solar daily report for {client}, {day}\n{headline}\n\nOpen the attached HTML file in a browser for the interactive chart. The PDF is a printable copy.")
    msg.add_alternative(f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;background:#F5F7F3;padding:24px;color:#18372F">
<div style="font-size:22px;font-weight:800">☀ SOLAR<span style="color:#C88D00">PULSE</span></div>
<div style="color:#61776E;font-size:12px;letter-spacing:1.5px">DAILY REPORT · {day}</div>
<div style="background:#fff;border:1px solid #DCE7DF;border-radius:14px;padding:20px;margin-top:16px">
<div style="color:#61776E;font-size:13px">{client}</div>
<div style="font-size:30px;font-weight:800;color:#BA8000;margin:6px 0">{'—' if kwh is None else f'{kwh:.1f} kWh'}</div>
<div style="font-size:14px">{headline}</div></div>
<p style="font-size:13px;color:#61776E">Open the attached <b>HTML</b> file in your browser for the interactive power curve. The <b>PDF</b> is a printable copy.</p></div>""", subtype="html")
    for p, mt in ((html_path, ("text", "html")), (pdf_path, ("application", "pdf"))):
        if p and Path(p).exists():
            msg.add_attachment(Path(p).read_bytes(), maintype=mt[0], subtype=mt[1], filename=Path(p).name)
    with smtplib.SMTP(g["SMTP_HOST"], int(g.get("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls(); s.login(g["SMTP_USER"], g["SMTP_PASS"]); s.send_message(msg)
    print("email sent to", g["MAIL_TO"]); return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
