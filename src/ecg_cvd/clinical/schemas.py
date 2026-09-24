"""Strict request shapes for the REST API."""
from __future__ import annotations

from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(APIModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(APIModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


class OrganizationRequest(APIModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")


class UserRequest(APIModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=2, max_length=200)
    role: Literal["HOSPITAL_ADMIN", "DOCTOR", "TECHNICIAN", "PATIENT"]
    organization_id: str | None = None
    patient_uuid: str | None = None


class PatientMatchRequest(APIModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = None
    phone: str | None = Field(default=None, max_length=50)
    mrn: str | None = Field(default=None, max_length=80)
    abha_id: str | None = Field(default=None, max_length=200)


class IdentifierInput(APIModel):
    identifier_system: str = Field(min_length=1, max_length=200)
    identifier_type: str = Field(min_length=1, max_length=64)
    identifier_value: str = Field(min_length=1, max_length=200)
    assigning_organization: str | None = Field(default=None, max_length=200)


class PatientRequest(APIModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    administrative_gender: str | None = Field(default=None, max_length=32)
    sex_at_birth: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    mrn: str | None = Field(default=None, max_length=80)
    identifiers: list[IdentifierInput] = Field(default_factory=list, max_length=12)
    confirm_existing_patient_uuid: str | None = None


class EncounterRequest(APIModel):
    patient_uuid: str
    encounter_type: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=2000)


class DiagnosisInput(APIModel):
    display: str = Field(min_length=1, max_length=300)
    code_system: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, max_length=100)


class PrescriptionItemInput(APIModel):
    medicine: str = Field(min_length=1, max_length=300)
    dose: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=100)
    frequency: str | None = Field(default=None, max_length=100)
    duration: str | None = Field(default=None, max_length=100)
    instructions: str | None = Field(default=None, max_length=2000)


class ReviewRequest(APIModel):
    doctor_assessment: str = Field(min_length=1, max_length=4000)
    review_status: Literal["REVIEWED", "REQUIRES_FOLLOW_UP", "NOT_INTERPRETABLE"] = "REVIEWED"
    diagnosis: DiagnosisInput | None = None
    clinical_note: str | None = Field(default=None, max_length=8000)
    prescription_items: list[PrescriptionItemInput] = Field(default_factory=list, max_length=20)
    prescription_instructions: str | None = Field(default=None, max_length=2000)


class FeatureRequest(APIModel):
    key: str = Field(min_length=2, max_length=100, pattern=r"^[A-Z0-9_]+$")
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = True
