"""The report must be well-formed HTML (this would have caught a pasted-in-the-wrong-place edit)."""
import datetime as dt
from html.parser import HTMLParser

from app.html_report import Site, build_daily_html
from tests.test_trips import DAY, day_with_stop

VOID = {"meta", "br", "img", "line", "circle", "polyline", "polygon", "path", "input", "hr", "link"}


class Check(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)
        for k, v in attrs:                      # a tag opened inside another tag: <div class=<div ...>
            if v and "<" in v and k != "style":
                self.errors.append(f"bad attribute {k}={v[:30]}")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"unexpected </{tag}> after {self.stack[-3:]}")


def test_report_html_is_well_formed_and_has_no_stray_text():
    s, f = day_with_stop()
    site = Site(client="X", address="Y", plant_id="P", serial="S", kwp=5, psh=4.5, tariff=7.0)
    hist = {DAY - dt.timedelta(days=i): v for i, v in zip((3, 2, 1), (18.0, 20.0, 22.0))}
    h = build_daily_html(s, DAY, site, faults=f, history=hist)
    c = Check(); c.feed(h)
    assert not c.errors, c.errors[:3]
    assert c.stack == [], c.stack[:5]
    assert 'repeat(6,minmax(0,1fr))">' not in h.split("<body", 1)[1].split("<div class=\"card\"")[0]
    assert h.count('class="savings-tile') == 5 and "Average per day:" in h