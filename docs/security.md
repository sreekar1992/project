# Security notes and limitations

## Controls present in the code

- Passwords use Werkzeug password hashes; JWT access/refresh tokens use distinct configurable signing material.
- RBAC is resolved server-side, and service methods scope records to organization and, for patient accounts, to the linked patient.
- Audit rows capture action, resource reference, success state, request ID, actor when available, organization, IP, user agent, and limited metadata. Callers are instructed not to place raw ECG/PHI in audit metadata.
- The API emits request IDs, no-store cache headers, MIME-sniffing protection, frame denial, restrictive CORS handling, and other basic security headers. HSTS is added outside development.
- Uploads use a configured size limit, secure filenames, extension validation, and parser validation before persistent storage.
- Private storage validates object keys, avoids overwriting local assets, records SHA-256 hashes, and checks hash integrity before file/PDF/explanation download.
- New local clinical assets use application-level **AES-256-GCM** with a fresh per-object nonce and the logical object key as authenticated associated data. The local 32-byte key is created with mode `0600` under ignored `var/` storage by default, or can be directed with `LOCAL_ASSET_ENCRYPTION_KEY_PATH`. Existing plaintext local assets remain readable and are reported as legacy until a deliberate migration/re-upload.
- S3 storage requests S3 server-side `AES256` encryption. The UI reports the actual storage posture for each authorized ECG; it does not label legacy local objects as encrypted.

## Important limits

The default Flask rate limiter uses in-memory storage, even when the Compose stack contains Redis. It is not a shared, durable rate-limit control across replicas. The application does not perform malware scanning, content-disarm/reconstruction, device attestation, signed-upload verification, DLP, centralized SIEM export, database field encryption, or independent append-only audit storage.

JWT signing uses a shared-secret HS256 design and has no key rotation, issuer federation, MFA, SSO, password reset, CSRF-token design, step-up authentication, or privileged-action confirmation. The refresh cookie is HttpOnly and SameSite=Lax, but the overall browser/session model still needs a formal threat-model and CSRF review before real use.

S3 object access may use presigned URLs; their bucket policy, encryption keys, logging, expiry, and network reachability must be managed outside this source code. Production starts fail if the configured bucket is absent unless an operator explicitly enables bucket creation; pre-provision it with a least-privilege identity instead. The local AES key file is a development safeguard, not a substitute for a KMS/HSM, encrypted backups, key rotation, or access governance. Do not use the legacy demonstration password or adversarial waveform camouflage for patient records.

## Required before any real patient data

Do not classify the implementation as HIPAA-, DPDP-, GDPR-, ISO 27001-, SOC 2-, or clinically compliant. Compliance depends on deployment, contracts, institution policies, jurisdiction, validation, and independent assessment—not a dependency list or source code alone.

Before handling real patient information, establish threat modeling, security testing, penetration testing, secrets rotation, MFA/SSO, least-privilege infrastructure identity, TLS and certificate policy, encrypted backups, disaster recovery, logging/alerting, incident response, retention/deletion controls, consent/privacy governance, vendor risk review, and a formal clinical safety/governance process. Validate authorization and tenant isolation under adversarial tests, not only happy-path tests.
