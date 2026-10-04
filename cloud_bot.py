"""
Hungary Consulate appointment watcher - GitHub Actions version.

Runs ONE check and exits (GitHub's scheduler calls it every ~30 minutes).
All personal details and email settings come from GitHub Secrets, so nothing
private is stored in the code.

Exit codes:
  0 - no appointment (normal), or a technical error (see logs)
  1 - POSSIBLE APPOINTMENT FOUND  -> the run is marked "failed" on purpose, which
      makes GitHub itself email the account owner as a backup alert.
"""

import json
import os
import smtplib
import ssl
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://konzinfobooking.mfa.gov.hu/"
CONSULATE_LABEL = "Israel - Tel Aviv"
CASE_TYPE_LABEL = "Citizenship applications"
NO_APPOINTMENT_TEXT = "We inform you that there are currently no appointments available"

SLOW_MO_MS = 50
SCREENSHOT_PATH = "possible_slot.png"

# ----- Settings from GitHub Secrets (environment variables) -----
DETAILS = json.loads(os.environ["FORM_DETAILS_JSON"])
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))


def log(message):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {message}", flush=True)


def send_email(subject, body, attachment_path=None):
    if not (SMTP_USER and SMTP_APP_PASSWORD and ALERT_EMAIL_TO):
        log("Email not configured (missing SMTP secrets) - relying on GitHub's failed-run notification.")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO
        msg.set_content(body)
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                msg.add_attachment(f.read(), maintype="image", subtype="png",
                                   filename=os.path.basename(attachment_path))
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as server:
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            server.send_message(msg)
        log("Alert email sent.")
    except Exception as e:
        log(f"FAILED to send alert email: {e}")


def fill_booking_form(page):
    d = DETAILS

    # Choose consulate (modal has no Save button; options appear after searching)
    page.get_by_role("button", name="Select location").click()
    page.wait_for_selector("#modal2", state="visible")
    page.locator("#modal2 input[placeholder='Search']").fill("Israel")
    page.get_by_text(CONSULATE_LABEL, exact=True).first.click()
    page.wait_for_timeout(500)
    if page.locator("#modal2").is_visible():
        page.locator("#modal2 button.close").click()
    page.wait_for_selector("#modal2", state="hidden", timeout=5000)

    # Choose case type
    page.get_by_role("button", name="Select type of application").click()
    page.wait_for_selector("#modalCases", state="visible")
    page.get_by_text(CASE_TYPE_LABEL, exact=True).first.click()
    page.get_by_role("button", name="Save").click()
    page.wait_for_load_state("networkidle")

    # Personal details
    page.get_by_label("Name", exact=True).fill(d["full_name"])
    dob = page.locator("#birthDate")
    dob.click()
    dob.fill(d["date_of_birth"])
    dob.press("Tab")
    page.get_by_label("Number of applicants", exact=True).fill(str(d["num_applicants"]))
    page.get_by_label("Phone number", exact=True).fill(d["phone"])
    page.get_by_label("Email address", exact=True).fill(d["email"])
    page.get_by_label("Re-enter the email address", exact=True).fill(d["email"])
    page.get_by_label("Name of your case-handler", exact=False).fill(d["case_handler"])
    page.get_by_label("How many documents do you have in total for legalization", exact=False).fill(
        str(d["num_documents"]))
    page.get_by_label("How many forms do you have in total with your application", exact=False).fill(
        str(d["num_forms"]))
    page.get_by_label("Are declaration of paternity forms included", exact=False).fill(d["paternity"])

    # Consent checkboxes
    page.get_by_text("I have read and acknowledged the information related to the procedure").click()
    page.get_by_text("I give my consent to the processing of my personal data").click()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, slow_mo=SLOW_MO_MS)
        page = browser.new_context(locale="en-US").new_page()
        try:
            page.goto(URL, wait_until="networkidle", timeout=60000)
            fill_booking_form(page)

            btn = page.get_by_role("button", name="Select date »")
            btn.wait_for(state="visible", timeout=30000)
            for _ in range(30):
                if btn.is_enabled():
                    break
                page.wait_for_timeout(500)
            btn.click()

            try:
                page.get_by_text(NO_APPOINTMENT_TEXT).wait_for(state="visible", timeout=10000)
                log("Checked: no appointments available right now.")
                return 0
            except PWTimeout:
                pass  # popup did not appear -> possible slot

            page.screenshot(path=SCREENSHOT_PATH, full_page=True)
            log("!!! POSSIBLE APPOINTMENT SLOT DETECTED !!!")
            send_email(
                "🚨 Hungary Consulate: possible appointment slot found!",
                "The 'no appointments available' popup did NOT appear.\n"
                "Go to the site NOW and finish the booking yourself:\n" + URL + "\n\n"
                "(The site will ask for a code sent to your email - keep an eye on that inbox.)",
                SCREENSHOT_PATH,
            )
            return 1

        except Exception as e:
            log(f"ERROR during check (no alert sent): {e}\n{traceback.format_exc()}")
            print(f"::warning title=Check failed::{e}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
