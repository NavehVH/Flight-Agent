"""Send the final report email via Gmail SMTP.

Credentials come from environment variables (loaded from user-data.txt by
whichever script calls this): EMAIL_USER, EMAIL_PASS, RECIPIENT_EMAIL.
EMAIL_PASS must be a Gmail App Password, not the account password —
https://myaccount.google.com/apppasswords
"""

import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(subject: str, body: str) -> None:
    sender = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
