import smtplib
from email import policy
from email.parser import BytesParser

import pytest

from app.mailer import Mailer
from app.rendering import html_email


class FakeSMTP:
    def __init__(self,*args,**kwargs):self.payload=None;self.closed=False
    def login(self,*args):pass
    def mail(self,*args):return 250,b'OK'
    def rcpt(self,*args):return 250,b'OK'
    def data(self,payload):self.payload=payload;return 250,b'OK'
    def close(self):self.closed=True


@pytest.fixture
def smtp_settings(settings):
    settings.smtp_username=settings.smtp_from='sender@example.com'
    settings.smtp_password='private-authorization-code'
    return settings


BRIEF={'title':'测试简报','overview':'<script>alert(1)</script>','items':[]}


def test_html_escaped():
    html=html_email(BRIEF)
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_accepted_and_mime(smtp_settings):
    fake=FakeSMTP()
    status,error=Mailer(smtp_settings,lambda *a,**k:fake).send('reader@example.com',BRIEF,'123')
    assert status=='accepted' and error is None and fake.closed
    parsed=BytesParser(policy=policy.default).parsebytes(fake.payload)
    assert parsed['To']=='reader@example.com'
    assert parsed['Subject']=='测试简报'
    assert parsed.is_multipart()
    assert b'\r\n' in fake.payload and b'private-authorization-code' not in fake.payload


def test_auth_failed(smtp_settings):
    class Bad(FakeSMTP):
        def login(self,*args):raise smtplib.SMTPAuthenticationError(535,b'private error text')
    status,error=Mailer(smtp_settings,Bad).send('reader@example.com',BRIEF,'123')
    assert status=='failed' and 'private' not in error


def test_data_timeout_uncertain(smtp_settings):
    class Bad(FakeSMTP):
        def data(self,*args):raise TimeoutError()
    assert Mailer(smtp_settings,Bad).send('reader@example.com',BRIEF,'123')[0]=='uncertain'


def test_explicit_data_rejection_retryable(smtp_settings):
    class Bad(FakeSMTP):
        def data(self,*args):raise smtplib.SMTPDataError(554,b'no')
    assert Mailer(smtp_settings,Bad).send('reader@example.com',BRIEF,'123')[0]=='failed'


def test_before_data_failure_retryable(smtp_settings):
    class Bad(FakeSMTP):
        def mail(self,*args):raise smtplib.SMTPServerDisconnected()
    assert Mailer(smtp_settings,Bad).send('reader@example.com',BRIEF,'123')[0]=='failed'


def test_missing_configuration(settings):
    assert Mailer(settings).send('reader@example.com',BRIEF,'123')[0]=='failed'
