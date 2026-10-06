"""Profile edits share the registration field and calendar rules."""
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.registration_inputs import Availability, Career, Input, Name, Phone, WorkerInput


class BasicInput(Input):
    name: Name | None = None
    phoneNumber: Phone | None = None
    birthDate: date | None = None
    gender: Literal["MALE", "FEMALE"] | None = None

    @field_validator("*", mode="before")
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError("Omit unchanged fields instead of null")
        return value

    @field_validator("birthDate", mode="before")
    @classmethod
    def calendar_date(cls, value):
        return WorkerInput.calendar_date(value)

    @field_validator("birthDate")
    @classmethod
    def past_birthday(cls, value):
        return WorkerInput.past_birthday(value)

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("At least one basic field is required")
        return self


class CareersInput(Input):
    experienceLevel: Literal["NEW", "EXPERIENCED"]
    careers: Annotated[list[Career], Field(max_length=20)]

    @field_validator("careers")
    @classmethod
    def career_count(cls, value, info: ValidationInfo):
        return WorkerInput.career_count(value, info)


class AvailabilitiesInput(Input):
    availabilities: Annotated[list[Availability], Field(min_length=1, max_length=100)]

    @field_validator("availabilities")
    @classmethod
    def available_intervals(cls, value):
        return WorkerInput.available_intervals(value)
