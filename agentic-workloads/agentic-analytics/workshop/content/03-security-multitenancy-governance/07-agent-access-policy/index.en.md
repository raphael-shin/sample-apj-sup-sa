---
title: "Step 7: Multi-Tenant Isolation & User Access"
weight: 50
---

## Learning Objectives

By the end of this step, you will:
- Enforce tool-level access control with Cedar policies (analysts can't create bookings)
- Switch from the database owner role to an RLS-enforced role for tenant data isolation
- Deploy a Gateway Interceptor that propagates JWT claims to Lambda targets
- Wire JWT claims through to PostgreSQL session variables for row-level security
- Verify that different tenants see only their own data

## The Problem

Your current analytics assistant up to the previous step has two security gaps:

1. **Any user can use any tool.** Analyst Orion Moonshadow can create bookings — but analysts should only read data, not modify it.
2. **Any user can see all tenants' data.** A user at Mythical Unicorns can see Mythic Unicorns' customers and revenue. In a multi-tenant SaaS platform, this is a data breach.

You need two layers of security:
- **Tool-level access control** — which tools each role can use
- **Data-level isolation** — which rows each tenant and role can see

::alert[**SaaS critical:** In a pool model (shared database), a single misconfigured query could expose one tenant's data to another. You need enforcement at the **infrastructure level** — not in the agent's prompt, not in the application code. :link[AgentCore Policy]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html"} handles tool access. :link[PostgreSQL Row-Level Security]{href="https://www.postgresql.org/docs/current/ddl-rowsecurity.html"} handles data isolation. Together, they make multi-tenant security deterministic and auditable.]{type="warning"}

## The Solution

| Layer | Technology | What It Controls | Enforcement Point |
|-------|-----------|-----------------|-------------------|
| **Tool access** | :link[AgentCore Policy (Cedar)]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html"} | Which tools each role can use | Gateway — tool is hidden from unauthorized users |
| **Data isolation** | :link[PostgreSQL RLS]{href="https://www.postgresql.org/docs/current/ddl-rowsecurity.html"} | Which rows each tenant can see | Database engine — impossible to bypass from application |
| **Identity propagation** | :link[Gateway Interceptor]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html"} | Passes JWT from Gateway to Lambda targets | Gateway — forwards Authorization header to targets |

The flow:

```
User logs in → Cognito JWT (custom:role [for role-based access control], custom:account_id [for tenant isolation])
  → Agent passes JWT to Gateway as Bearer token
    → Cedar Policy evaluates: can this role use this tool?
    → Interceptor propagates JWT to Lambda target with header injection
      → Lambda extracts claims, SETs PostgreSQL session variables
        → RLS policy: WHERE account_id = get_current_account_id()
          → Only this tenant's rows returned
```

## Lab Procedures

### Step 7.1: Add Cedar Policies (TODO 7.1)

:link[AgentCore Policy]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html"} uses :link[Cedar]{href="https://www.cedarpolicy.com/"}, a policy language created by AWS. Cedar policies are **deterministic** — unlike prompt-based restrictions, they use formal logic for safeguarding against prompt injection.

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true}. **Step 7a has TWO fences you must uncomment** — both are labelled `Step 7a`:

1. The **Policy Engine + Cedar policies** block (the `PolicyEngine` resource and the three `AWS::BedrockAgentCore::Policy` resources).
2. The **3-line `PolicyEngineConfiguration` block on the Gateway** (this wires the Gateway to the policy engine — they must come live together, or the Gateway would reference a policy engine that doesn't exist).

::::expand{header="💡 Need help with TODO 7.1? Click to see exactly what to uncomment"}
Uncomment **both** Step-7a fences:
- The `# ===== UNCOMMENT FROM HERE (Step 7a: Cedar policy engine ...)` fence — the `PolicyEngine`, `AllowAllToolsPolicy`, `ForbidWriteAnalystPolicy`, and `ForbidCustomSqlStaffPolicy` resources.
- The small `# --- Step 7a: ALSO uncomment these 3 lines ...` block on the `Gateway` resource — the `PolicyEngineConfiguration:` / `Arn:` / `Mode:` lines.

If you uncomment only the policies but not the Gateway block, the policies exist but aren't enforced. If you uncomment only the Gateway block but not the policies, `make deploy` fails because the Gateway references a `PolicyEngine` that isn't there. Uncomment both.
::::

Then deploy — first in LOG_ONLY mode (the default), which logs policy decisions without blocking:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

Once that succeeds, switch to **enforcement** by redeploying with the policy mode flipped:

```bash
make deploy POLICY_MODE=ENFORCE
```

The three policies (already written in the template) are:

```cedar
// 1. Base permit — allow all tools for any authenticated principal
permit(principal, action, resource == AgentCore::Gateway::"<arn>");

// 2. Forbid booking tool for analysts
forbid(principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"APIInteg___create_booking_tool",
  resource == AgentCore::Gateway::"<arn>")
when { principal.getTag("custom:role") == "analyst" };

// 3. Forbid Custom SQL for staff (too risky for non-technical users)
forbid(principal is AgentCore::OAuthUser,
  action in [AgentCore::Action::"CustomSQL___text_to_sql_tool",
             AgentCore::Action::"CustomSQL___execute_sql_tool"],
  resource == AgentCore::Gateway::"<arn>")
when { principal.getTag("custom:role") == "staff" };
```

::alert[**`make deploy POLICY_MODE=ENFORCE`** passes `PolicyMode=ENFORCE` to the stack — the same `PolicyMode` parameter the Gateway's `PolicyEngineConfiguration.Mode` reads. LOG_ONLY logs decisions but allows every call (useful for testing); ENFORCE blocks unauthorized calls at the Gateway.]{type="info"}

::alert[**Forbid wins over permit.** Cedar uses default-deny with forbid-wins semantics. The base permit allows everything, then forbid policies carve out exceptions. If any forbid matches, access is denied — regardless of permits. This is the :link[recommended pattern]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/example-policies.html"} for AgentCore Policy.]{type="info"}

### Step 7.2: Switch to the RLS-Enforced Database Role (TODO 7.2)

Currently, your Lambda tools connect to the database as `postgres` — the table owner. In PostgreSQL, **table owners bypass Row-Level Security by default**. This means RLS policies have no effect, and all tenants' data is visible.

To fix this, you'll switch to `app_user` — a non-owner role created during the base CloudFormation deployment. Because `app_user` doesn't own the tables, PostgreSQL automatically enforces RLS on every query.

#### TODO 7.2: Flip the EnforceRls switch

Open :code[agentcore-topup-stack.yaml]{showCopyAction=true} and find `TODO 7.2` in the `Conditions:` block near the top of the file. **Comment the first line and uncomment the second** — exactly one line each:

```yaml
# Before (postgres — bypasses RLS):
  EnforceRls: !Equals ['off', 'on']     # default OFF — postgres secret, RLS bypassed
  # EnforceRls: !Equals ['on', 'on']    # Step 7.2 ON — app_user secret, RLS enforced

# After (app_user — RLS enforced):
  # EnforceRls: !Equals ['off', 'on']   # default OFF — postgres secret, RLS bypassed
  EnforceRls: !Equals ['on', 'on']      # Step 7.2 ON — app_user secret, RLS enforced
```

Then redeploy:

```bash
make deploy
```

::alert[**What does this change?** All three SQL Lambdas read their database secret as `AURORA_SECRET_ARN: !If [EnforceRls, <app_user secret>, <postgres secret>]`. With `EnforceRls` off, they use the `postgres` owner secret (RLS bypassed). Flipping it on points them at the `app_user` secret (non-owner, RLS enforced). The secret ARNs are imported from the base stack; the Lambda code doesn't change — only which secret it reads. One `make deploy` updates all three Lambdas at once.]{type="info"}

### Step 7.3: Understand RLS Session Variables

The Lambda tools SET PostgreSQL session variables from the JWT claims before executing queries. The RLS policies use these variables to filter rows:

```sql
-- RLS policy on the customers table (already in the schema):
CREATE POLICY tenant_read_customers ON customers
  FOR SELECT USING (account_id = get_current_account_id());
-- get_current_account_id() reads the session variable SET by the Lambda
```

Open :code[tools/prebaked_sql_toolset_lambda.py]{showCopyAction=true} and look at `get_db_connection`:

```python
def get_db_connection(rls_context=None):
    ...
    if rls_context and (rls_context.get('account_id') or rls_context.get('role')):
        with conn.cursor() as cur:
            if rls_context.get('account_id'):
                cur.execute("SET app.current_account_id = %s", [rls_context['account_id']])
            if rls_context.get('role'):
                cur.execute("SET app.current_user_role = %s", [rls_context['role']])
    return conn
```

This pattern is the same in all three Lambda toolsets. The `rls_context` is extracted from the JWT by `lambda_handler` and passed through the call chain. When the Lambda connects as `app_user` (after the credential switch in Step 7.2), PostgreSQL RLS policies read these session variables to filter rows by tenant.

### Step 7.4: Examine the Gateway Interceptor

The Lambda tools now know how to SET session variables from JWT claims — but the JWT needs to reach the Lambda first. By default, the Gateway authenticates the request but does **not** forward the Authorization header to Lambda targets.

The :link[Gateway Interceptor]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html"} solves this. It's already part of your stack's baseline — the `InterceptorLambda` and the Gateway's `InterceptorConfigurations` came live with the very first `make deploy` in Step 2. It's a Lambda function that runs on every Gateway request and injects headers into the target call. The `Authorization` header from the interceptor response is :link[automatically propagated to the target]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html#gateway-headers-interceptor-propagation"}.

Open :code[infra/interceptor_lambda.py]{showCopyAction=true} and look at the key section:

```python
# Extract Authorization header (case-insensitive)
auth_header = None
for key, value in headers.items():
    if key.lower() == 'authorization':
        auth_header = value
        break

# Propagate to Lambda targets
response_headers = {}
if auth_header:
    response_headers['Authorization'] = auth_header
```

This is the bridge between the Gateway (which validates the JWT) and the Lambda (which reads the JWT claims for RLS). Without it, `_extract_rls_context_from_jwt()` in the Lambda would receive no headers and RLS would have no tenant or user's role context.

### Step 7.5: One deploy updates all three Lambdas

There are **no separate scripts to re-run.** Because all three SQL Lambdas share the single `EnforceRls` switch you flipped in Step 7.2, the `make deploy` you ran there already updated `DataFoundationLambda`, `ApiIntegLambda`, and `CustomSqlLambda` to read the `app_user` secret in one shot. Confirm the stack settled:

```bash
make status   # expect UPDATE_COMPLETE
```

If you ran Steps 7.1 and 7.2 as separate `make deploy` calls (recommended), the Cedar enforcement and the RLS switch are both live now.

### Step 7.6: Test Tool-Level Access Control

All test users share the same password: :code[Unicorn123!]{showCopyAction=true}

| User | Role | Tenant |
|------|------|--------|
| :code[lyra.starwhisper@example-mythicalunicorns.com]{showCopyAction=true} | rental_admin | Mythical Unicorns |
| :code[orion.moonshadow@example-mythicalunicorns.com]{showCopyAction=true} | analyst | Mythical Unicorns |
| :code[aria.skybloom@example-mythicunicorns.com]{showCopyAction=true} | rental_admin | Mythic Unicorns |

::alert[**Start fresh:** It is best to clear the chatbot conversation from the previous step by clicking the small bin icon next to the chat input field or by refreshing the application demo browser tab.]{type="info"}

**Test as Admin (Lyra):**
1. Log in as Lyra, ask: **"Show me top 5 customers by revenue"**
2. You should see customers.
3. Ask: **"Create a booking for my top customer next Sunday 2:30 pm for 30 mins with unicorn Vega Sapphire"** — it should work

**Test as Analyst (Orion):**
4. Log out, log in as Orion
5. Ask: **"Show me top 5 customers by revenue"** — same query, same tenant data
6. Ask: **"Create a booking for my top customer next Sunday 2:30 pm for 30 mins with unicorn Vega Sapphire"** — the agent cannot do this. The `create_booking_tool` is **inaccessible** for the analyst.

### Step 7.7: Test Tenant Data Isolation

This is the most important test. Log in as users from **different tenants** and verify they see different data.

**As Mythical Unicorns (Lyra):**
1. Ask: **"Show me top 3 customers"** — note the customer names
2. Ask: **"What's my total revenue?"** — note the figure

**As Mythic Unicorns (Aria):**
3. Log out, log in as :code[aria.skybloom@example-mythicunicorns.com]{showCopyAction=true}
4. Ask: **"Show me top 3 customers"** — you should see **completely different** names
5. Ask: **"What's my total revenue?"** — the figure should be different

::alert[**This is the pool model in action.** Both tenants share the same agent, same Gateway, same Lambda, same database — but each sees only their own data. The isolation is enforced by PostgreSQL RLS, not by the application. Even if the LLM generates a Custom SQL query without a tenant filter, RLS still protects the data.]{type="success"}

### Step 7.8: The Invisibility Test

The Cedar policy doesn't just *refuse* the booking tool — it makes it **invisible**.

1. Log in as Orion (analyst), ask: **"What tools do you have?"**
2. The agent lists its tools — Create Booking tool is **not in the list**
3. Log in as Lyra (admin), ask the same — The Create Booking tool **appears**

::alert[**Infrastructure-level vs prompt-level security.** A prompt restriction ("don't let analysts create bookings") can be bypassed with prompt injection. Cedar policy cannot — the tool literally doesn't exist in the analyst's session. The agent can't call what it can't see.]{type="info"}

## How It All Fits Together

```
┌─────────────────────────────────────────────────────────┐
│                    Security Layers                        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Layer 1: AgentCore Policy (Cedar)                       │
│  ├─ Evaluates JWT claims (custom:role)                   │
│  ├─ Hides unauthorized tools from agent                  │
│  └─ Enforcement: Gateway level                           │
│                                                          │
│  Layer 2: Gateway Interceptor                            │
│  ├─ Propagates Authorization header to Lambda            │
│  └─ Enables identity-aware tool execution                │
│                                                          │
│  Layer 3: PostgreSQL RLS                                 │
│  ├─ Lambda SETs session vars from JWT claims             │
│  ├─ RLS policies filter rows by account_id              │
│  ├─ Views use security_invoker = true                    │
│  └─ Enforcement: Database engine level                   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Verification

- After uncommenting both Step-7a fences and `make deploy`, the stack has a Policy Engine with 3 Cedar policies
- `make deploy POLICY_MODE=ENFORCE` switches the Gateway to enforcement mode
- Flipping `EnforceRls` and `make deploy` updates all three SQL Lambdas to the `app_user` secret
- Admin (Lyra) can create bookings; analyst (Orion) cannot see the tool
- Mythical Unicorns user sees only Mythical Unicorns data
- Mythic Unicorns user sees only Mythic Unicorns data

## Troubleshooting

**Still seeing all tenants' data**
- Did you flip the `EnforceRls` condition (comment line 1, uncomment line 2) and `make deploy`?
- Confirm `make status` shows `UPDATE_COMPLETE` after the flip.
- The Gateway Interceptor must be live — it's in the Step-2 baseline, so confirm the stack deployed cleanly back then.

**Analyst can still create bookings**
- Verify you ran `make deploy POLICY_MODE=ENFORCE` (not just `make deploy`, which defaults to LOG_ONLY).
- The user must log in via the Cognito Hosted UI (click Login button). Direct API auth doesn't carry OAuth claims.

**Queries return zero results**
- The interceptor must be deployed for JWT propagation. Without it, the Lambda has no JWT context and RLS blocks all rows (fail-closed).
- Check that you're logged in (not Guest mode).

## Reference Materials

- :link[AgentCore Policy Documentation]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html" external=true}
- :link[Cedar Policy Language]{href="https://www.cedarpolicy.com/" external=true}
- :link[AgentCore Gateway — Header Propagation]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html" external=true}
- :link[AgentCore Gateway — Interceptors]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html" external=true}
- :link[PostgreSQL Row-Level Security]{href="https://www.postgresql.org/docs/current/ddl-rowsecurity.html" external=true}
- :link[AWS SaaS Lens — Pool Model Data Isolation]{href="https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/pool-model.html" external=true}
- :link[Amazon Cognito — Pre-Token Generation Lambda]{href="https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html" external=true}
