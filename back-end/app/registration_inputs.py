"""Registration inputs follow OpenAPI; read-only identity and role cannot be injected."""
import re
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Industry = Literal["RESTAURANT", "CAFE", "CONVENIENCE_STORE", "OTHER"]
Weekday = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
HalfHour = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):(00|30)$", strict=True)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$", strict=True)]
Name = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^\S(?:.*\S)?$", strict=True)]
Phone = Annotated[str, Field(pattern=r"^010[0-9]{8}$", strict=True)]


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Career(Input):
    industry: Industry
    duties: Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S", strict=True)]
    storeName: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S", strict=True)] | None = None
    startMonth: Month
    endMonth: Month | None
    isCurrent: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def months(self):
        current = today().strftime("%Y-%m")
        if self.startMonth.startswith("0000") or self.startMonth > current:
            raise ValueError("Invalid startMonth")
        if self.isCurrent != (self.endMonth is None):
            raise ValueError("Invalid current career")
        if self.endMonth is not None and not self.startMonth <= self.endMonth <= current:
            raise ValueError("Invalid endMonth")
        return self


class Availability(Input):
    days: Annotated[list[Weekday], Field(min_length=1, max_length=7)]
    startTime: HalfHour
    endTime: HalfHour
    endsNextDay: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def span(self):
        if len(set(self.days)) != len(self.days):
            raise ValueError("Duplicate days")
        if (self.endTime <= self.startTime) != self.endsNextDay:
            raise ValueError("Duration must be greater than zero and at most 24 hours")
        return self


class WorkerInput(Input):
    name: Name
    phoneNumber: Phone
    birthDate: date
    gender: Literal["MALE", "FEMALE"]
    experienceLevel: Literal["NEW", "EXPERIENCED"]
    careers: Annotated[list[Career], Field(max_length=20)]
    availabilities: Annotated[list[Availability], Field(min_length=1, max_length=100)]

    @field_validator("birthDate", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError("ISO calendar date required")
        return value

    @model_validator(mode="after")
    def profile(self):
        if self.birthDate > today():
            raise ValueError("Future birthday")
        if (self.experienceLevel == "NEW") != (len(self.careers) == 0):
            raise ValueError("Career count does not match experienceLevel")
        weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        intervals = []
        def minutes(value):
            h, m = map(int, value.split(":"))
            return h * 60 + m
        for group in self.availabilities:
            start = minutes(group.startTime)
            end = minutes(group.endTime) + 1440 * group.endsNextDay
            for day in group.days:
                begin = weekdays.index(day) * 1440 + start
                finish = weekdays.index(day) * 1440 + end
                # Shift both directions so Sunday->Monday overlap is detected.
                for a, b in intervals:
                    if any(begin < b + shift and a + shift < finish for shift in (-10080, 0, 10080)):
                        raise ValueError("Overlapping availabilities")
                intervals.append((begin, finish))
        return self

