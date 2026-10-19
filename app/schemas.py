import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


def validate_email(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value):
        raise ValueError('请输入有效邮箱地址')
    return value


def validate_time(value):
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('时间须为 HH:MM')
    return value


class SubscriptionCreate(StrictModel):
    nickname: ShortText
    email: str
    topics: list[ShortText] = Field(default_factory=list, max_length=10)
    keywords: list[ShortText] = Field(default_factory=list, max_length=20)
    send_time: str = '09:00'
    enabled: bool = True

    _email = field_validator('email')(validate_email)
    _time = field_validator('send_time')(validate_time)

    @model_validator(mode='after')
    def require_interest(self):
        if not self.topics and not self.keywords:
            raise ValueError('请至少填写一个关注话题或关键词')
        return self


class SubscriptionPatch(StrictModel):
    nickname: ShortText | None = None
    email: str | None = None
    topics: list[ShortText] | None = Field(default=None, max_length=10)
    keywords: list[ShortText] | None = Field(default=None, max_length=20)
    send_time: str | None = None
    enabled: bool | None = None

    @model_validator(mode='after')
    def no_null(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError('更新字段不能为 null')
        return self


class NewsItem(StrictModel):
    id: str
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    excerpt: str = ''


class BriefItem(StrictModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]
    relevance: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
    source_ids: list[str] = Field(min_length=1, max_length=5)


class AgentBrief(StrictModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    overview: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]
    items: list[BriefItem] = Field(max_length=5)
