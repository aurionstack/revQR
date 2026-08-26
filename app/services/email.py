import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import asyncio

from app.config import settings

logger = logging.getLogger(__name__)


def _send_smtp_sync(to_email: str, subject: str, html_content: str, text_content: str):
    """Synchronous SMTP email sending function to run in a thread pool."""
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        # SMTP not configured — log securely to server console
        print(f"\n{'='*70}")
        print(f"[SECURE EMAIL SERVICE] (SMTP not configured in .env)")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(f"Content:\n{text_content}")
        print(f"{'='*70}\n")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = to_email

        part1 = MIMEText(text_content, "plain", "utf-8")
        part2 = MIMEText(html_content, "html", "utf-8")
        msg.attach(part1)
        msg.attach(part2)

        context = ssl.create_default_context()
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, to_email, msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                if settings.SMTP_TLS:
                    server.starttls(context=context)
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, to_email, msg.as_string())

        logger.info(f"Password reset email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


async def send_password_reset_email(to_email: str, reset_url: str, business_name: str = "there") -> bool:
    """
    Sends a secure password reset link to the user's verified email address.
    Runs asynchronously without blocking the web request.
    """
    subject = "Reset Your revQR Password"

    text_content = f"""Hi {business_name},

We received a request to reset your password for revQR.

Click the link below or copy and paste it into your browser to set a new password:
{reset_url}

This link is valid for 30 minutes. If you did not request this password reset, you can safely ignore this email — your account remains completely secure.

Best regards,
The revQR Team
"""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f4f4f5; margin:0; padding:20px; color:#18181b; }}
        .card {{ max-width:480px; margin:0 auto; background:#ffffff; border-radius:12px; padding:32px 24px; border:1px solid #e4e4e7; }}
        .btn {{ display:inline-block; background:#18181b; color:#ffffff !important; padding:12px 24px; text-decoration:none; border-radius:8px; font-weight:600; font-size:14px; margin:20px 0; }}
        .hint {{ color:#71717a; font-size:12px; line-height:1.5; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="margin-top:0; font-size:20px;">Reset Your Password</h2>
        <p>Hi {business_name},</p>
        <p>We received a request to reset the password for your revQR account.</p>
        <div style="text-align:center;">
          <a href="{reset_url}" class="btn">Set New Password</a>
        </div>
        <p class="hint">This secure link is valid for <strong>30 minutes</strong>. If you did not request this reset, you can safely ignore this email — no one can access your account without access to your email inbox.</p>
        <hr style="border:none; border-top:1px solid #e4e4e7; margin:24px 0;" />
        <p class="hint" style="margin-bottom:0;">revQR · Built for Indian SMBs</p>
      </div>
    </body>
    </html>
    """


    # Run sending in background threadpool
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_smtp_sync, to_email, subject, html_content, text_content)
