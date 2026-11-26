"""EmailSender: implements the MessageSender port with SMTP (docs/adr/0010).

Never sends twice (ADR 0004). What went wrong decides what the caller may do:
- before the server has the message (can't connect, wrong password, address refused, message
  refused): nothing was sent, so SourceUnavailable / SourceRejectedRequest, and a retry is safe;
- while handing it over (connection broke, no answer): it MAY have been sent → DeliveryUnknown,
  never retried automatically.
The recipient is checked again here, so a crafted "address" can never add mail headers
(header injection); EmailMessage also refuses line breaks in headers.
"""

import contextlib
import smtplib
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

from kiakia.domain.errors import DeliveryUnknown, SourceRejectedRequest, SourceUnavailable
from kiakia.domain.models import SentMessage

from .parser import is_address

SmtpFactory = Callable[[], smtplib.SMTP]


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        password: str,
        display_name: str,
        timeout: float = 20.0,
        smtp_factory: SmtpFactory | None = None,  # injectable for tests
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._address = address
        self._password = password
        self._display_name = display_name
        self._clock = clock
        self._connect: SmtpFactory = smtp_factory or (
            lambda: smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        )

    def __repr__(self) -> str:
        return f"EmailSender(address={self._address!r})"  # never the password

    def _message(self, to: str, text: str, message_id: str, sent_at: datetime) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self._display_name, self._address))
        msg["To"] = to
        msg["Subject"] = f"Message from {self._display_name}"
        msg["Date"] = format_datetime(sent_at)
        msg["Message-ID"] = message_id
        msg["Auto-Submitted"] = "no"  # a person wrote this, so other systems may answer it
        msg.set_content(text)
        return msg

    def send_text(self, external_chat_id: str, text: str) -> SentMessage:
        to = external_chat_id.strip().lower()
        if not is_address(to):
            raise SourceRejectedRequest("email: this conversation has no valid email address to reply to")
        sent_at = self._clock()
        message_id = make_msgid(domain=self._address.rsplit("@", 1)[1])
        msg = self._message(to, text, message_id, sent_at)

        # 1. Connect and sign in: nothing can have been sent yet.
        try:
            smtp = self._connect()
        except (OSError, smtplib.SMTPException) as exc:
            raise SourceUnavailable(f"email: could not reach the mail server ({type(exc).__name__})") from exc
        try:
            try:
                smtp.login(self._address, self._password)
            except smtplib.SMTPAuthenticationError:
                raise SourceRejectedRequest(
                    "email: sign-in refused (check EMAIL_ADDRESS and EMAIL_APP_PASSWORD)"
                ) from None
            except (OSError, smtplib.SMTPException) as exc:
                raise SourceUnavailable(f"email: could not sign in ({type(exc).__name__})") from exc

            # 2. Hand the message over.
            try:
                refused = smtp.send_message(msg, from_addr=self._address, to_addrs=[to])
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
                raise SourceRejectedRequest(
                    f"email: the address was refused ({type(exc).__name__})"
                ) from None
            except smtplib.SMTPDataError as exc:
                # The server answered "no" to the message itself: it was NOT accepted.
                raise SourceRejectedRequest(
                    f"email: the mail server refused the message ({exc.smtp_code})"
                ) from None
            except (OSError, smtplib.SMTPException):
                raise DeliveryUnknown(
                    "email: the connection broke while sending; it may have been delivered"
                ) from None
            if refused:
                raise SourceRejectedRequest("email: the address was refused")
        finally:
            # Already sent (or failed above): a problem while closing doesn't change that.
            with contextlib.suppress(OSError, smtplib.SMTPException):
                smtp.quit()

        return SentMessage(external_message_id=message_id.strip("<>"), sent_at=sent_at)
