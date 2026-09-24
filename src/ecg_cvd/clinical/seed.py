"""Create clearly fake local-development platform users and records."""
from __future__ import annotations

import argparse
from datetime import date
import getpass
from pathlib import Path

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .app import create_app
from .db import get_session
from .models import Organization, Patient, PatientOrganization, Role, User, UserRole
from .services import initialize_reference_data


def seed_development_data(app, password: str) -> None:
    if len(password) < 12:
        raise ValueError("Choose a development seed password with at least 12 characters.")
    with app.app_context():
        session = get_session()
        initialize_reference_data(session)
        hospital = session.scalar(select(Organization).where(Organization.code == "CITYCARD"))
        if hospital is None:
            hospital = Organization(name="City Cardiology Hospital (demo)", code="CITYCARD")
            session.add(hospital)
            session.flush()
        patient = session.scalar(select(Patient).where(Patient.empi_id == "EMPI-DEMO-000001"))
        if patient is None:
            patient = Patient(empi_id="EMPI-DEMO-000001", first_name="Ravi", last_name="Kumar",
                              date_of_birth=date(1974, 6, 12), administrative_gender="male")
            session.add(patient)
            session.flush()
            session.add(PatientOrganization(patient_uuid=patient.patient_uuid, organization_id=hospital.organization_id,
                                            mrn="CITYCARD-DEMO-0001"))
        roles = {role.name: role for role in session.scalars(select(Role)).all()}
        fixtures = [
            ("superadmin@example.test", "Demo Super Admin", "SUPER_ADMIN", None, None),
            ("hospitaladmin@example.test", "Demo Hospital Admin", "HOSPITAL_ADMIN", hospital.organization_id, None),
            ("doctor@example.test", "Demo Doctor", "DOCTOR", hospital.organization_id, None),
            ("technician@example.test", "Demo ECG Technician", "TECHNICIAN", hospital.organization_id, None),
            ("patient@example.test", "Demo Patient", "PATIENT", hospital.organization_id, patient.patient_uuid),
        ]
        for email, name, role_name, organization_id, patient_id in fixtures:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name=name, password_hash=generate_password_hash(password),
                            organization_id=organization_id, patient_id=patient_id)
                session.add(user)
                session.flush()
                session.add(UserRole(user_id=user.user_id, role_id=roles[role_name].role_id,
                                     organization_id=organization_id))
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed fake ECG health-platform development data.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--password", help="Development-only password for all fake seed accounts")
    args = parser.parse_args()
    password = args.password or getpass.getpass("Development seed password (not stored in source): ")
    app = create_app({"PROJECT_ROOT": args.project, "ENVIRONMENT": "development"})
    seed_development_data(app, password)
    print("Seeded fake development users and City Cardiology Hospital. The supplied password was not printed or saved.")


if __name__ == "__main__":
    main()
