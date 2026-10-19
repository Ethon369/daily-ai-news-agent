import smtplib
import socket
import ssl
from email import policy
from email.message import EmailMessage
from email.utils import formatdate

from .rendering import html_email, markdown
from .schemas import validate_email


class Mailer:
    def __init__(self, settings, factory=smtplib.SMTP_SSL):
        self.settings = settings
        self.factory = factory

    def send(self, recipient, brief, delivery_id):
        cfg = self.settings
        if not cfg.smtp_ready:
            return 'failed', 'SMTP 未配置：请填写 SMTP_USERNAME、SMTP_PASSWORD、SMTP_FROM'
        try:
            sender = validate_email(cfg.smtp_from)
            recipient = validate_email(recipient)
        except ValueError:
            return 'failed', '发件或收件邮箱格式无效'
        message = EmailMessage()
        message['Subject'] = brief['title'].replace('\r', ' ').replace('\n', ' ')[:180]
        message['From'] = sender
        message['To'] = recipient
        message['Date'] = formatdate(localtime=False)
        message['Message-ID'] = f'<{delivery_id}@daily-ai.local>'
        message.set_content(markdown(brief))
        message.add_alternative(html_email(brief), subtype='html')
        smtp = None
        phase = 'connect'
        try:
            smtp = self.factory(cfg.smtp_host, cfg.smtp_port, timeout=20, context=ssl.create_default_context())
            smtp.login(cfg.smtp_username, cfg.smtp_password)
            phase = 'envelope'
            code, _ = smtp.mail(sender)
            if code != 250:
                return 'failed', f'SMTP 发件人被拒绝 ({code})'
            code, _ = smtp.rcpt(recipient)
            if code not in (250, 251):
                return 'failed', f'SMTP 收件人被拒绝 ({code})'
            phase = 'data'
            code, _ = smtp.data(message.as_bytes(policy=policy.SMTP))
            if code != 250:
                return 'failed', f'SMTP 明确拒绝正文 ({code})'
            return 'accepted', None
        except smtplib.SMTPAuthenticationError:
            return 'failed', 'SMTP 认证失败，请检查邮箱服务和授权码'
        except smtplib.SMTPResponseException as exc:
            return 'failed', f'SMTP 明确拒绝请求 ({exc.smtp_code})'
        except (OSError, socket.timeout, smtplib.SMTPException):
            if phase == 'data':
                return 'uncertain', 'SMTP 正文提交期间连接中断，结果不确定，请核查收件箱；不会自动重发'
            return 'failed', 'SMTP 连接失败，正文尚未提交，可检查网络后重试'
        finally:
            if smtp:
                # Do not let QUIT/network failure turn an accepted DATA into a retry.
                try:
                    smtp.close()
                except OSError:
                    pass
