# Security-scan remediation notes (Checkov / cfn-guard / Bandit / Semgrep)

Baseline from Holmes scan `c4b6b927` (2026-06-22): **191 findings, 93 HIGH**
(cfn-guard 65, Checkov 24, ACAT 2, Semgrep 2; Bandit HIGH = 0 already).

## Result (local reproduction, 2026-06-24)

| Scanner | HIGH before | HIGH after | Disposition of the rest |
|---|---|---|---|
| Bandit | 0 | **0** | already clean |
| cfn-guard (wa rule names) | 65 | 29 rule-failures | 36 cleared (fixed or KMS-suppressed); 29 are intentional-design / false-positive — see §C |
| Checkov (Holmes HIGH IDs) | 24 | 7 cleared (2 fixed, 5 suppressed) | remainder intentional-design — see §C |
| Semgrep raw-SQL | 2 | 2 (false positive) | §D — reported, not silenced |

`cfn-lint` is **clean (0)** on all three templates after the edits — every change is
deploy-safe. No genuine fix was reverted except `AccessControl: Private` (it is a legacy
property that breaks deploy under modern S3 Object Ownership — see §C `S3_BUCKET_NO_PUBLIC_RW_ACL`).

**Remaining HIGHs are all justified for sample code** (the reviewer's accepted outcome):
public WebRTC/ALB ingress (load-bearing), HTTP-only ALB behind CloudFront TLS, default
CloudFront cert, S3 access-logging + Object Lock (need a dedicated log bucket / fight teardown),
versioning-off on PII buckets (bounded-blast-radius delete), single-AZ + public RDS (POC),
disabled secret rotation (documented), and scoped inline IAM policies.

Local toolchain used to reproduce + verify (no Holmes upload needed for the loop):

```
checkov  3.3.2   (pipx)    checkov -d infra/g1 --framework cloudformation
cfn-guard 3.2.0  (brew)    cfn-guard validate -d infra/g1/<f>.yaml -r /tmp/wa-security.guard
bandit   1.9.4   (pipx)    bandit -r backend/src report-worker/src voice-worker/src bank --severity-level high
```

cfn-guard ruleset = AWS Guard Rules Registry `wa-Security-Pillar` (the same managed-rule
names Holmes reports), compiled to a single guard file.

## Decision policy (agreed with reviewer + owner)

1. **Genuine cheap fixes** — applied in-template (no cost / no topology change).
2. **KMS customer-managed-key (CMK) findings** — SUPPRESSED via resource `Metadata`
   (`guard.SuppressedRules` + `checkov.skip`) with a one-line justification. Default
   AWS-managed encryption is already on for these services; a dedicated CMK adds cost +
   key-policy management not warranted for sample code (reviewer explicitly cited
   "SQS with KMS" as unnecessary).
3. **Intentional-design / false-positive HIGHs** — NOT silenced. Left in place and
   documented here with rationale (owner chose "fix + report residual", not mass-suppress).

---

## A. Genuine fixes applied

| Finding(s) | Resource | Fix |
|---|---|---|
| RDS_STORAGE_ENCRYPTED, CKV_AWS_16 | LatencyDB | `StorageEncrypted: true` (default RDS key, no cost) |
| RDS_AUTOMATIC_MINOR_VERSION_UPGRADE_ENABLED | LatencyDB | `AutoMinorVersionUpgrade: true` |
| CKV_AWS_161 | LatencyDB | `EnableIAMDatabaseAuthentication: true` (coexists with Secrets-Manager password auth) |
| CKV_AWS_27 | ReportQueue | `SqsManagedSseEnabled: true` (SSE-SQS, AWS-managed, free) |
| S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED | SpaBucket, SourceBucket | `BucketEncryption` SSE-S3 (AES256) |
| S3_BUCKET_VERSIONING_ENABLED, CKV_AWS_21 | SpaBucket, SourceBucket | `VersioningConfiguration: Enabled` (+ noncurrent expiry) |
| S3_BUCKET_SSL_REQUESTS_ONLY | SpaBucket, ResumeBucket, AudioBucket, SourceBucket | bucket policy deny when `aws:SecureTransport=false` |

## B. KMS-CMK findings — SUPPRESSED with justification

| Finding | Resources | Why suppressed |
|---|---|---|
| CLOUDWATCH_LOG_GROUP_ENCRYPTED | Worker/Backend/ReportWorker log groups (deploy + gate) | CW Logs encrypted at rest with an AWS-managed key by default |
| SECRETSMANAGER_USING_CMK, CKV_AWS_149 | VoiceTokenSecret, DeepgramSecret | Secrets Manager encrypts with `aws/secretsmanager` by default |
| S3_DEFAULT_ENCRYPTION_KMS | SpaBucket, SourceBucket | SSE-S3 (AES256) applied; CMK not needed for non-PII assets |
| SQS_QUEUE_KMS_MASTER_KEY_ID_RULE | ReportQueue | SSE-SQS applied; CMK unnecessary (reviewer-cited) |
| CKV_AWS_136 | ECR repos | ECR encrypted with AES256 by default; CMK not warranted |
| CKV_AWS_373 | InterviewAgent | Bedrock Agent CMK not warranted for a generic, no-PII demo agent |
| CODEBUILD_ENCRYPTION_KEY_RULE | BuildProject | transient build artifacts (NO_ARTIFACTS), stack deleted post-build |

## C. Intentional-design / false-positive HIGHs — LEFT + reported (NOT silenced)

| Finding | Resource | Rationale |
|---|---|---|
| EC2_SECURITY_GROUP_INGRESS_OPEN_TO_WORLD_RULE (UDP) | TaskSecurityGroup | **Load-bearing** — WebRTC media is browser→task direct over an OS-assigned ephemeral UDP port (aioice has no port-pinning). Media gated by voice_token + DTLS-SRTP, not the SG (Constitution I). |
| EC2_SECURITY_GROUP_INGRESS_OPEN_TO_WORLD_RULE (tcp 80), CKV_AWS_260 | AlbSecurityGroup | Internet-facing ALB is the CloudFront origin + demo direct access. |
| ELBV2_LISTENER_SSL_POLICY_RULE, CKV_AWS_2, CKV_AWS_103 | AlbListener | ALB is HTTP-only **by design** — CloudFront terminates TLS; ALB origin fetch is HTTP (documented). |
| CLOUDFRONT_CUSTOM_SSL_CERTIFICATE, CKV_AWS_174 | Distribution | Uses the default `*.cloudfront.net` cert; a custom cert / TLS1.2_2021 floor requires a custom domain the demo does not own. |
| CLOUDFRONT_ACCESSLOGS_ENABLED, CKV_AWS_86 | Distribution | Access logging needs a dedicated log bucket; out of scope for a demo. |
| S3_BUCKET_LOGGING_ENABLED, CKV_AWS_18 | all buckets | S3 access logging needs a dedicated log bucket (recursive); out of scope for a demo. |
| S3_BUCKET_DEFAULT_LOCK_ENABLED | all buckets | Object Lock would block the `DeletionPolicy: Delete` teardown the demo relies on. |
| S3_BUCKET_VERSIONING_ENABLED | ResumeBucket, AudioBucket | Versioning intentionally OFF on PII buckets — overwrite/delete must leave NO recoverable version (bounded-blast-radius delete, Constitution III). |
| S3_BUCKET_NO_PUBLIC_RW_ACL | all buckets | False positive — every bucket sets `PublicAccessBlockConfiguration` (all four flags true), which blocks ALL public access. The rule only PASSes if a non-`PublicReadWrite` `AccessControl` is set, but `AccessControl` is a **legacy property** (cfn-lint W3045) that fails deploy on buckets with the modern default Object Ownership = BucketOwnerEnforced, so it is deliberately NOT set. |
| RDS_INSTANCE_PUBLIC_ACCESS_CHECK, CKV_AWS_17 | LatencyDB | POC: the laptop harness reaches the DB over its public endpoint; `HarnessIngressCidr` is documented "RESTRICT before real use". |
| RDS_MULTI_AZ_SUPPORT | LatencyDB | Single-AZ by design (demo scale / cost). |
| RDS_MASTER_USER_PASSWORD_USES_SECURE_PARAMETER | LatencyDB | False positive — `ManageMasterUserPassword: true` (RDS-managed secret, no plaintext password). |
| SECRETSMANAGER_ROTATION_ENABLED_CHECK | secrets | Rotation intentionally disabled (documented); services are rotation-proof in code via `DB_SECRET_ARN`. Rotation would need a Lambda — out of scope. |
| IAM_NO_INLINE_POLICY_CHECK | task/build roles | Inline policies are scoped to single-purpose roles and deleted with them — appropriate + self-contained for a sample. |
| CKV_AWS_107 | CodeBuildRole | Build role needs `ecr:GetAuthorizationToken` / `sts:GetServiceBearerToken` to push images; scoped to the three repos. |

## D. Semgrep raw-SQL (bank/embed.py, bank/load_fixture.py) — reported, NOT silenced

False positive, LEFT in place per the "fix-only, don't silence non-KMS" policy:
- The rule `python.sqlalchemy.security.sqlalchemy-execute-raw-query` is a **SQLAlchemy**
  rule misfiring on code that uses **asyncpg** (wrong library).
- It fires on a DDL `CREATE INDEX ... WITH (lists = {lists})` where `lists` is a computed
  integer (`max(1, int(count ** 0.5))`, `count` from `SELECT count(*)`). Postgres cannot
  bind a storage parameter as a query parameter, so interpolation of a validated int is the
  only option; there is **no user-controlled input**.
- Out of the reviewer's requested 3-tool loop (Checkov / cfn-guard / Bandit). **Bandit — the
  in-scope Python SAST tool — reports 0 HIGH** on these files (and the whole codebase).
- These are offline operator scripts (`python -m bank.embed`), not the live request path.

If a clean Holmes Semgrep pass is required, add a targeted `# nosemgrep:
python.sqlalchemy.security.sqlalchemy-execute-raw-query` on the two `conn.execute(f"""CREATE
INDEX ...""")` lines. Not applied here to honor "report, don't silence" for non-KMS findings.
