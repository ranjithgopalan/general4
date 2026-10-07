# Vault Lambda Extension - Technical Design Document

## Table of Contents
1. [Overview](#overview)
2. [HashiCorp Vault URLs and Endpoints](#hashicorp-vault-urls-and-endpoints)
3. [Secret Configuration and Provisioning](#secret-configuration-and-provisioning)
4. [AppRole Authentication Method](#approle-authentication-method)
5. [Secure Access Flow](#secure-access-flow)
6. [Vault Authentication Sequence Diagram](#vault-authentication-sequence-diagram)
7. [Runtime Secret Access by Lambda Functions](#runtime-secret-access-by-lambda-functions)
8. [Lambda-to-Lambda Secret Retrieval Pattern](#lambda-to-lambda-secret-retrieval-pattern)
9. [Dynamic Secrets Management](#dynamic-secrets-management)
10. [Digital Certificate Lifecycle Management](#digital-certificate-lifecycle-management)
11. [Component Design](#component-design)
12. [API Definitions](#api-definitions)
13. [Security Considerations](#security-considerations)

## Overview

The Vault Lambda Extension enables AWS Lambda functions to securely retrieve secrets from HashiCorp Vault without requiring static credentials. This integration ensures that sensitive credentials are never stored in code or environment variables, following security best practices for secrets management.

Lambda functions use the vault-lambda-extension client to authenticate with HashiCorp Vault and retrieve both **static secrets** (such as API credentials, database passwords, and configuration data) and **dynamic secrets** (such as TLS certificates, temporary database credentials, and time-limited access tokens). The authentication mechanism leverages HashiCorp Vault's AppRole authentication method, which uses role_id and secret_id credentials to provide secure, machine-to-machine authentication.

### Static vs Dynamic Secrets

**Static Secrets** (KV v2 Secrets Engine):
- Manually configured secrets stored in Vault's key-value store
- Long-lived credentials that are rotated on a schedule
- Examples: Database passwords, API keys, OAuth tokens
- Retrieved via the KV v2 secrets engine at `secret/data/{path}`

**Dynamic Secrets** (PKI, Database, AWS Secrets Engines):
- Automatically generated on-demand by Vault
- Short-lived credentials with automatic expiration
- Automatically revoked after lease expiration
- Examples: TLS certificates, temporary database credentials, cloud service credentials
- Retrieved via specialized secrets engines (PKI, database, AWS, etc.)

This document covers both static secrets management using the KV v2 engine and dynamic secrets management with a specific focus on digital certificate lifecycle using the PKI secrets engine.

## HashiCorp Vault URLs and Endpoints

HashiCorp Vault is accessed via RESTful API endpoints over HTTPS. The following are typical URL patterns used in the InsCore application:

### Base Vault URL
```
https://xe1.vault.aig.net
```
- **Protocol**: HTTPS (TLS 1.2 or higher required)
- **Hostname**: xe1.vault.aig.net (internal DNS name within VPC)

### Environment-Specific URLs

**Development Environment**:
```
https://xe1.vault.aig.net
```

**Staging Environment**:
```
https://xe1.vault.aig.net
```

**Production Environment**:
```
https://pe1.vault.aig.net
```

### Common API Endpoints

**Authentication Endpoint**:
```
POST https://xe1.vault.aig.net/v1/auth/approle/login
```
- Used by Lambda functions to authenticate using AppRole credentials (role_id and secret_id)
- Returns Vault token upon successful authentication

**Secret Read Endpoint** (KV v2 engine):
```
GET https://xe1.vault.aig.net/v1/secret/data/inscore/{environment}/{service}/config
```
- Retrieves secrets for specific service
- Example: `https://xe1.vault.aig.net/v1/secret/data/inscore/prod/api-gateway/credentials`

**Secret Write Endpoint** (Configuration Time):
```
POST https://xe1.vault.aig.net/v1/secret/data/inscore/{environment}/{service}/config
```
- Used to store/update secrets during configuration
- Requires elevated permissions (typically admin or CI/CD service account)

**Token Renewal Endpoint**:
```
POST https://xe1.vault.aig.net/v1/auth/token/renew-self
```
- Extends the TTL of the current token

**Health Check Endpoint**:
```
GET https://xe1.vault.aig.net/v1/sys/health
```
- Returns Vault cluster health status
- Used for monitoring and health checks

### URL Path Structure

Vault organizes secrets in a hierarchical path structure:

```
/v1/secret/data/{organization}/{environment}/{service}/{secret-type}
```

**Example Paths**:
- Database credentials: `/v1/secret/data/inscore/prod/rds/db-credentials`
- API keys: `/v1/secret/data/inscore/prod/third-party/api-keys`
- OAuth tokens: `/v1/secret/data/inscore/prod/auth/oauth-config`
- Service credentials: `/v1/secret/data/inscore/prod/lambda/service-accounts`

### Connection Configuration

Lambda functions configure the Vault client using environment variables:

```json
{
  "vault_addr": "https://xe1.vault.aig.net",
  "vault_namespace": "inscore",
  "vault_auth_provider": "approle",
  "vault_auth_role": "7454-inscore",
  "auth_mount_path": "auth/approle",
  "vault_secret_path": "/v1/secret/data/inscore/prod/api-gateway",
  "vault_secret_file": "/tmp/creds.json",
  "approle_secret_name": "inscore/vault/approle-credentials"
}
```

**Configuration Mapping to Environment Variables**:

| Configuration Parameter | Environment Variable | Example Value |
|------------------------|---------------------|---------------|
| vault_addr | VAULT_ADDR | https://xe1.vault.aig.net |
| vault_namespace | VAULT_NAMESPACE | inscore |
| vault_auth_provider | VAULT_AUTH_PROVIDER | approle |
| vault_auth_role | VAULT_AUTH_ROLE | 7454-inscore |
| vault_secret_path | VAULT_SECRET_PATH | /v1/secret/data/inscore/prod/api-gateway |
| vault_secret_file | VAULT_SECRET_FILE | /tmp/creds.json |
| approle_secret_name | APPROLE_SECRET_NAME | inscore/vault/approle-credentials |

**Complete Configuration Example**:
```javascript
// Vault client configuration from environment variables
const config = {
    vaultAddr: process.env.VAULT_ADDR,  // Base URL
    namespace: process.env.VAULT_NAMESPACE,  // Namespace for multi-tenancy
    authProvider: process.env.VAULT_AUTH_PROVIDER,  // Authentication method
    authRole: process.env.VAULT_AUTH_ROLE,  // Vault role name
    secretPath: process.env.VAULT_SECRET_PATH,  // Path to secrets in Vault
    secretFile: process.env.VAULT_SECRET_FILE || '/tmp/vault/secret.json',  // Local cache file
    approleSecretName: process.env.APPROLE_SECRET_NAME  // AWS Secrets Manager secret name
};
```

## Secret Configuration and Provisioning

This section describes how secrets are stored in HashiCorp Vault during configuration time, before Lambda functions need to access them at runtime.

### Overview

Secret provisioning is a **configuration-time activity** performed by authorized administrators, DevOps teams, or CI/CD pipelines. This is separate from runtime secret retrieval by Lambda functions. The provisioning process ensures that secrets are securely stored in Vault before application deployment.

### Provisioning Methods

#### 1. Manual Provisioning via Vault CLI

Authorized administrators can use the Vault CLI to manually store secrets:

**Prerequisites**:
- Vault CLI installed on administrative workstation
- Vault admin token or user credentials with write permissions
- Network access to Vault cluster

**Example: Storing Database Credentials**
```bash
# Authenticate to Vault (using token or user credentials)
export VAULT_ADDR="https://xe1.vault.aig.net"
export VAULT_TOKEN="s.xxxxxxxxxxxxxx"  # Admin token

# Write secret to Vault
vault kv put secret/inscore/prod/rds/db-credentials \
  username="inscore_db_user" \
  password="SecureP@ssw0rd123" \
  host="inscore-prod-rds.cluster-xxxxx.us-east-1.rds.amazonaws.com" \
  port="5432" \
  database="inscore_db"

# Verify secret was written
vault kv get secret/inscore/prod/rds/db-credentials
```

**Example: Storing API Keys**
```bash
vault kv put secret/inscore/prod/third-party/api-keys \
  gdms_api_key="gdms_live_xxxxxxxxxxxx" \
  payment_gateway_key="pk_live_xxxxxxxxxxxx" \
  analytics_token="analytics_xxxxxxxxxxxx"
```

#### 2. Automated Provisioning via CI/CD Pipeline

**Architecture**:
```
CI/CD Pipeline (GitHub Actions/Jenkins)
  ↓
CI/CD Service Account (with Vault write permissions)
  ↓
Vault API (HTTPS)
  ↓
Vault Storage Backend
```

**GitHub Actions Example**:
```yaml
name: Provision Vault Secrets

on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Target environment'
        required: true
        type: choice
        options:
          - dev
          - staging
          - prod

jobs:
  provision-secrets:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Authenticate to Vault
        id: vault-auth
        run: |
          # Authenticate using GitHub OIDC or AppRole
          VAULT_TOKEN=$(curl -s --request POST \
            --data '{"role":"cicd-provisioner","jwt":"'${{ secrets.GITHUB_TOKEN }}'"}' \
            https://xe1.vault.aig.net/v1/auth/jwt/login | jq -r '.auth.client_token')
          echo "::add-mask::$VAULT_TOKEN"
          echo "VAULT_TOKEN=$VAULT_TOKEN" >> $GITHUB_ENV

      - name: Write secrets to Vault
        run: |
          # Read secrets from secure parameter store or secret manager
          DB_PASSWORD="${{ secrets.DB_PASSWORD }}"
          API_KEY="${{ secrets.API_KEY }}"

          # Write to Vault
          curl --request POST \
            --header "X-Vault-Token: $VAULT_TOKEN" \
            --data '{
              "data": {
                "username": "inscore_db_user",
                "password": "'$DB_PASSWORD'",
                "host": "inscore-${{ inputs.environment }}-rds.cluster.us-east-1.rds.amazonaws.com",
                "port": "5432"
              }
            }' \
            https://xe1.vault.aig.net/v1/secret/data/inscore/${{ inputs.environment }}/rds/db-credentials

      - name: Verify provisioning
        run: |
          # Verify secret exists (without reading sensitive data)
          curl --header "X-Vault-Token: $VAULT_TOKEN" \
            https://xe1.vault.aig.net/v1/secret/metadata/inscore/${{ inputs.environment }}/rds/db-credentials
```

#### 3. Terraform-Based Provisioning

**Terraform Configuration**:
```hcl
# Configure Vault provider
provider "vault" {
  address = "https://xe1.vault.aig.net"
  token   = var.vault_token  # Or use AWS auth
}

# Create KV v2 secret
resource "vault_kv_secret_v2" "db_credentials" {
  mount = "secret"
  name  = "inscore/prod/rds/db-credentials"

  data_json = jsonencode({
    username = "inscore_db_user"
    password = var.db_password  # From Terraform variables
    host     = aws_rds_cluster.main.endpoint
    port     = "5432"
    database = "inscore_db"
  })
}

# Create API keys secret
resource "vault_kv_secret_v2" "api_keys" {
  mount = "secret"
  name  = "inscore/prod/third-party/api-keys"

  data_json = jsonencode({
    gdms_api_key         = var.gdms_api_key
    payment_gateway_key  = var.payment_gateway_key
    analytics_token      = var.analytics_token
  })
}

# Create Vault policy for Lambda access
resource "vault_policy" "lambda_read_policy" {
  name = "inscore-lambda-read"

  policy = <<EOT
# Allow reading secrets for InsCore Lambda functions
path "secret/data/inscore/prod/*" {
  capabilities = ["read"]
}

# Allow token renewal
path "auth/token/renew-self" {
  capabilities = ["update"]
}
EOT
}
```

### Secret Organization Best Practices

**Path Hierarchy**:
```
secret/
├── inscore/
│   ├── dev/
│   │   ├── rds/
│   │   │   └── db-credentials
│   │   ├── third-party/
│   │   │   └── api-keys
│   │   └── lambda/
│   │       └── service-accounts
│   ├── staging/
│   │   └── [same structure]
│   └── prod/
│       ├── rds/
│       │   └── db-credentials
│       ├── third-party/
│       │   └── api-keys
│       ├── lambda/
│       │   └── service-accounts
│       └── auth/
│           └── oauth-config
```

### Access Control for Provisioning

**Required Permissions** (Vault Policy):
```hcl
# Policy for secret provisioning (CI/CD or Admin)
path "secret/data/inscore/*" {
  capabilities = ["create", "update", "delete"]
}

path "secret/metadata/inscore/*" {
  capabilities = ["list", "read", "delete"]
}

# Ability to read existing secrets for updates
path "secret/data/inscore/*" {
  capabilities = ["read"]
}
```

### Secret Versioning

Vault KV v2 engine automatically versions secrets:
- Each update creates a new version
- Previous versions are retained (configurable retention)
- Rollback capability to previous versions
- Audit trail of all changes

**View Secret Versions**:
```bash
vault kv metadata get secret/inscore/prod/rds/db-credentials
```

**Read Specific Version**:
```bash
vault kv get -version=2 secret/inscore/prod/rds/db-credentials
```

### Rotation and Updates

**Update Existing Secret**:
```bash
# This creates a new version, old version is retained
vault kv put secret/inscore/prod/rds/db-credentials \
  username="inscore_db_user" \
  password="NewSecureP@ssw0rd456" \
  host="inscore-prod-rds.cluster-xxxxx.us-east-1.rds.amazonaws.com" \
  port="5432"
```

**Automated Rotation** (future enhancement):
- Vault can integrate with databases for automatic credential rotation
- Lambda functions always retrieve latest version
- Old credentials are revoked after rotation period

## AppRole Authentication Method

The Vault Lambda Extension uses HashiCorp Vault's AppRole authentication method, which is designed for machine-to-machine authentication. AppRole uses role_id and secret_id credentials to authenticate applications and services without requiring human interaction. This authentication mechanism provides a secure, automated authentication flow suitable for Lambda functions and other automated workloads.

### Key Components

#### AppRole Credentials

**Role ID**:
- Unique identifier for the AppRole
- Functions similar to a username
- Not sensitive and can be stored in application configuration
- Example: `044bbe3e-a4fe-b238-5d8e-926865a1dc6d`
- Publicly accessible and used to identify the authentication role

**Secret ID**:
- Secret credential associated with the Role ID
- Functions similar to a password
- Sensitive credential that must be protected
- Example: `06123feb-6e21-968d-b2a0-2a5e26b4b38f`
- Should be securely stored and rotated periodically
- Can be bound to CIDR ranges or have usage limits

#### Credential Storage Options

**AWS Secrets Manager** (Recommended):
- Store secret_id in AWS Secrets Manager
- Lambda retrieves secret_id at runtime using IAM permissions
- Automatic rotation support
- Audit logging of secret access

**Environment Variables** (Less Secure):
- Store role_id and secret_id as Lambda environment variables
- Use AWS KMS encryption for environment variables
- Suitable for development environments

**Lambda Extension Layer**:
- Package credentials securely within Lambda layer
- Retrieve credentials during Lambda initialization
- Implement credential caching for performance

### Vault AppRole Configuration

**Auth Method Mount Path**: `auth/approle`
- AppRole authentication method enabled at this path
- All AppRole authentication requests target this endpoint

**AppRole Role**: `7454-inscore`
- Vault AppRole role configured for InsCore Lambda access
- Defines authentication policies and token parameters
- Role name returned in token metadata

**Role Configuration**:
```hcl
# Create AppRole role in Vault
vault write auth/approle/role/7454-inscore \
    token_policies="7454-inscore-write,default,token_update" \
    token_ttl=8h \
    token_max_ttl=24h \
    secret_id_ttl=0 \
    secret_id_num_uses=0 \
    token_type="service"
```

**Configuration Parameters**:
- `token_policies`: Policies attached to generated tokens (7454-inscore-write, default, token_update)
- `token_ttl`: Token time-to-live (28800 seconds / 8 hours)
- `token_max_ttl`: Maximum token lifetime including renewals (24 hours)
- `secret_id_ttl`: Secret ID expiration time (0 = never expires)
- `secret_id_num_uses`: Number of times secret_id can be used (0 = unlimited)
- `token_type`: Type of token issued (service = long-lived, batch = lightweight)

### AppRole Authentication Response

When Lambda successfully authenticates using AppRole, Vault returns a response containing:

**Response Structure** (based on actual Vault response):
```json
{
  "request_id": "28a41325-afc4-7ac5-ddbd-bee61c021ba7",
  "lease_id": "",
  "renewable": false,
  "lease_duration": 0,
  "data": null,
  "auth": {
    "client_token": "hvs.CAESILsszQYzqeWSBouny_-cZEip6-vpgP5QQez0Y5F0BjfgGiUKHGh2cy5nNzJZelhuNE1IUEZ4MDNPcjZRMk5uT2cQ0c6CRhgG",
    "accessor": "TGA01Lk3NjluKUXVm7PyBXhG",
    "policies": ["7454-inscore-write", "default", "token_update"],
    "token_policies": ["7454-inscore-write", "default", "token_update"],
    "metadata": {
      "role_name": "7454-inscore"
    },
    "lease_duration": 28800,
    "renewable": true,
    "entity_id": "9258532e-872e-fa96-a07f-df878d7421a5",
    "token_type": "service",
    "orphan": true,
    "num_uses": 0
  }
}
```

**Response Fields Explained**:

- **client_token**: The Vault token used for subsequent API requests (must be included in `X-Vault-Token` header)
- **accessor**: Token accessor for token management operations without exposing the token itself
- **policies**: List of Vault policies attached to the token, defining access permissions
- **token_policies**: Policies directly attached (distinct from identity policies)
- **metadata.role_name**: Name of the AppRole used for authentication ("7454-inscore")
- **lease_duration**: Token validity period in seconds (28800 = 8 hours)
- **renewable**: Whether the token can be renewed before expiration (true)
- **entity_id**: Unique identifier for the authenticated entity
- **token_type**: Type of token ("service" = full-featured, "batch" = lightweight)
- **orphan**: Token has no parent (true for AppRole tokens)
- **num_uses**: Remaining token uses (0 = unlimited)

### Security Considerations for AppRole

**Secret ID Protection**:
- Never commit secret_id to version control
- Use AWS Secrets Manager or Parameter Store for secret_id storage
- Rotate secret_id regularly (recommended: every 90 days)
- Implement secret_id usage limits or TTL for enhanced security

**Role ID Management**:
- Role ID is not sensitive but should not be publicly exposed
- Store role_id as Lambda environment variable or configuration
- Use different AppRoles for different environments (dev, staging, prod)

**Network Security**:
- Bind secret_id to CIDR ranges matching Lambda VPC
- Use VPC endpoints for Vault communication
- Enable TLS for all Vault API calls

**Audit and Monitoring**:
- Enable Vault audit logging for all authentication attempts
- Monitor failed authentication attempts
- Alert on unusual token usage patterns
- Track token renewal and expiration events

## Secure Access Flow

The authentication flow follows a streamlined three-step process using AppRole credentials that ensures secure access to Vault secrets:

### Step 1: Lambda Retrieves AppRole Credentials

The Lambda function retrieves its AppRole credentials during initialization:

**Credential Retrieval Options**:

**Option A: AWS Secrets Manager (Recommended)**:
1. Lambda uses its IAM execution role to call AWS Secrets Manager
2. Retrieves secret containing both role_id and secret_id
3. Credentials are decrypted automatically using AWS KMS
4. Provides audit trail of credential access

```javascript
// Retrieve credentials from AWS Secrets Manager
const secretsManager = new AWS.SecretsManager();
const secretData = await secretsManager.getSecretValue({
  SecretId: 'inscore/vault/approle-credentials'
}).promise();

const credentials = JSON.parse(secretData.SecretString);
const roleId = credentials.role_id;
const secretId = credentials.secret_id;
```

**Option B: Environment Variables**:
1. role_id and secret_id stored as encrypted Lambda environment variables
2. AWS KMS decrypts environment variables at runtime
3. Lambda reads credentials from process.env

```javascript
// Retrieve credentials from environment variables
const roleId = process.env.VAULT_ROLE_ID;
const secretId = process.env.VAULT_SECRET_ID;
```

**Security Considerations**:
- secret_id should never be hardcoded in Lambda function code
- Use AWS Secrets Manager for production environments
- Environment variables are acceptable for development/testing
- Implement credential rotation for long-lived Lambda functions

### Step 2: Lambda Authenticates with Vault Using AppRole

The Lambda function (via vault-lambda-extension client) sends authentication request to Vault:

**Authentication Request**:
- **Endpoint**: `POST https://xe1.vault.aig.net/v1/auth/approle/login`
- **Headers**: `Content-Type: application/json`
- **Body**:
```json
{
  "role_id": "044bbe3e-a4fe-b238-5d8e-926865a1dc6d",
  "secret_id": "06123feb-6e21-968d-b2a0-2a5e26b4b38f"
}
```

**Vault Validation Process**:
1. Vault receives the authentication request
2. Validates that the role_id corresponds to a configured AppRole
3. Verifies the secret_id is valid and not expired
4. Checks secret_id usage limits (if configured)
5. Validates any CIDR binding constraints (if configured)
6. Confirms the AppRole is enabled and not deleted

**Validation Checks**:
- Role ID exists in Vault's AppRole backend
- Secret ID is valid and matches the Role ID
- Secret ID has not exceeded usage limit (num_uses)
- Secret ID has not expired (secret_id_ttl)
- Request originates from allowed CIDR range (if bound_cidr_list configured)
- AppRole role is active and not disabled

### Step 3: Vault Issues Token and Lambda Accesses Secrets

Upon successful authentication, Vault issues a token and the Lambda function can access secrets:

**Vault Authentication Response**:
```json
{
  "request_id": "28a41325-afc4-7ac5-ddbd-bee61c021ba7",
  "auth": {
    "client_token": "hvs.CAESILsszQYzqeWSBouny_-cZEip6-vpgP5QQez0Y5F0BjfgGiUKHGh2cy5nNzJZelhuNE1IUEZ4MDNPcjZRMk5uT2cQ0c6CRhgG",
    "accessor": "TGA01Lk3NjluKUXVm7PyBXhG",
    "policies": ["7454-inscore-write", "default", "token_update"],
    "token_policies": ["7454-inscore-write", "default", "token_update"],
    "metadata": {
      "role_name": "7454-inscore"
    },
    "lease_duration": 28800,
    "renewable": true,
    "entity_id": "9258532e-872e-fa96-a07f-df878d7421a5",
    "token_type": "service",
    "orphan": true,
    "num_uses": 0
  }
}
```

**Token Usage**:
1. Lambda extracts `client_token` from response
2. Caches token in memory for subsequent requests
3. Uses token in `X-Vault-Token` header for all secret retrieval requests
4. Monitors token lease_duration (28800 seconds / 8 hours)
5. Renews token before expiration if renewable=true

**Secret Retrieval with Token**:
```bash
# Example: Retrieve database credentials
curl --header "X-Vault-Token: hvs.CAESIL..." \
  https://xe1.vault.aig.net/v1/secret/data/inscore/prod/rds/db-credentials
```

**Token Lifecycle Management**:
- **Initial TTL**: 8 hours (28800 seconds)
- **Renewable**: Yes (can extend token lifetime)
- **Max TTL**: 24 hours (configured in AppRole)
- **Auto-renewal**: Lambda extension automatically renews token before expiration
- **Re-authentication**: If token expires or renewal fails, Lambda re-authenticates with role_id/secret_id

**Token Renewal**:
```bash
# Renew token before expiration
POST https://xe1.vault.aig.net/v1/auth/token/renew-self
X-Vault-Token: hvs.CAESIL...
{
  "increment": 28800
}
```

**In essence**: Lambda retrieves credentials → Lambda authenticates with Vault using AppRole → Vault validates and issues token → Lambda accesses secrets using token.

## Vault Authentication Sequence Diagram

```mermaid
sequenceDiagram
    participant LF as Lambda Function
    participant SM as AWS Secrets Manager
    participant VLE as Vault Lambda Extension<br/>(Client)
    participant V as HashiCorp Vault<br/>(AppRole Auth)

    Note over LF,V: AppRole Authentication Flow

    LF->>VLE: Initialize and request secrets

    Note over VLE: Step 1: Retrieve AppRole Credentials
    VLE->>SM: Get AppRole credentials
    Note over VLE,SM: Lambda uses IAM role to access Secrets Manager
    SM-->>VLE: Return role_id and secret_id
    Note over VLE: role_id: 044bbe3e-a4fe-b238-5d8e-926865a1dc6d<br/>secret_id: 06123feb-6e21-968d-b2a0-2a5e26b4b38f

    Note over VLE: Step 2: Authenticate with Vault
    VLE->>V: POST /v1/auth/approle/login
    Note over VLE,V: Body: { "role_id": "...", "secret_id": "..." }

    Note over V: Vault Validates Credentials
    V->>V: Validate role_id exists
    V->>V: Verify secret_id is valid
    V->>V: Check secret_id not expired
    V->>V: Verify usage limits not exceeded
    V->>V: Validate CIDR constraints (if configured)

    Note over V: Step 3: Issue Vault Token
    V->>V: Generate client_token
    V->>V: Attach policies: 7454-inscore-write, default, token_update
    V->>V: Set lease_duration: 28800s (8 hours)

    V-->>VLE: Return authentication response
    Note over V,VLE: Response includes:<br/>- client_token<br/>- policies<br/>- lease_duration: 28800s<br/>- renewable: true

    VLE->>VLE: Cache client_token in memory
    VLE->>VLE: Store token expiration time

    Note over VLE: Access Secrets Using Token
    VLE->>V: GET /v1/secret/data/inscore/prod/...
    Note over VLE,V: Header: X-Vault-Token: hvs.CAESIL...

    V->>V: Validate token
    V->>V: Check token not expired
    V->>V: Verify policies allow secret access

    V-->>VLE: Return secret data
    Note over V,VLE: Database credentials, API keys, etc.

    VLE-->>LF: Provide secrets to Lambda function

    Note over LF: Lambda uses secrets for application logic

    Note over VLE: Token Renewal (before expiration)
    VLE->>V: POST /v1/auth/token/renew-self
    Note over VLE,V: Header: X-Vault-Token: hvs.CAESIL...
    V-->>VLE: Token renewed, new lease_duration

    Note over LF,V: Lambda has secure access to secrets via Vault token
```

## Runtime Secret Access by Lambda Functions

This section describes how Lambda functions access secrets from Vault during application runtime after authentication is complete.

### Secret Access Flow

Once a Lambda function has successfully authenticated with Vault and obtained a Vault token, it can retrieve secrets using the following process:

#### 1. Token Caching and Management

The vault-lambda-extension caches the Vault token to avoid repeated authentication:
- Token is cached in memory within the Lambda execution context
- Token is reused for subsequent secret retrievals until it expires
- Extension monitors token TTL and automatically renews before expiration
- If renewal fails or token expires, extension re-authenticates automatically

#### 2. Secret Retrieval Pattern

**Synchronous Retrieval** (recommended for Lambda cold start):
```
Lambda Init Phase:
  ↓
Authenticate with Vault
  ↓
Retrieve All Required Secrets
  ↓
Cache Secrets in Memory
  ↓
Lambda Ready to Process Requests
```

**Lazy Loading** (on-demand retrieval):
```
Lambda Request Received
  ↓
Check if Secret in Cache
  ↓
If Not Cached → Retrieve from Vault
  ↓
Cache Secret in Memory
  ↓
Use Secret for Request Processing
```

### Runtime Access Examples

#### Node.js Lambda Function with Vault Extension

**Example 1: Basic Secret Retrieval**
```javascript
const AWS = require('aws-sdk');
const vault = require('node-vault');

class VaultClient {
    constructor() {
        this.vaultAddr = process.env.VAULT_ADDR;  // https://xe1.vault.aig.net or https://pe1.vault.aig.net
        this.authProvider = process.env.VAULT_AUTH_PROVIDER || 'approle';  // approle or aws
        this.authRole = process.env.VAULT_AUTH_ROLE;  // e.g., 7454-inscore
        this.secretPath = process.env.VAULT_SECRET_PATH;  // e.g., /v1/secret/data/inscore/prod/api-gateway
        this.secretFile = process.env.VAULT_SECRET_FILE || '/tmp/vault/secret.json';  // Default: /tmp/vault/secret.json
        this.namespace = process.env.VAULT_NAMESPACE || 'inscore';
        this.roleId = null;
        this.secretId = null;
        this.client = null;
        this.token = null;
        this.secretsCache = {};
    }

    /**
     * Retrieve AppRole credentials from AWS Secrets Manager
     */
    async getAppRoleCredentials() {
        const approleSecretName = process.env.APPROLE_SECRET_NAME;

        // If APPROLE_SECRET_NAME is configured, retrieve from Secrets Manager
        if (approleSecretName) {
            const secretsManager = new AWS.SecretsManager();

            try {
                const secretData = await secretsManager.getSecretValue({
                    SecretId: approleSecretName
                }).promise();

                const credentials = JSON.parse(secretData.SecretString);
                this.roleId = credentials.role_id;
                this.secretId = credentials.secret_id;

                console.log(`Successfully retrieved AppRole credentials from Secrets Manager: ${approleSecretName}`);
                return true;

            } catch (error) {
                console.error('Failed to retrieve AppRole credentials from Secrets Manager:', error);
                // Fall through to environment variable fallback
            }
        }

        // Fallback to environment variables
        this.roleId = process.env.VAULT_ROLE_ID;
        this.secretId = process.env.VAULT_SECRET_ID;

        if (!this.roleId || !this.secretId) {
            throw new Error('AppRole credentials not found. Configure APPROLE_SECRET_NAME or set VAULT_ROLE_ID and VAULT_SECRET_ID environment variables');
        }

        console.log('Using AppRole credentials from environment variables');
        return true;
    }

    async authenticate() {
        try {
            // Retrieve AppRole credentials
            await this.getAppRoleCredentials();

            // Initialize Vault client
            this.client = vault({
                apiVersion: 'v1',
                endpoint: this.vaultAddr
            });

            // Authenticate with Vault using AppRole
            const authResponse = await this.client.approleLogin({
                role_id: this.roleId,
                secret_id: this.secretId
            });

            // Store token
            this.token = authResponse.auth.client_token;
            this.client.token = this.token;

            console.log(`Successfully authenticated with Vault. Token TTL: ${authResponse.auth.lease_duration}s`);
            console.log(`Policies: ${authResponse.auth.policies.join(', ')}`);
            console.log(`Role: ${authResponse.auth.metadata.role_name}`);
            return true;

        } catch (error) {
            console.error('Vault authentication failed:', error);
            return false;
        }
    }

    async getSecret(secretPath) {
        // Check cache first
        if (this.secretsCache[secretPath]) {
            return this.secretsCache[secretPath];
        }

        try {
            // Ensure authenticated
            if (!this.client || !this.token) {
                await this.authenticate();
            }

            // Determine the full path - if secretPath already starts with /v1/, use as-is
            // Otherwise, construct the path
            let fullPath = secretPath;
            if (!secretPath.startsWith('/v1/')) {
                fullPath = `secret/data/${secretPath}`;
            } else {
                // Remove /v1/ prefix for node-vault library
                fullPath = secretPath.substring(4);
            }

            // Read secret from Vault (KV v2 engine)
            const secretResponse = await this.client.read(fullPath);
            const secretData = secretResponse.data.data;

            // Cache the secret in memory
            this.secretsCache[secretPath] = secretData;

            console.log(`Successfully retrieved secret: ${secretPath}`);
            return secretData;

        } catch (error) {
            console.error(`Error retrieving secret ${secretPath}:`, error);
            throw error;
        }
    }

    /**
     * Write secrets to file as specified by VAULT_SECRET_FILE
     */
    async writeSecretsToFile(secrets) {
        const fs = require('fs').promises;
        const path = require('path');

        try {
            // Ensure directory exists
            const dir = path.dirname(this.secretFile);
            await fs.mkdir(dir, { recursive: true });

            // Write secrets to file
            await fs.writeFile(this.secretFile, JSON.stringify(secrets, null, 2));

            console.log(`Successfully wrote secrets to ${this.secretFile}`);
            return true;

        } catch (error) {
            console.error(`Error writing secrets to file ${this.secretFile}:`, error);
            throw error;
        }
    }

    /**
     * Read secrets from file if it exists
     */
    async readSecretsFromFile() {
        const fs = require('fs').promises;

        try {
            const fileContent = await fs.readFile(this.secretFile, 'utf8');
            const secrets = JSON.parse(fileContent);

            console.log(`Successfully read secrets from ${this.secretFile}`);
            return secrets;

        } catch (error) {
            if (error.code === 'ENOENT') {
                console.log(`Secrets file not found: ${this.secretFile}`);
                return null;
            }
            console.error(`Error reading secrets from file ${this.secretFile}:`, error);
            throw error;
        }
    }

    /**
     * Retrieve secrets using VAULT_SECRET_PATH and cache to file
     */
    async retrieveAndCacheSecrets() {
        if (!this.secretPath) {
            throw new Error('VAULT_SECRET_PATH environment variable not set');
        }

        // Try to read from file first
        const cachedSecrets = await this.readSecretsFromFile();
        if (cachedSecrets) {
            console.log('Using cached secrets from file');
            return cachedSecrets;
        }

        // Retrieve from Vault
        const secrets = await this.getSecret(this.secretPath);

        // Write to file for caching
        await this.writeSecretsToFile(secrets);

        return secrets;
    }

    async getDbCredentials(environment) {
        const path = `inscore/${environment}/rds/db-credentials`;
        return this.getSecret(path);
    }

    async getApiKeys(environment) {
        const path = `inscore/${environment}/third-party/api-keys`;
        return this.getSecret(path);
    }
}

// Initialize Vault client globally (reused across Lambda invocations)
let vaultClient = null;

exports.handler = async (event, context) => {
    // Initialize Vault client on cold start
    if (!vaultClient) {
        vaultClient = new VaultClient();
        await vaultClient.authenticate();
    }

    // Retrieve secrets
    try {
        // Option 1: Use VAULT_SECRET_PATH environment variable (recommended)
        // This retrieves secrets from the path configured in VAULT_SECRET_PATH
        // and caches them to VAULT_SECRET_FILE
        const secrets = await vaultClient.retrieveAndCacheSecrets();
        console.log('Retrieved secrets from Vault');

        // Access secrets from the retrieved data
        // Example: secrets might contain { db_username, db_password, api_key, etc. }
        const dbUsername = secrets.db_username;
        const dbPassword = secrets.db_password;
        const apiKey = secrets.api_key;

        // Option 2: Retrieve specific secrets by path (alternative approach)
        // const environment = process.env.ENVIRONMENT || 'prod';
        // const dbCreds = await vaultClient.getDbCredentials(environment);
        // const apiKeys = await vaultClient.getApiKeys(environment);

        // Use secrets to process request
        // ... application logic here ...

        return {
            statusCode: 200,
            body: JSON.stringify({
                message: 'Success',
                secretsLoaded: true,
                secretFile: vaultClient.secretFile
            })
        };

    } catch (error) {
        console.error('Error:', error);
        return {
            statusCode: 500,
            body: JSON.stringify({
                error: 'Internal server error',
                message: error.message
            })
        };
    }
};
```

**Example 2: With Token Renewal**
```javascript
class VaultClientWithRenewal extends VaultClient {
    constructor() {
        super();
        this.tokenExpiry = null;
        this.tokenRenewable = false;
    }

    async authenticate() {
        const success = await super.authenticate();

        if (success) {
            // Calculate token expiry time
            const tokenInfo = await this.client.tokenLookupSelf();
            const leaseDuration = tokenInfo.data.ttl;
            this.tokenExpiry = new Date(Date.now() + (leaseDuration * 1000));
            this.tokenRenewable = tokenInfo.data.renewable;
            return true;
        }
        return false;
    }

    async ensureValidToken() {
        if (!this.token || !this.tokenExpiry) {
            await this.authenticate();
            return;
        }

        // Renew token if within 5 minutes of expiry
        const timeUntilExpiry = (this.tokenExpiry - Date.now()) / 1000;

        if (timeUntilExpiry < 300) {  // Less than 5 minutes
            if (this.tokenRenewable) {
                try {
                    const response = await this.client.tokenRenewSelf({ increment: 3600 });
                    const leaseDuration = response.auth.lease_duration;
                    this.tokenExpiry = new Date(Date.now() + (leaseDuration * 1000));
                    console.log(`Token renewed. New TTL: ${leaseDuration}s`);
                } catch (error) {
                    console.error('Token renewal failed, re-authenticating:', error);
                    await this.authenticate();
                }
            } else {
                // Token not renewable, re-authenticate
                console.log('Token not renewable, re-authenticating');
                await this.authenticate();
            }
        }
    }

    async getSecret(secretPath) {
        // Ensure token is valid before retrieving secret
        await this.ensureValidToken();

        // Call parent method to retrieve secret
        return super.getSecret(secretPath);
    }
}
```


### Lambda Environment Variables

Lambda functions require the following environment variables for Vault access:

| Environment Variable | Sample Value(s) | Description |
|---------------------|-----------------|-------------|
| **VAULT_ADDR** | `https://xe1.vault.aig.net`<br/>`https://pe1.vault.aig.net` | **XE1**: NonProd Vault<br/>**PE1**: Prod Vault<br/>Base URL for HashiCorp Vault API |
| **VAULT_AUTH_PROVIDER** | `approle` | Name of the authentication method to use<br/>Supported values: `approle`, `aws`<br/>Use `approle` for AppRole authentication |
| **VAULT_AUTH_ROLE** | `7454-hip-local`<br/>`7454-inscore` | Name of the Vault role for this Lambda function<br/>Reach out to Vault Team/Cloud Operations to get this created<br/>For AppRole: This is the AppRole role name configured in Vault |
| **VAULT_SECRET_PATH** | `/v1/secret/data/inscore`<br/>`/v1/secret/data/inscore/prod/api-gateway` | Path of your secrets in Vault<br/>Must include the full API path including `/v1/secret/data/` prefix<br/>Example: `/v1/secret/data/inscore/prod/rds/db-credentials` |
| **VAULT_SECRET_FILE** | `/tmp/creds.json` | Path where secrets are written to disk after fetching from Vault<br/>If not set, defaults to `/tmp/vault/secret.json`<br/>Used for caching secrets locally during Lambda execution |
| **VAULT_NAMESPACE** | `inscore` | Vault namespace for multi-tenancy<br/>Optional: Required only if Vault Enterprise namespaces are used |
| **APPROLE_SECRET_NAME** | `inscore/vault/approle-credentials` | AWS Secrets Manager secret name containing AppRole credentials<br/>Secret should contain JSON with `role_id` and `secret_id` fields<br/>**Recommended for production environments** |

**Environment-Specific Configuration Examples**:

**Development Environment**:
```bash
VAULT_ADDR=https://xe1.vault.aig.net
VAULT_AUTH_PROVIDER=approle
VAULT_AUTH_ROLE=7454-inscore
VAULT_SECRET_PATH=/v1/secret/data/inscore/dev/api-gateway
VAULT_SECRET_FILE=/tmp/creds.json
VAULT_NAMESPACE=inscore
APPROLE_SECRET_NAME=inscore/vault/approle-credentials-dev
```

**Production Environment**:
```bash
VAULT_ADDR=https://pe1.vault.aig.net
VAULT_AUTH_PROVIDER=approle
VAULT_AUTH_ROLE=7454-inscore
VAULT_SECRET_PATH=/v1/secret/data/inscore/prod/api-gateway
VAULT_SECRET_FILE=/tmp/creds.json
VAULT_NAMESPACE=inscore
APPROLE_SECRET_NAME=inscore/vault/approle-credentials-prod
```

**Alternative: Direct Credential Storage (Less Secure)**:
```bash
# Not recommended for production
VAULT_ROLE_ID=044bbe3e-a4fe-b238-5d8e-926865a1dc6d
VAULT_SECRET_ID=06123feb-6e21-968d-b2a0-2a5e26b4b38f
```

**Security Best Practices**:
- Use `APPROLE_SECRET_NAME` pointing to AWS Secrets Manager for production
- AWS Secrets Manager secret contains both `role_id` and `secret_id`
- Lambda IAM execution role has permissions to read from Secrets Manager
- Provides audit trail and supports automatic rotation
- Encrypt environment variables using AWS KMS
- Use different AppRole roles for different environments (dev, staging, prod)

### Secret Caching Strategy

The vault-lambda-extension supports both **in-memory caching** and **file-based caching** to optimize performance and reduce Vault API calls.

**Benefits of Caching**:
- Reduces latency for subsequent requests
- Minimizes Vault API calls to Vault cluster
- Improves Lambda performance and cold start times
- Reduces costs (fewer Vault requests)
- Enables offline access to secrets during transient network issues

**Cache Layers**:

#### 1. File-Based Cache (VAULT_SECRET_FILE)

Secrets are written to the file system path specified by `VAULT_SECRET_FILE` (default: `/tmp/vault/secret.json`).

**File Cache Characteristics**:
- Persists across Lambda warm invocations
- Stored in `/tmp` directory (512 MB - 10 GB available in Lambda)
- Survives multiple Lambda invocations within same execution context
- Automatically cleared when Lambda container is recycled
- First invocation fetches from Vault and writes to file
- Subsequent invocations read from file (faster)

**File Cache Flow**:
```
First Invocation (Cold Start):
  Lambda Init → Authenticate with Vault → Fetch Secrets → Write to /tmp/creds.json → Return Secrets

Subsequent Invocations (Warm Container):
  Lambda Invoke → Read from /tmp/creds.json → Return Cached Secrets (no Vault call)

Container Recycle:
  /tmp directory cleared → Next invocation repeats cold start process
```

**Example File Contents** (`/tmp/creds.json`):
```json
{
  "db_username": "inscore_db_user",
  "db_password": "SecureP@ssw0rd123",
  "db_host": "inscore-prod-rds.cluster-xxxxx.us-east-1.rds.amazonaws.com",
  "db_port": "5432",
  "api_key": "gdms_live_xxxxxxxxxxxx",
  "oauth_token": "oauth_token_xxxxxxxxxxxx"
}
```

#### 2. In-Memory Cache

Secrets are also cached in the Lambda function's memory for the lifetime of the execution context.

**In-Memory Cache Characteristics**:
- Fastest access (no I/O operations)
- Cleared on Lambda container restart
- Stored in JavaScript object/Map for quick lookup
- Useful for frequently accessed secrets within same invocation
- Lower memory footprint than file cache

**Cache Invalidation**:
- **File Cache**: Cleared on Lambda container restart/recycle
- **In-Memory Cache**: Cleared on Lambda container restart
- **Manual Invalidation**: Delete file at `VAULT_SECRET_FILE` path to force refresh
- **TTL-based Expiry**: Implement time-based cache expiry for sensitive secrets
- **Event-based Refresh**: Force refresh on specific events (e.g., secret rotation notification)

**Cache Precedence**:
```
1. Check in-memory cache → If exists, return immediately
2. Check file cache (VAULT_SECRET_FILE) → If exists, load to memory and return
3. Fetch from Vault → Cache in memory and file → Return to caller
```

**Performance Impact**:

| Cache Type | Access Time | Vault API Calls | Use Case |
|-----------|-------------|-----------------|----------|
| No Cache | ~200-500ms | Every invocation | Not recommended |
| In-Memory Only | <1ms | First invocation only | Fast, single invocation |
| File Cache | ~5-10ms | First cold start only | Persistent across warm invocations |
| Both Caches | <1ms | First cold start only | **Recommended for production** |

**Security Considerations**:
- Secrets in `/tmp` are not encrypted at rest
- `/tmp` directory is isolated per Lambda execution environment
- Other Lambda functions cannot access your `/tmp` directory
- Secrets are cleared when container is recycled
- For highly sensitive secrets, consider in-memory caching only

### Error Handling

**Common Error Scenarios**:

1. **Authentication Failure**: Retry with exponential backoff, alert if persistent
2. **Token Expiration**: Automatically re-authenticate
3. **Secret Not Found**: Log error, use fallback or fail gracefully
4. **Network Timeout**: Retry with timeout, implement circuit breaker
5. **Vault Service Unavailable**: Use cached secrets if available, alert operations team

## Lambda-to-Lambda Secret Retrieval Pattern

This section describes how a client Lambda function can retrieve secrets from Vault by invoking a dedicated vault-lambda-extension Lambda function.

### Architecture Overview

In this pattern:
- **Client-Lambda**: Application Lambda that needs secrets (e.g., API Gateway Lambda, Business Logic Lambda)
- **vault-lambda-extension Lambda**: Dedicated Lambda function that handles Vault authentication and secret retrieval
- **HashiCorp Vault**: Centralized secrets management service

**Benefits of this Pattern**:
- Centralized Vault authentication logic
- Reusable secret retrieval service across multiple Lambdas
- Simplified client Lambda code (no direct Vault integration needed)
- Consistent error handling and retry logic
- Easier to update Vault authentication mechanism

### Sequence Diagram: Client-Lambda Retrieves API Key

```mermaid
sequenceDiagram
    participant CL as Client-Lambda<br/>(API Gateway)
    participant VLE as vault-lambda-extension<br/>Lambda
    participant SM as AWS Secrets Manager
    participant V as HashiCorp Vault
    participant Cache as File Cache<br/>(/tmp/creds.json)

    Note over CL,V: Client-Lambda requests RDM API key

    CL->>VLE: Invoke Lambda with secret path
    Note over CL,VLE: Payload: {<br/>"secretPath": "/v1/secret/data/inscore/prod/third-party/api-keys",<br/>"secretKey": "RDM-API-KEY"<br/>}

    Note over VLE: Check if authenticated
    alt Not authenticated or token expired
        VLE->>SM: Get AppRole credentials
        SM-->>VLE: role_id, secret_id

        VLE->>V: POST /v1/auth/approle/login
        Note over VLE,V: Authenticate with AppRole
        V-->>VLE: Return Vault token (8h TTL)

        VLE->>VLE: Cache token in memory
    end

    Note over VLE: Check file cache
    VLE->>Cache: Check if /tmp/creds.json exists
    alt File cache exists
        Cache-->>VLE: Return cached secrets
        Note over VLE: Parse JSON and extract secret
    else File cache not found
        VLE->>V: GET /v1/secret/data/inscore/prod/third-party/api-keys
        Note over VLE,V: Header: X-Vault-Token: hvs.CAESIL...

        V->>V: Validate token and policies
        V-->>VLE: Return secret data
        Note over V,VLE: {<br/>"data": {<br/>"RDM-API-KEY": "rdm_api_key_xxx",<br/>"PAYMENT-API-KEY": "payment_key_yyy"<br/>}<br/>}

        VLE->>Cache: Write secrets to /tmp/creds.json
        Note over VLE,Cache: Cache for subsequent invocations
    end

    VLE->>VLE: Extract requested secret key
    Note over VLE: Extract "RDM-API-KEY" from response

    VLE-->>CL: Return secret value
    Note over VLE,CL: Response: {<br/>"statusCode": 200,<br/>"body": {<br/>"secret": "rdm_api_key_xxx"<br/>}<br/>}

    CL->>CL: Use RDM API key
    Note over CL: Call RDM API with retrieved key
```

### Implementation Details

#### 1. Client-Lambda Invocation Code

**Client-Lambda (Invoking vault-lambda-extension)**:
```javascript
const AWS = require('aws-sdk');
const lambda = new AWS.Lambda();

/**
 * Retrieve secret from vault-lambda-extension Lambda
 */
async function getSecretFromVault(secretPath, secretKey) {
    const payload = {
        secretPath: secretPath,
        secretKey: secretKey
    };

    const params = {
        FunctionName: 'vault-lambda-extension',  // Name of the Vault Lambda
        InvocationType: 'RequestResponse',
        Payload: JSON.stringify(payload)
    };

    try {
        const response = await lambda.invoke(params).promise();
        const result = JSON.parse(response.Payload);

        if (result.statusCode === 200) {
            const body = JSON.parse(result.body);
            return body.secret;
        } else {
            throw new Error(`Failed to retrieve secret: ${result.body}`);
        }
    } catch (error) {
        console.error('Error invoking vault-lambda-extension:', error);
        throw error;
    }
}

/**
 * Client-Lambda handler
 */
exports.handler = async (event, context) => {
    try {
        // Retrieve RDM API key from Vault via vault-lambda-extension Lambda
        const rdmApiKey = await getSecretFromVault(
            '/v1/secret/data/inscore/prod/third-party/api-keys',
            'RDM-API-KEY'
        );

        console.log('Successfully retrieved RDM API key');

        // Use the API key to call RDM API
        const axios = require('axios');
        const rdmResponse = await axios.get('https://rdm-api.example.com/data', {
            headers: {
                'Authorization': `Bearer ${rdmApiKey}`,
                'Content-Type': 'application/json'
            }
        });

        return {
            statusCode: 200,
            body: JSON.stringify({
                message: 'Success',
                data: rdmResponse.data
            })
        };

    } catch (error) {
        console.error('Error:', error);
        return {
            statusCode: 500,
            body: JSON.stringify({
                error: 'Internal server error',
                message: error.message
            })
        };
    }
};
```

#### 2. vault-lambda-extension Lambda Handler

**vault-lambda-extension Lambda (Handling secret requests)**:
```javascript
const AWS = require('aws-sdk');
const vault = require('node-vault');
const fs = require('fs').promises;

// Global Vault client (persists across invocations)
let vaultClient = null;
let vaultToken = null;
let tokenExpiry = null;

/**
 * Initialize Vault client and authenticate
 */
async function initializeVaultClient() {
    const vaultAddr = process.env.VAULT_ADDR;
    const secretFile = process.env.VAULT_SECRET_FILE || '/tmp/vault/secret.json';

    // Create Vault client
    const client = vault({
        apiVersion: 'v1',
        endpoint: vaultAddr
    });

    // Get AppRole credentials from Secrets Manager
    const secretsManager = new AWS.SecretsManager();
    const secretData = await secretsManager.getSecretValue({
        SecretId: process.env.APPROLE_SECRET_NAME
    }).promise();

    const credentials = JSON.parse(secretData.SecretString);

    // Authenticate with Vault
    const authResponse = await client.approleLogin({
        role_id: credentials.role_id,
        secret_id: credentials.secret_id
    });

    vaultToken = authResponse.auth.client_token;
    client.token = vaultToken;

    // Calculate token expiry
    tokenExpiry = Date.now() + (authResponse.auth.lease_duration * 1000);

    console.log('Successfully authenticated with Vault');
    return client;
}

/**
 * Check if token is valid
 */
function isTokenValid() {
    if (!vaultToken || !tokenExpiry) {
        return false;
    }
    // Renew token if within 5 minutes of expiry
    return Date.now() < (tokenExpiry - 300000);
}

/**
 * Read secrets from file cache
 */
async function readFromCache(secretFile) {
    try {
        const fileContent = await fs.readFile(secretFile, 'utf8');
        return JSON.parse(fileContent);
    } catch (error) {
        if (error.code !== 'ENOENT') {
            console.error('Error reading cache:', error);
        }
        return null;
    }
}

/**
 * Write secrets to file cache
 */
async function writeToCache(secretFile, secrets) {
    try {
        const path = require('path');
        const dir = path.dirname(secretFile);
        await fs.mkdir(dir, { recursive: true });
        await fs.writeFile(secretFile, JSON.stringify(secrets, null, 2));
        console.log(`Secrets cached to ${secretFile}`);
    } catch (error) {
        console.error('Error writing cache:', error);
    }
}

/**
 * Lambda handler
 */
exports.handler = async (event, context) => {
    const secretFile = process.env.VAULT_SECRET_FILE || '/tmp/vault/secret.json';

    try {
        const { secretPath, secretKey } = event;

        if (!secretPath) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'secretPath is required' })
            };
        }

        // Check file cache first
        let secrets = await readFromCache(secretFile);

        if (secrets && secretKey && secrets[secretKey]) {
            console.log(`Retrieved ${secretKey} from file cache`);
            return {
                statusCode: 200,
                body: JSON.stringify({
                    secret: secrets[secretKey],
                    source: 'cache'
                })
            };
        }

        // Initialize or reuse Vault client
        if (!vaultClient || !isTokenValid()) {
            vaultClient = await initializeVaultClient();
        }

        // Remove /v1/ prefix if present for node-vault
        let vaultPath = secretPath;
        if (secretPath.startsWith('/v1/')) {
            vaultPath = secretPath.substring(4);
        }

        // Retrieve from Vault
        console.log(`Fetching secrets from Vault: ${vaultPath}`);
        const response = await vaultClient.read(vaultPath);
        secrets = response.data.data;

        // Cache to file
        await writeToCache(secretFile, secrets);

        // Return specific secret key or all secrets
        if (secretKey) {
            if (!secrets[secretKey]) {
                return {
                    statusCode: 404,
                    body: JSON.stringify({ error: `Secret key '${secretKey}' not found` })
                };
            }
            return {
                statusCode: 200,
                body: JSON.stringify({
                    secret: secrets[secretKey],
                    source: 'vault'
                })
            };
        } else {
            return {
                statusCode: 200,
                body: JSON.stringify({
                    secrets: secrets,
                    source: 'vault'
                })
            };
        }

    } catch (error) {
        console.error('Error retrieving secret:', error);
        return {
            statusCode: 500,
            body: JSON.stringify({
                error: 'Failed to retrieve secret',
                message: error.message
            })
        };
    }
};
```

### Request and Response Format

**Request Payload** (Client-Lambda to vault-lambda-extension):
```json
{
  "secretPath": "/v1/secret/data/inscore/prod/third-party/api-keys",
  "secretKey": "RDM-API-KEY"
}
```

**Success Response** (vault-lambda-extension to Client-Lambda):
```json
{
  "statusCode": 200,
  "body": {
    "secret": "rdm_api_key_xxx",
    "source": "vault"
  }
}
```

**Error Response**:
```json
{
  "statusCode": 404,
  "body": {
    "error": "Secret key 'RDM-API-KEY' not found"
  }
}
```

### IAM Permissions Required

**Client-Lambda Execution Role**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:us-east-1:123456789012:function:vault-lambda-extension"
    }
  ]
}
```

**vault-lambda-extension Execution Role**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:inscore/vault/approle-credentials-*"
    }
  ]
}
```

### Performance Considerations

| Scenario | Latency | Cost |
|----------|---------|------|
| **First invocation (cold start)** | ~2-3 seconds | Lambda invocation + Vault authentication + Secret retrieval |
| **Warm invocation (file cache hit)** | ~50-100ms | Lambda invocation only (no Vault call) |
| **Warm invocation (cache miss)** | ~500-700ms | Lambda invocation + Secret retrieval |

**Optimization Tips**:
- Enable Provisioned Concurrency for vault-lambda-extension Lambda to avoid cold starts
- Use file caching (`VAULT_SECRET_FILE`) to minimize Vault API calls
- Implement token renewal to avoid re-authentication
- Cache frequently accessed secrets in client Lambda memory

## Dynamic Secrets Management

Dynamic secrets are automatically generated by Vault on-demand and have a limited lifetime. Unlike static secrets that are manually provisioned and stored, dynamic secrets are created when requested and automatically revoked after expiration. This section covers Vault's dynamic secrets capabilities with a focus on the PKI secrets engine for certificate management.

### Overview of Dynamic Secrets

**Key Characteristics**:
- **On-Demand Generation**: Secrets are created when requested, not pre-provisioned
- **Automatic Expiration**: Secrets have a built-in lease duration (TTL)
- **Automatic Revocation**: Expired secrets are automatically invalidated
- **Unique Per Request**: Each request generates a unique secret
- **Audit Trail**: Complete lifecycle tracking from generation to revocation

**Benefits**:
- Reduced credential sprawl and management overhead
- Shorter credential lifetime reduces exposure window
- Automatic rotation eliminates manual processes
- No need for secret versioning or manual rotation
- Eliminates risk of stale credentials

### Supported Dynamic Secrets Engines

#### 1. PKI Secrets Engine (Focus of this Document)
- **Purpose**: Generate X.509 certificates and private keys
- **Use Cases**: TLS/SSL certificates, mutual TLS authentication, service mesh certificates
- **Lease Duration**: Configurable (hours to years)
- **Mount Path**: `pki/` or `pki_int/` (for intermediate CA)

#### 2. Database Secrets Engine
- **Purpose**: Generate dynamic database credentials
- **Supported Databases**: PostgreSQL, MySQL, MongoDB, Oracle, MSSQL
- **Use Cases**: Temporary database access, read-only credentials
- **Lease Duration**: Configurable (minutes to hours)

#### 3. AWS Secrets Engine
- **Purpose**: Generate dynamic AWS IAM credentials
- **Credential Types**: IAM users, STS assumed roles, federation tokens
- **Use Cases**: Temporary AWS access for external services
- **Lease Duration**: Configurable (minutes to hours)

### Dynamic Secrets Lifecycle

```mermaid
graph LR
    A[Request Secret] --> B[Vault Generates Secret]
    B --> C[Issue Secret with Lease]
    C --> D[Application Uses Secret]
    D --> E{Lease Expiring?}
    E -->|Yes| F[Renew Lease]
    E -->|No| D
    F --> G{Renewal Allowed?}
    G -->|Yes| C
    G -->|No| H[Lease Expires]
    F --> H
    H --> I[Vault Revokes Secret]
    I --> J[Secret Invalidated]
```

**Lifecycle Stages**:
1. **Request**: Lambda function requests a dynamic secret from Vault
2. **Generation**: Vault generates the secret (e.g., certificate, database credential)
3. **Issuance**: Vault issues the secret with a lease ID and TTL
4. **Usage**: Lambda function uses the secret for its operations
5. **Renewal**: Before expiration, lease can be renewed (if renewable)
6. **Expiration**: When TTL expires, Vault automatically revokes the secret
7. **Revocation**: The secret is invalidated and can no longer be used

### Lease Management

**Lease Properties**:
- **Lease ID**: Unique identifier for the issued secret
- **Lease Duration**: Initial TTL for the secret
- **Renewable**: Whether the lease can be extended
- **Max TTL**: Maximum lifetime including renewals

**Lease Renewal**:
```
POST /v1/sys/leases/renew
{
  "lease_id": "pki/issue/inscore-role/abc123",
  "increment": 3600
}
```

**Lease Revocation** (manual):
```
POST /v1/sys/leases/revoke
{
  "lease_id": "pki/issue/inscore-role/abc123"
}
```

### PKI Secrets Engine URLs and Endpoints

**Root CA Endpoints**:
```
# Generate root CA certificate
POST https://xe1.vault.aig.net/v1/pki/root/generate/internal

# Read CA certificate
GET https://xe1.vault.aig.net/v1/pki/ca/pem

# Read CA certificate chain
GET https://xe1.vault.aig.net/v1/pki/ca_chain
```

**Intermediate CA Endpoints**:
```
# Generate intermediate CSR
POST https://xe1.vault.aig.net/v1/pki_int/intermediate/generate/internal

# Sign intermediate certificate
POST https://xe1.vault.aig.net/v1/pki/root/sign-intermediate

# Set signed intermediate certificate
POST https://xe1.vault.aig.net/v1/pki_int/intermediate/set-signed
```

**Certificate Issuance Endpoints**:
```
# Issue certificate for a role
POST https://xe1.vault.aig.net/v1/pki_int/issue/inscore-role

# Issue certificate and sign CSR
POST https://xe1.vault.aig.net/v1/pki_int/sign/inscore-role
```

**Certificate Management Endpoints**:
```
# Revoke certificate
POST https://xe1.vault.aig.net/v1/pki_int/revoke

# Read certificate by serial number
GET https://xe1.vault.aig.net/v1/pki_int/cert/:serial

# List certificates
GET https://xe1.vault.aig.net/v1/pki_int/certs

# Read CRL (Certificate Revocation List)
GET https://xe1.vault.aig.net/v1/pki_int/crl
```

**Role Management Endpoints**:
```
# Create/Update PKI role
POST https://xe1.vault.aig.net/v1/pki_int/roles/inscore-role

# Read PKI role
GET https://xe1.vault.aig.net/v1/pki_int/roles/inscore-role

# List PKI roles
GET https://xe1.vault.aig.net/v1/pki_int/roles
```

## Digital Certificate Lifecycle Management

This section provides comprehensive coverage of digital certificate management using Vault's PKI secrets engine, including certificate generation, issuance, renewal, and rotation.

### Certificate Architecture Overview

```mermaid
graph TB
    subgraph "Vault PKI Infrastructure"
        RCA[Root CA<br/>pki/]
        ICA[Intermediate CA<br/>pki_int/]
        ROLES[PKI Roles<br/>inscore-role]
    end

    subgraph "Lambda Execution"
        LF[Lambda Function]
        CM[Certificate Manager]
        CC[Certificate Cache]
    end

    subgraph "Certificate Store"
        CERT[X.509 Certificate]
        PKEY[Private Key]
        CHAIN[Certificate Chain]
    end

    RCA -->|Signs| ICA
    ICA -->|Issues| CERT
    LF -->|Request Certificate| CM
    CM -->|Issue Request| ROLES
    ROLES -->|Generate & Sign| CERT
    ROLES -->|Generate| PKEY
    ROLES -->|Build| CHAIN
    CM -->|Cache| CC
    CC -->|Provide| LF

    style "Vault PKI Infrastructure" fill:#f0e1ff
    style "Lambda Execution" fill:#e1f5ff
    style "Certificate Store" fill:#fff4e1
```

### PKI Secrets Engine Configuration

#### Step 1: Enable and Configure Root CA

The root CA is typically configured once during initial setup and used only to sign intermediate certificates.

**Enable PKI Secrets Engine**:
```bash
# Enable root PKI backend
vault secrets enable pki

# Set max TTL to 10 years for root CA
vault secrets tune -max-lease-ttl=87600h pki
```

**Generate Root CA Certificate**:
```bash
vault write pki/root/generate/internal \
    common_name="InsCore Root CA" \
    issuer_name="inscore-root-ca" \
    ttl=87600h \
    key_bits=4096 \
    exclude_cn_from_sans=true \
    organization="American International Group" \
    ou="InsCore Platform" \
    country="US" \
    locality="New York" \
    province="NY"
```

**Configure CA and CRL URLs**:
```bash
vault write pki/config/urls \
    issuing_certificates="https://xe1.vault.aig.net/v1/pki/ca" \
    crl_distribution_points="https://xe1.vault.aig.net/v1/pki/crl"
```

#### Step 2: Enable and Configure Intermediate CA

The intermediate CA is used for day-to-day certificate issuance to Lambda functions.

**Enable Intermediate PKI Backend**:
```bash
# Enable intermediate PKI backend
vault secrets enable -path=pki_int pki

# Set max TTL to 5 years for intermediate CA
vault secrets tune -max-lease-ttl=43800h pki_int
```

**Generate Intermediate CSR**:
```bash
vault write -format=json pki_int/intermediate/generate/internal \
    common_name="InsCore Intermediate CA" \
    issuer_name="inscore-intermediate-ca" \
    key_bits=4096 \
    exclude_cn_from_sans=true \
    organization="American International Group" \
    ou="InsCore Platform" \
    country="US" \
    | jq -r '.data.csr' > inscore_intermediate.csr
```

**Sign Intermediate Certificate with Root CA**:
```bash
vault write -format=json pki/root/sign-intermediate \
    issuer_ref="inscore-root-ca" \
    csr=@inscore_intermediate.csr \
    format=pem_bundle \
    ttl=43800h \
    | jq -r '.data.certificate' > inscore_intermediate.cert.pem
```

**Import Signed Intermediate Certificate**:
```bash
vault write pki_int/intermediate/set-signed \
    certificate=@inscore_intermediate.cert.pem
```

**Configure Intermediate CA URLs**:
```bash
vault write pki_int/config/urls \
    issuing_certificates="https://xe1.vault.aig.net/v1/pki_int/ca" \
    crl_distribution_points="https://xe1.vault.aig.net/v1/pki_int/crl"
```

#### Step 3: Create PKI Role for Lambda Functions

PKI roles define certificate parameters and constraints for issued certificates.

**Create InsCore Lambda Role**:
```bash
vault write pki_int/roles/inscore-lambda \
    allowed_domains="inscore.aig.com,*.inscore.aig.com,inscore.internal,*.inscore.internal" \
    allow_subdomains=true \
    allow_glob_domains=true \
    allow_any_name=false \
    enforce_hostnames=true \
    allow_ip_sans=true \
    allowed_uri_sans="spiffe://inscore.aig.com/*" \
    server_flag=true \
    client_flag=true \
    code_signing_flag=false \
    email_protection_flag=false \
    key_type="rsa" \
    key_bits=2048 \
    signature_bits=256 \
    use_pss=false \
    ttl="720h" \
    max_ttl="2160h" \
    require_cn=true \
    organization="American International Group" \
    ou="InsCore Platform" \
    country="US"
```

**Role Configuration Parameters**:
- `allowed_domains`: Domains that can be issued (glob patterns supported)
- `allow_subdomains`: Allow subdomains of allowed_domains
- `allow_ip_sans`: Allow IP addresses in Subject Alternative Names
- `allowed_uri_sans`: Allow URI SANs (useful for SPIFFE IDs)
- `server_flag`: Enable TLS server authentication
- `client_flag`: Enable TLS client authentication
- `ttl`: Default certificate lifetime (30 days)
- `max_ttl`: Maximum certificate lifetime (90 days)
- `key_type`: RSA, EC, or ED25519
- `key_bits`: Key size (2048 or 4096 for RSA)

**Create Service-Specific Roles**:
```bash
# Role for API Gateway service
vault write pki_int/roles/inscore-api-gateway \
    allowed_domains="api-gateway.inscore.internal" \
    allow_subdomains=false \
    server_flag=true \
    client_flag=true \
    ttl="168h" \
    max_ttl="720h"

# Role for microservices
vault write pki_int/roles/inscore-microservice \
    allowed_domains="*.services.inscore.internal" \
    allow_subdomains=true \
    server_flag=true \
    client_flag=true \
    ttl="168h" \
    max_ttl="720h"
```

### Certificate Generation and Issuance

#### Certificate Request Flow

```mermaid
sequenceDiagram
    participant LF as Lambda Function
    participant CM as Certificate Manager
    participant V as Vault PKI Engine
    participant ICA as Intermediate CA
    participant CC as Certificate Cache

    LF->>CM: Request TLS certificate
    Note over LF,CM: Common Name: api-gateway.inscore.internal

    CM->>CM: Check certificate cache
    alt Certificate cached and valid
        CM->>LF: Return cached certificate
    else Certificate not cached or expired
        CM->>V: POST /v1/pki_int/issue/inscore-lambda
        Note over CM,V: Include CN, SANs, IP SANs, TTL

        V->>ICA: Generate private key
        ICA-->>V: Private key (2048-bit RSA)

        V->>ICA: Create CSR
        ICA-->>V: Certificate Signing Request

        V->>ICA: Sign certificate with intermediate CA
        ICA-->>V: Signed X.509 certificate

        V->>V: Build certificate chain
        V->>V: Create lease for certificate

        V-->>CM: Return certificate bundle + lease
        Note over V,CM: Certificate, Private Key, CA Chain, Lease ID

        CM->>CC: Cache certificate with metadata
        Note over CM,CC: Cache until 80% of TTL elapsed

        CM-->>LF: Return certificate bundle

        LF->>LF: Configure TLS with certificate
        Note over LF: Use for HTTPS, mTLS, etc.
    end
```

#### Certificate Issuance API

**Endpoint**: `POST /v1/pki_int/issue/{role_name}`

**Request Example**:
```bash
curl --request POST \
  --header "X-Vault-Token: ${VAULT_TOKEN}" \
  --data @certificate-request.json \
  https://xe1.vault.aig.net/v1/pki_int/issue/inscore-lambda
```

**Request Payload** (`certificate-request.json`):
```json
{
  "common_name": "api-gateway.inscore.internal",
  "alt_names": "api-gateway.inscore.aig.com,api.inscore.internal",
  "ip_sans": "10.0.1.100,10.0.1.101",
  "uri_sans": "spiffe://inscore.aig.com/api-gateway",
  "ttl": "720h",
  "format": "pem",
  "private_key_format": "pkcs8",
  "exclude_cn_from_sans": false
}
```

**Request Parameters**:
- `common_name` (required): Primary DNS name for the certificate
- `alt_names`: Comma-separated list of Subject Alternative Names (SANs)
- `ip_sans`: Comma-separated list of IP addresses for IP SANs
- `uri_sans`: Comma-separated list of URIs (e.g., SPIFFE IDs)
- `ttl`: Certificate lifetime (must be ≤ role's max_ttl)
- `format`: Certificate format (pem, der, pem_bundle)
- `private_key_format`: Private key format (pkcs8, der, pem)
- `exclude_cn_from_sans`: Whether to exclude CN from SANs

**Success Response** (200 OK):
```json
{
  "request_id": "request-uuid-here",
  "lease_id": "pki_int/issue/inscore-lambda/abc123def456",
  "renewable": false,
  "lease_duration": 2592000,
  "data": {
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIDXTCCAkWgAwIBAgIUZx...\n-----END CERTIFICATE-----",
    "issuing_ca": "-----BEGIN CERTIFICATE-----\nMIIDjTCCAnWgAwIBAgIUYw...\n-----END CERTIFICATE-----",
    "ca_chain": [
      "-----BEGIN CERTIFICATE-----\nMIIDjTCCAnWgAwIBAgIUYw...\n-----END CERTIFICATE-----",
      "-----BEGIN CERTIFICATE-----\nMIIDmTCCAoGgAwIBAgIUXx...\n-----END CERTIFICATE-----"
    ],
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQE...\n-----END PRIVATE KEY-----",
    "private_key_type": "rsa",
    "serial_number": "5e:71:3c:9a:7d:2b:1f:8e:4a:5c:3d:2e:1b:4f:6a:8c:9d:0e:2f:1a",
    "expiration": 1732104000,
    "ttl": 2592000
  },
  "wrap_info": null,
  "warnings": null,
  "auth": null
}
```

**Response Fields**:
- `certificate`: PEM-encoded X.509 certificate
- `issuing_ca`: PEM-encoded intermediate CA certificate
- `ca_chain`: Array of CA certificates (intermediate and root)
- `private_key`: PEM-encoded private key (PKCS#8 format)
- `private_key_type`: Type of private key (rsa, ec, ed25519)
- `serial_number`: Hexadecimal certificate serial number
- `expiration`: Unix timestamp of certificate expiration
- `ttl`: Certificate lifetime in seconds
- `lease_id`: Vault lease ID for the certificate
- `lease_duration`: Lease duration in seconds

### Certificate Manager Implementation

#### Node.js Certificate Manager Class

```javascript
const AWS = require('aws-sdk');
const vault = require('node-vault');
const crypto = require('crypto');
const tls = require('tls');

class CertificateManager {
    constructor(vaultClient, vaultToken) {
        this.vaultClient = vaultClient;
        this.vaultClient.token = vaultToken;
        this.certificateCache = new Map();
        this.pkiMountPath = process.env.PKI_MOUNT_PATH || 'pki_int';
        this.defaultRole = process.env.PKI_ROLE || 'inscore-lambda';
    }

    /**
     * Issue a new TLS certificate from Vault PKI
     * @param {Object} options - Certificate request options
     * @returns {Object} Certificate bundle with certificate, private key, and CA chain
     */
    async issueCertificate(options) {
        const {
            commonName,
            altNames = [],
            ipSans = [],
            uriSans = [],
            ttl = '720h',
            role = this.defaultRole,
            format = 'pem',
            privateKeyFormat = 'pkcs8'
        } = options;

        // Validate required parameters
        if (!commonName) {
            throw new Error('commonName is required for certificate issuance');
        }

        // Check cache first
        const cacheKey = this._getCacheKey(commonName, altNames);
        const cachedCert = this._getCachedCertificate(cacheKey);
        if (cachedCert) {
            console.log(`Using cached certificate for ${commonName}`);
            return cachedCert;
        }

        try {
            console.log(`Issuing new certificate for ${commonName} from role ${role}`);

            // Prepare request payload
            const requestData = {
                common_name: commonName,
                ttl: ttl,
                format: format,
                private_key_format: privateKeyFormat,
                exclude_cn_from_sans: false
            };

            // Add optional parameters
            if (altNames.length > 0) {
                requestData.alt_names = altNames.join(',');
            }
            if (ipSans.length > 0) {
                requestData.ip_sans = ipSans.join(',');
            }
            if (uriSans.length > 0) {
                requestData.uri_sans = uriSans.join(',');
            }

            // Issue certificate from Vault
            const response = await this.vaultClient.write(
                `${this.pkiMountPath}/issue/${role}`,
                requestData
            );

            // Extract certificate bundle
            const certBundle = {
                certificate: response.data.certificate,
                privateKey: response.data.private_key,
                privateKeyType: response.data.private_key_type,
                issuingCa: response.data.issuing_ca,
                caChain: response.data.ca_chain,
                serialNumber: response.data.serial_number,
                expiration: response.data.expiration,
                leaseId: response.lease_id,
                leaseDuration: response.lease_duration,
                issuedAt: new Date()
            };

            // Parse certificate for metadata
            const certMetadata = this._parseCertificate(certBundle.certificate);
            certBundle.subject = certMetadata.subject;
            certBundle.issuer = certMetadata.issuer;
            certBundle.validFrom = certMetadata.validFrom;
            certBundle.validTo = certMetadata.validTo;

            // Cache the certificate (cache until 80% of TTL)
            this._cacheCertificate(cacheKey, certBundle);

            console.log(`Successfully issued certificate for ${commonName}`);
            console.log(`  Serial Number: ${certBundle.serialNumber}`);
            console.log(`  Valid From: ${certBundle.validFrom}`);
            console.log(`  Valid To: ${certBundle.validTo}`);
            console.log(`  Lease Duration: ${certBundle.leaseDuration}s`);

            return certBundle;

        } catch (error) {
            console.error(`Failed to issue certificate for ${commonName}:`, error);
            throw new Error(`Certificate issuance failed: ${error.message}`);
        }
    }

    /**
     * Sign a Certificate Signing Request (CSR)
     * @param {string} csr - PEM-encoded CSR
     * @param {Object} options - Signing options
     * @returns {Object} Certificate bundle
     */
    async signCsr(csr, options = {}) {
        const {
            role = this.defaultRole,
            ttl = '720h',
            format = 'pem'
        } = options;

        try {
            console.log(`Signing CSR with role ${role}`);

            const response = await this.vaultClient.write(
                `${this.pkiMountPath}/sign/${role}`,
                {
                    csr: csr,
                    ttl: ttl,
                    format: format
                }
            );

            const certBundle = {
                certificate: response.data.certificate,
                issuingCa: response.data.issuing_ca,
                caChain: response.data.ca_chain,
                serialNumber: response.data.serial_number,
                expiration: response.data.expiration,
                leaseId: response.lease_id,
                leaseDuration: response.lease_duration,
                issuedAt: new Date()
            };

            console.log(`Successfully signed CSR. Serial: ${certBundle.serialNumber}`);
            return certBundle;

        } catch (error) {
            console.error('Failed to sign CSR:', error);
            throw new Error(`CSR signing failed: ${error.message}`);
        }
    }

    /**
     * Revoke a certificate by serial number
     * @param {string} serialNumber - Certificate serial number (hex format with colons)
     */
    async revokeCertificate(serialNumber) {
        try {
            console.log(`Revoking certificate with serial: ${serialNumber}`);

            await this.vaultClient.write(
                `${this.pkiMountPath}/revoke`,
                {
                    serial_number: serialNumber
                }
            );

            // Remove from cache
            this._removeCertificateFromCache(serialNumber);

            console.log(`Successfully revoked certificate: ${serialNumber}`);

        } catch (error) {
            console.error(`Failed to revoke certificate ${serialNumber}:`, error);
            throw new Error(`Certificate revocation failed: ${error.message}`);
        }
    }

    /**
     * Read certificate by serial number
     * @param {string} serialNumber - Certificate serial number
     * @returns {Object} Certificate data
     */
    async readCertificate(serialNumber) {
        try {
            const response = await this.vaultClient.read(
                `${this.pkiMountPath}/cert/${serialNumber}`
            );

            return {
                certificate: response.data.certificate,
                revocationTime: response.data.revocation_time,
                revocationTimeRfc3339: response.data.revocation_time_rfc3339
            };

        } catch (error) {
            if (error.response && error.response.statusCode === 404) {
                return null;
            }
            throw error;
        }
    }

    /**
     * Read Certificate Revocation List (CRL)
     * @returns {string} PEM-encoded CRL
     */
    async getCrl() {
        try {
            const response = await this.vaultClient.read(`${this.pkiMountPath}/crl/pem`);
            return response.data;
        } catch (error) {
            console.error('Failed to read CRL:', error);
            throw new Error(`CRL retrieval failed: ${error.message}`);
        }
    }

    /**
     * Create TLS credentials for Node.js HTTPS server
     * @param {Object} certBundle - Certificate bundle from issueCertificate()
     * @returns {Object} TLS credentials for https.createServer()
     */
    createTlsCredentials(certBundle) {
        return {
            key: certBundle.privateKey,
            cert: certBundle.certificate,
            ca: certBundle.caChain,
            requestCert: true,
            rejectUnauthorized: true
        };
    }

    /**
     * Get cached certificate if valid
     * @private
     */
    _getCachedCertificate(cacheKey) {
        const cached = this.certificateCache.get(cacheKey);
        if (!cached) {
            return null;
        }

        // Check if certificate is still valid (not past 80% of TTL)
        const now = Date.now();
        const issuedAt = cached.issuedAt.getTime();
        const ttlMs = cached.leaseDuration * 1000;
        const renewThreshold = issuedAt + (ttlMs * 0.8);

        if (now > renewThreshold) {
            console.log(`Cached certificate expired (past 80% TTL), removing from cache`);
            this.certificateCache.delete(cacheKey);
            return null;
        }

        // Check actual certificate expiration
        const validTo = new Date(cached.validTo).getTime();
        if (now > validTo) {
            console.log(`Cached certificate expired, removing from cache`);
            this.certificateCache.delete(cacheKey);
            return null;
        }

        return cached;
    }

    /**
     * Cache certificate with metadata
     * @private
     */
    _cacheCertificate(cacheKey, certBundle) {
        this.certificateCache.set(cacheKey, certBundle);
        console.log(`Cached certificate with key: ${cacheKey}`);
    }

    /**
     * Generate cache key for certificate
     * @private
     */
    _getCacheKey(commonName, altNames = []) {
        const names = [commonName, ...altNames].sort();
        return crypto
            .createHash('sha256')
            .update(names.join(','))
            .digest('hex')
            .substring(0, 16);
    }

    /**
     * Remove certificate from cache by serial number
     * @private
     */
    _removeCertificateFromCache(serialNumber) {
        for (const [key, cert] of this.certificateCache.entries()) {
            if (cert.serialNumber === serialNumber) {
                this.certificateCache.delete(key);
                console.log(`Removed certificate ${serialNumber} from cache`);
                break;
            }
        }
    }

    /**
     * Parse X.509 certificate for metadata
     * @private
     */
    _parseCertificate(pemCertificate) {
        try {
            // Use Node.js crypto to parse certificate
            const cert = new crypto.X509Certificate(pemCertificate);

            return {
                subject: cert.subject,
                issuer: cert.issuer,
                validFrom: cert.validFrom,
                validTo: cert.validTo,
                serialNumber: cert.serialNumber,
                fingerprint: cert.fingerprint,
                fingerprint256: cert.fingerprint256
            };
        } catch (error) {
            console.error('Failed to parse certificate:', error);
            return {
                subject: 'unknown',
                issuer: 'unknown',
                validFrom: null,
                validTo: null
            };
        }
    }

    /**
     * Clear all cached certificates
     */
    clearCache() {
        const count = this.certificateCache.size;
        this.certificateCache.clear();
        console.log(`Cleared ${count} certificates from cache`);
    }

    /**
     * Get cache statistics
     */
    getCacheStats() {
        const stats = {
            totalCached: this.certificateCache.size,
            certificates: []
        };

        for (const [key, cert] of this.certificateCache.entries()) {
            const now = Date.now();
            const issuedAt = cert.issuedAt.getTime();
            const ttlMs = cert.leaseDuration * 1000;
            const remainingTtl = Math.max(0, (issuedAt + ttlMs - now) / 1000);

            stats.certificates.push({
                cacheKey: key,
                commonName: cert.subject,
                serialNumber: cert.serialNumber,
                validTo: cert.validTo,
                remainingTtlSeconds: Math.floor(remainingTtl)
            });
        }

        return stats;
    }
}

module.exports = CertificateManager;
```

### Certificate Rotation Strategies

Certificate rotation is the process of replacing certificates before they expire to maintain continuous service availability.

#### Proactive Rotation (Recommended)

Rotate certificates at 80% of their TTL to provide a safety margin.

```javascript
class CertificateRotationManager {
    constructor(certificateManager) {
        this.certManager = certificateManager;
        this.activeCertificates = new Map();
        this.rotationThreshold = 0.8; // Rotate at 80% of TTL
    }

    /**
     * Issue certificate with automatic rotation scheduling
     */
    async issueCertificateWithRotation(options) {
        const certBundle = await this.certManager.issueCertificate(options);

        // Schedule rotation
        this._scheduleRotation(options.commonName, certBundle, options);

        return certBundle;
    }

    /**
     * Schedule certificate rotation
     * @private
     */
    _scheduleRotation(commonName, certBundle, originalOptions) {
        const ttlMs = certBundle.leaseDuration * 1000;
        const rotationTime = ttlMs * this.rotationThreshold;

        console.log(`Scheduling rotation for ${commonName} in ${rotationTime / 1000}s`);

        const rotationTimer = setTimeout(async () => {
            try {
                console.log(`Auto-rotating certificate for ${commonName}`);
                const newCertBundle = await this.certManager.issueCertificate(originalOptions);

                // Notify application to reload certificate
                await this._notifyCertificateRotation(commonName, newCertBundle);

                // Optionally revoke old certificate
                if (process.env.REVOKE_OLD_CERT === 'true') {
                    await this.certManager.revokeCertificate(certBundle.serialNumber);
                }

                // Schedule next rotation
                this._scheduleRotation(commonName, newCertBundle, originalOptions);

            } catch (error) {
                console.error(`Certificate rotation failed for ${commonName}:`, error);
                // Retry rotation after 5 minutes
                setTimeout(() => {
                    this._scheduleRotation(commonName, certBundle, originalOptions);
                }, 300000);
            }
        }, rotationTime);

        this.activeCertificates.set(commonName, {
            certBundle,
            rotationTimer,
            originalOptions
        });
    }

    /**
     * Notify application of certificate rotation
     * @private
     */
    async _notifyCertificateRotation(commonName, newCertBundle) {
        // Emit event or call callback
        console.log(`Certificate rotated for ${commonName}. New serial: ${newCertBundle.serialNumber}`);

        // In a real application, this might:
        // 1. Update TLS server with new certificate
        // 2. Notify connected clients
        // 3. Update load balancer configuration
        // 4. Write to certificate file system
    }

    /**
     * Cancel all scheduled rotations
     */
    cancelAllRotations() {
        for (const [commonName, certData] of this.activeCertificates.entries()) {
            clearTimeout(certData.rotationTimer);
            console.log(`Cancelled rotation for ${commonName}`);
        }
        this.activeCertificates.clear();
    }
}
```

#### Reactive Rotation (On-Demand)

Rotate certificates when they're about to expire or when an error occurs.

```javascript
/**
 * Check certificate expiration and rotate if needed
 */
async function checkAndRotateCertificate(certBundle, options, certManager) {
    const now = Date.now();
    const validTo = new Date(certBundle.validTo).getTime();
    const timeUntilExpiry = validTo - now;
    const ttlMs = certBundle.leaseDuration * 1000;
    const rotationThreshold = ttlMs * 0.8;

    // Check if rotation needed
    if (timeUntilExpiry < rotationThreshold) {
        console.log(`Certificate expiring soon, rotating...`);
        const newCertBundle = await certManager.issueCertificate(options);

        // Revoke old certificate
        await certManager.revokeCertificate(certBundle.serialNumber);

        return newCertBundle;
    }

    return certBundle;
}
```

#### Lambda Integration Example

```javascript
const VaultClient = require('./vault-client');
const CertificateManager = require('./certificate-manager');
const CertificateRotationManager = require('./certificate-rotation-manager');

// Global instances (persist across Lambda invocations)
let vaultClient = null;
let certManager = null;
let rotationManager = null;
let tlsCertificate = null;

/**
 * Initialize certificate management on Lambda cold start
 */
async function initializeCertificateManagement() {
    // Authenticate with Vault
    vaultClient = new VaultClient();
    await vaultClient.authenticate();

    // Initialize certificate manager
    certManager = new CertificateManager(vaultClient.client, vaultClient.token);

    // Initialize rotation manager
    rotationManager = new CertificateRotationManager(certManager);

    // Issue certificate for this Lambda function
    const commonName = process.env.SERVICE_DOMAIN || 'api-gateway.inscore.internal';
    const altNames = (process.env.ALT_NAMES || '').split(',').filter(Boolean);

    tlsCertificate = await rotationManager.issueCertificateWithRotation({
        commonName: commonName,
        altNames: altNames,
        ttl: process.env.CERT_TTL || '168h', // 7 days
        role: process.env.PKI_ROLE || 'inscore-lambda'
    });

    console.log(`Certificate issued for ${commonName}`);
    console.log(`  Serial: ${tlsCertificate.serialNumber}`);
    console.log(`  Valid until: ${tlsCertificate.validTo}`);

    return tlsCertificate;
}

/**
 * Lambda handler with certificate management
 */
exports.handler = async (event, context) => {
    try {
        // Initialize on cold start
        if (!tlsCertificate) {
            await initializeCertificateManagement();
        }

        // Use certificate for outbound HTTPS requests with mTLS
        const https = require('https');
        const httpsAgent = new https.Agent({
            key: tlsCertificate.privateKey,
            cert: tlsCertificate.certificate,
            ca: tlsCertificate.caChain,
            rejectUnauthorized: true
        });

        // Make HTTPS request with mTLS
        const axios = require('axios');
        const response = await axios.get('https://api.partner.com/data', {
            httpsAgent: httpsAgent
        });

        return {
            statusCode: 200,
            body: JSON.stringify({
                message: 'Success',
                certificateSerial: tlsCertificate.serialNumber,
                data: response.data
            })
        };

    } catch (error) {
        console.error('Error:', error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: error.message })
        };
    }
};

/**
 * Cleanup on Lambda shutdown (if using Lambda Extensions)
 */
process.on('SIGTERM', async () => {
    console.log('SIGTERM received, cleaning up...');
    if (rotationManager) {
        rotationManager.cancelAllRotations();
    }
    if (tlsCertificate && process.env.REVOKE_ON_SHUTDOWN === 'true') {
        await certManager.revokeCertificate(tlsCertificate.serialNumber);
    }
});
```

### Certificate Use Cases

#### Use Case 1: Mutual TLS (mTLS) for Service-to-Service Communication

```javascript
/**
 * Configure Express.js server with mTLS using Vault certificates
 */
const express = require('express');
const https = require('https');

async function startMtlsServer() {
    // Issue server certificate
    const serverCert = await certManager.issueCertificate({
        commonName: 'api-server.inscore.internal',
        ttl: '720h',
        role: 'inscore-lambda'
    });

    // Create Express app
    const app = express();

    app.get('/api/data', (req, res) => {
        // Client certificate available in req.socket.getPeerCertificate()
        const clientCert = req.socket.getPeerCertificate();
        console.log(`Client authenticated: ${clientCert.subject.CN}`);

        res.json({ message: 'Secure data', timestamp: Date.now() });
    });

    // Create HTTPS server with mTLS
    const tlsOptions = {
        key: serverCert.privateKey,
        cert: serverCert.certificate,
        ca: serverCert.caChain,
        requestCert: true,
        rejectUnauthorized: true
    };

    const server = https.createServer(tlsOptions, app);
    server.listen(8443, () => {
        console.log('mTLS server listening on port 8443');
    });
}
```

#### Use Case 2: TLS Termination at API Gateway

```javascript
/**
 * Issue certificate for API Gateway TLS termination
 */
async function setupApiGatewayCertificate() {
    const cert = await certManager.issueCertificate({
        commonName: 'api.inscore.aig.com',
        altNames: [
            'api-gateway.inscore.internal',
            'api.inscore.internal'
        ],
        ttl: '2160h', // 90 days
        role: 'inscore-api-gateway'
    });

    // Upload to AWS Certificate Manager for ALB/API Gateway
    const acm = new AWS.ACM({ region: 'us-east-1' });
    const acmResponse = await acm.importCertificate({
        Certificate: Buffer.from(cert.certificate),
        PrivateKey: Buffer.from(cert.privateKey),
        CertificateChain: Buffer.from(cert.caChain.join('\n'))
    }).promise();

    console.log(`Certificate imported to ACM: ${acmResponse.CertificateArn}`);
    return acmResponse.CertificateArn;
}
```

#### Use Case 3: SPIFFE/SPIRE Integration

```javascript
/**
 * Issue SPIFFE-compatible certificate for service identity
 */
async function issueSpiffeCertificate(serviceName) {
    const cert = await certManager.issueCertificate({
        commonName: `${serviceName}.inscore.internal`,
        uriSans: [`spiffe://inscore.aig.com/service/${serviceName}`],
        ttl: '24h',
        role: 'inscore-microservice'
    });

    console.log(`SPIFFE certificate issued for ${serviceName}`);
    console.log(`  SPIFFE ID: spiffe://inscore.aig.com/service/${serviceName}`);

    return cert;
}
```

### Certificate Revocation

#### Immediate Revocation

```javascript
/**
 * Revoke certificate immediately (e.g., security incident)
 */
async function emergencyRevokeCertificate(serialNumber, reason) {
    console.log(`EMERGENCY: Revoking certificate ${serialNumber}`);
    console.log(`Reason: ${reason}`);

    await certManager.revokeCertificate(serialNumber);

    // Update CRL and notify services
    await updateAndDistributeCrl();

    console.log(`Certificate ${serialNumber} revoked and CRL updated`);
}

/**
 * Update and distribute Certificate Revocation List
 */
async function updateAndDistributeCrl() {
    const crl = await certManager.getCrl();

    // Publish to S3 for distribution
    const s3 = new AWS.S3();
    await s3.putObject({
        Bucket: 'inscore-pki-crl',
        Key: 'inscore-intermediate.crl',
        Body: crl,
        ContentType: 'application/pkix-crl',
        ACL: 'public-read'
    }).promise();

    console.log('CRL published to S3');
}
```

#### Bulk Revocation

```javascript
/**
 * Revoke multiple certificates (e.g., security incident affecting multiple services)
 */
async function bulkRevokeCertificates(serialNumbers) {
    console.log(`Bulk revoking ${serialNumbers.length} certificates`);

    const results = await Promise.allSettled(
        serialNumbers.map(serial => certManager.revokeCertificate(serial))
    );

    const successful = results.filter(r => r.status === 'fulfilled').length;
    const failed = results.filter(r => r.status === 'rejected').length;

    console.log(`Bulk revocation complete: ${successful} succeeded, ${failed} failed`);

    // Update CRL
    await updateAndDistributeCrl();
}
```

### Monitoring and Observability

#### Certificate Expiration Monitoring

```javascript
/**
 * Monitor certificate expiration and send alerts
 */
class CertificateExpirationMonitor {
    constructor(certManager, cloudWatch) {
        this.certManager = certManager;
        this.cloudWatch = cloudWatch;
    }

    async monitorCertificateExpiration(certBundle, serviceName) {
        const now = Date.now();
        const validTo = new Date(certBundle.validTo).getTime();
        const daysUntilExpiry = (validTo - now) / (1000 * 60 * 60 * 24);

        // Send CloudWatch metric
        await this.cloudWatch.putMetricData({
            Namespace: 'InsCore/PKI',
            MetricData: [{
                MetricName: 'CertificateDaysUntilExpiry',
                Value: daysUntilExpiry,
                Unit: 'Count',
                Dimensions: [
                    { Name: 'Service', Value: serviceName },
                    { Name: 'SerialNumber', Value: certBundle.serialNumber }
                ],
                Timestamp: new Date()
            }]
        }).promise();

        // Alert if expiring soon
        if (daysUntilExpiry < 7) {
            console.warn(`WARNING: Certificate for ${serviceName} expires in ${daysUntilExpiry.toFixed(1)} days`);
            await this._sendExpirationAlert(serviceName, certBundle, daysUntilExpiry);
        }
    }

    async _sendExpirationAlert(serviceName, certBundle, daysUntilExpiry) {
        const sns = new AWS.SNS();
        await sns.publish({
            TopicArn: process.env.PKI_ALERT_TOPIC_ARN,
            Subject: `Certificate Expiration Alert: ${serviceName}`,
            Message: JSON.stringify({
                service: serviceName,
                serialNumber: certBundle.serialNumber,
                commonName: certBundle.subject,
                daysUntilExpiry: daysUntilExpiry.toFixed(1),
                validTo: certBundle.validTo,
                action: 'Certificate rotation recommended'
            }, null, 2)
        }).promise();
    }
}
```

#### Certificate Issuance Metrics

```javascript
/**
 * Track certificate issuance metrics
 */
async function trackCertificateIssuance(certBundle, serviceName) {
    const cloudWatch = new AWS.CloudWatch();

    await cloudWatch.putMetricData({
        Namespace: 'InsCore/PKI',
        MetricData: [
            {
                MetricName: 'CertificatesIssued',
                Value: 1,
                Unit: 'Count',
                Dimensions: [
                    { Name: 'Service', Value: serviceName }
                ],
                Timestamp: new Date()
            },
            {
                MetricName: 'CertificateTTL',
                Value: certBundle.leaseDuration / 3600, // Convert to hours
                Unit: 'Count',
                Dimensions: [
                    { Name: 'Service', Value: serviceName }
                ],
                Timestamp: new Date()
            }
        ]
    }).promise();
}
```

## Component Design

### Architecture Overview

```mermaid
graph TB
    subgraph "Lambda Execution Context"
        LF[Lambda Function Handler]
        VC[Vault Client Library]
        SC[Secret Cache]
        TM[Token Manager]
    end

    subgraph "AWS Services"
        IAM[IAM Execution Role]
        STS_Local[AWS STS]
        CW[CloudWatch Logs]
    end

    subgraph "Vault Infrastructure"
        VA[Vault API Gateway]
        VAuth[Vault Auth Backend]
        VSE[Vault Secrets Engine KV v2]
        VAudit[Vault Audit Logs]
    end

    LF -->|1. Request Secret| VC
    VC -->|2. Check Cache| SC
    SC -->|Cache Miss| TM
    TM -->|3. Get AWS Credentials| IAM
    IAM -->|4. Return Temp Creds| TM
    TM -->|5. Sign Request| TM
    TM -->|6. Authenticate| VA
    VA -->|7. Validate| VAuth
    VAuth -->|8. Verify with AWS| STS_Local
    STS_Local -->|9. Confirm Identity| VAuth
    VAuth -->|10. Issue Token| VA
    VA -->|11. Return Token| VC
    VC -->|12. Retrieve Secret| VA
    VA -->|13. Fetch Secret| VSE
    VSE -->|14. Return Secret Data| VA
    VA -->|15. Return Secret| VC
    VC -->|16. Cache Secret| SC
    VC -->|17. Return Secret| LF

    VC -.->|Log Events| CW
    VA -.->|Audit Trail| VAudit

    style "Lambda Execution Context" fill:#e1f5ff
    style "AWS Services" fill:#fff4e1
    style "Vault Infrastructure" fill:#f0e1ff
```

### Component Descriptions

#### 1. Lambda Function Handler
- **Purpose**: Main application logic that requires secrets
- **Responsibilities**:
  - Initialize Vault client on cold start
  - Request secrets when needed
  - Handle secret data securely in memory
  - Process business logic using secrets
- **Interface**: Standard Lambda handler interface

#### 2. Vault Client Library
- **Purpose**: Abstraction layer for Vault communication
- **Responsibilities**:
  - Manage authentication lifecycle
  - Handle secret retrieval requests
  - Implement retry logic and error handling
  - Coordinate with Token Manager and Secret Cache
- **Dependencies**: node-vault (Node.js library for HashiCorp Vault)
- **Configuration**:
  - Vault address and namespace
  - Authentication role and method
  - Timeout and retry settings

#### 3. Secret Cache
- **Purpose**: In-memory cache for retrieved secrets
- **Responsibilities**:
  - Store secrets during Lambda execution context
  - Implement cache key management (secret path)
  - Provide fast secret lookup
  - Clear cache on container restart
- **Data Structure**: Dictionary/Map with secret path as key
- **TTL**: Lifetime of Lambda execution context
- **Security**: Secrets never written to disk, only in memory

#### 4. Token Manager
- **Purpose**: Manage Vault token lifecycle
- **Responsibilities**:
  - Authenticate with Vault using AWS IAM
  - Store and manage Vault token
  - Monitor token TTL and trigger renewal
  - Re-authenticate when token expires or renewal fails
  - Generate AWS SigV4 signed requests
- **Token Storage**: In-memory only, never persisted
- **Renewal Strategy**: Renew when 80% of TTL elapsed or 5 minutes before expiry

#### 5. IAM Execution Role
- **Purpose**: Provide AWS identity for Lambda function
- **Responsibilities**:
  - Grant Lambda permissions to AWS services
  - Provide temporary credentials for Vault authentication
  - Serve as identity proof in authentication flow
- **Configuration**: IAM role ARN must match `bound_iam_principal_arn` in Vault

#### 6. Vault API Gateway
- **Purpose**: Entry point for all Vault API requests
- **Responsibilities**:
  - Route requests to appropriate backends (auth, secrets)
  - Enforce TLS/HTTPS encryption
  - Load balance across Vault instances
  - Rate limiting and throttling
- **Endpoint**: https://xe1.vault.aig.net

#### 7. Vault Auth Backend (AWS IAM)
- **Purpose**: Authenticate Lambda functions using AWS IAM
- **Responsibilities**:
  - Validate IAM role against bound principals
  - Verify signed requests with AWS STS
  - Issue Vault tokens with appropriate policies
  - Track authentication attempts for audit
- **Configuration**: auth/aws/role/lambda-role

#### 8. Vault Secrets Engine (KV v2)
- **Purpose**: Store and retrieve secrets
- **Responsibilities**:
  - Store secrets in encrypted backend
  - Version secrets automatically
  - Enforce access policies
  - Provide secret metadata
- **Mount Point**: secret/
- **Path Structure**: secret/data/inscore/{env}/{service}/{secret-type}

#### 9. CloudWatch Logs
- **Purpose**: Centralized logging for Lambda and Vault operations
- **Logged Events**:
  - Authentication attempts (success/failure)
  - Secret retrieval requests
  - Token renewal events
  - Errors and exceptions
- **Retention**: 30-90 days (configurable)

#### 10. Vault Audit Logs
- **Purpose**: Immutable audit trail of all Vault operations
- **Logged Data**:
  - Who accessed what secrets and when
  - Authentication events
  - Token operations
  - Failed access attempts
- **Storage**: Secure audit backend (file, syslog, or AWS CloudWatch)

### Data Flow Sequence

**Cold Start (First Invocation)**:
1. Lambda container initializes
2. Lambda function handler loads
3. Vault client initializes with configuration
4. Token Manager authenticates with Vault via AWS IAM
5. Vault issues token
6. Lambda retrieves required secrets
7. Secrets cached in memory
8. Lambda ready to process requests

**Warm Start (Subsequent Invocations)**:
1. Lambda receives new request
2. Vault client checks Secret Cache
3. If secret cached → return immediately
4. If secret not cached → retrieve from Vault
5. Process request with secrets

**Token Renewal**:
1. Token Manager monitors token TTL
2. When TTL threshold reached → attempt renewal
3. If renewal succeeds → update token and expiry
4. If renewal fails → re-authenticate
5. Continue operations with new token

### Integration Points

| Component | Integration Method | Protocol |
|-----------|-------------------|----------|
| Lambda → Vault Client | Function call | In-process |
| Vault Client → Token Manager | Function call | In-process |
| Vault Client → Secret Cache | Function call | In-process |
| Token Manager → IAM | AWS SDK | HTTPS/IAM |
| Token Manager → Vault Auth | REST API | HTTPS |
| Vault Client → Vault Secrets Engine | REST API | HTTPS |
| Lambda → CloudWatch Logs | AWS SDK | HTTPS |
| Vault → Audit Logs | Plugin | File/Syslog |

## API Definitions

### Vault API Endpoints

#### 1. Authentication API

**Endpoint**: `POST /v1/auth/aws/login`

**Description**: Authenticate Lambda function using AWS IAM credentials

**Request Headers**:
```
Content-Type: application/json
```

**Request Body**:
```json
{
  "role": "lambda-role",
  "iam_http_request_method": "POST",
  "iam_request_url": "aHR0cHM6Ly9zdHMuYW1hem9uYXdzLmNvbS8=",
  "iam_request_body": "QWN0aW9uPUdldENhbGxlcklkZW50aXR5JlZlcnNpb249MjAxMS0wNi0xNQ==",
  "iam_request_headers": "{\"Authorization\":[\"AWS4-HMAC-SHA256...\"]}"
}
```

**Success Response** (200 OK):
```json
{
  "auth": {
    "client_token": "s.xyz123abc456def789",
    "accessor": "accessor_abc123",
    "policies": ["inscore-lambda-read"],
    "metadata": {
      "account_id": "XXXXXXXXXXXX",
      "role_id": "arn:aws:iam::XXXXXXXXXXXX:role/lambda-execution-role"
    },
    "lease_duration": 3600,
    "renewable": true
  }
}
```

**Error Response** (403 Forbidden):
```json
{
  "errors": [
    "invalid AWS role: lambda-execution-role does not match bound principals"
  ]
}
```

**Error Response** (500 Internal Server Error):
```json
{
  "errors": [
    "failed to verify AWS credentials with STS"
  ]
}
```

#### 2. Secret Read API

**Endpoint**: `GET /v1/secret/data/{path}`

**Description**: Retrieve secret from KV v2 secrets engine

**URL Example**:
```
GET https://xe1.vault.aig.net/v1/secret/data/inscore/prod/rds/db-credentials
```

**Request Headers**:
```
X-Vault-Token: s.xyz123abc456def789
```

**Success Response** (200 OK):
```json
{
  "request_id": "request-uuid-here",
  "lease_id": "",
  "renewable": false,
  "lease_duration": 0,
  "data": {
    "data": {
      "username": "inscore_db_user",
      "password": "SecureP@ssw0rd123",
      "host": "inscore-prod-rds.cluster-xxxxx.us-east-1.rds.amazonaws.com",
      "port": "5432",
      "database": "inscore_db"
    },
    "metadata": {
      "created_time": "2025-11-06T10:30:00.123456Z",
      "custom_metadata": null,
      "deletion_time": "",
      "destroyed": false,
      "version": 3
    }
  },
  "wrap_info": null,
  "warnings": null,
  "auth": null
}
```

**Error Response** (403 Forbidden):
```json
{
  "errors": [
    "permission denied"
  ]
}
```

**Error Response** (404 Not Found):
```json
{
  "errors": []
}
```

#### 3. Secret Write API (Configuration Time Only)

**Endpoint**: `POST /v1/secret/data/{path}`

**Description**: Create or update secret in KV v2 secrets engine

**URL Example**:
```
POST https://xe1.vault.aig.net/v1/secret/data/inscore/prod/rds/db-credentials
```

**Request Headers**:
```
X-Vault-Token: s.admin-token-here
Content-Type: application/json
```

**Request Body**:
```json
{
  "data": {
    "username": "inscore_db_user",
    "password": "SecureP@ssw0rd123",
    "host": "inscore-prod-rds.cluster-xxxxx.us-east-1.rds.amazonaws.com",
    "port": "5432",
    "database": "inscore_db"
  },
  "options": {
    "cas": 0
  }
}
```

**Success Response** (200 OK):
```json
{
  "request_id": "request-uuid-here",
  "lease_id": "",
  "renewable": false,
  "lease_duration": 0,
  "data": {
    "created_time": "2025-11-06T10:30:00.123456Z",
    "custom_metadata": null,
    "deletion_time": "",
    "destroyed": false,
    "version": 4
  },
  "wrap_info": null,
  "warnings": null,
  "auth": null
}
```

#### 4. Token Renewal API

**Endpoint**: `POST /v1/auth/token/renew-self`

**Description**: Renew the current Vault token

**Request Headers**:
```
X-Vault-Token: s.xyz123abc456def789
Content-Type: application/json
```

**Request Body**:
```json
{
  "increment": 3600
}
```

**Success Response** (200 OK):
```json
{
  "auth": {
    "client_token": "s.xyz123abc456def789",
    "accessor": "accessor_abc123",
    "policies": ["inscore-lambda-read"],
    "metadata": {
      "account_id": "XXXXXXXXXXXX",
      "role_id": "arn:aws:iam::XXXXXXXXXXXX:role/lambda-execution-role"
    },
    "lease_duration": 3600,
    "renewable": true
  }
}
```

**Error Response** (403 Forbidden):
```json
{
  "errors": [
    "token is not renewable"
  ]
}
```

#### 5. Token Lookup API

**Endpoint**: `GET /v1/auth/token/lookup-self`

**Description**: Retrieve information about the current token

**Request Headers**:
```
X-Vault-Token: s.xyz123abc456def789
```

**Success Response** (200 OK):
```json
{
  "data": {
    "accessor": "accessor_abc123",
    "creation_time": 1699270800,
    "creation_ttl": 3600,
    "display_name": "aws-lambda-execution-role",
    "entity_id": "entity-uuid",
    "expire_time": "2025-11-06T11:30:00.123456Z",
    "explicit_max_ttl": 0,
    "id": "s.xyz123abc456def789",
    "issue_time": "2025-11-06T10:30:00.123456Z",
    "meta": {
      "account_id": "XXXXXXXXXXXX",
      "role_id": "arn:aws:iam::XXXXXXXXXXXX:role/lambda-execution-role"
    },
    "num_uses": 0,
    "orphan": false,
    "path": "auth/aws/login",
    "policies": ["inscore-lambda-read"],
    "renewable": true,
    "ttl": 3456,
    "type": "service"
  }
}
```

#### 6. Secret Metadata API

**Endpoint**: `GET /v1/secret/metadata/{path}`

**Description**: Retrieve metadata for a secret (without reading secret data)

**URL Example**:
```
GET https://xe1.vault.aig.net/v1/secret/metadata/inscore/prod/rds/db-credentials
```

**Request Headers**:
```
X-Vault-Token: s.xyz123abc456def789
```

**Success Response** (200 OK):
```json
{
  "data": {
    "cas_required": false,
    "created_time": "2025-10-01T10:00:00.123456Z",
    "current_version": 4,
    "custom_metadata": {
      "owner": "platform-team",
      "environment": "prod"
    },
    "delete_version_after": "0s",
    "max_versions": 10,
    "oldest_version": 1,
    "updated_time": "2025-11-06T10:30:00.123456Z",
    "versions": {
      "1": {
        "created_time": "2025-10-01T10:00:00.123456Z",
        "deletion_time": "",
        "destroyed": false
      },
      "2": {
        "created_time": "2025-10-15T14:20:00.123456Z",
        "deletion_time": "",
        "destroyed": false
      },
      "3": {
        "created_time": "2025-11-01T09:15:00.123456Z",
        "deletion_time": "",
        "destroyed": false
      },
      "4": {
        "created_time": "2025-11-06T10:30:00.123456Z",
        "deletion_time": "",
        "destroyed": false
      }
    }
  }
}
```

#### 7. Health Check API

**Endpoint**: `GET /v1/sys/health`

**Description**: Check Vault cluster health status

**URL Example**:
```
GET https://xe1.vault.aig.net/v1/sys/health
```

**Query Parameters**:
- `standbyok=true` - Return 200 for standby nodes
- `perfstandbyok=true` - Return 200 for performance standby nodes

**Success Response** (200 OK - Active Node):
```json
{
  "initialized": true,
  "sealed": false,
  "standby": false,
  "performance_standby": false,
  "replication_performance_mode": "primary",
  "replication_dr_mode": "disabled",
  "server_time_utc": 1699274400,
  "version": "1.15.0",
  "cluster_name": "vault-cluster-prod",
  "cluster_id": "cluster-uuid-here"
}
```

**Response** (429 Standby Node):
```json
{
  "initialized": true,
  "sealed": false,
  "standby": true,
  "performance_standby": false,
  "server_time_utc": 1699274400,
  "version": "1.15.0"
}
```

### API Client Examples

#### cURL Examples

**Authenticate with Vault**:
```bash
curl --request POST \
  --data @auth-payload.json \
  https://xe1.vault.aig.net/v1/auth/aws/login
```

**Retrieve Secret**:
```bash
curl --header "X-Vault-Token: s.xyz123abc456def789" \
  https://xe1.vault.aig.net/v1/secret/data/inscore/prod/rds/db-credentials
```

**Renew Token**:
```bash
curl --request POST \
  --header "X-Vault-Token: s.xyz123abc456def789" \
  --data '{"increment": 3600}' \
  https://xe1.vault.aig.net/v1/auth/token/renew-self
```

**Check Vault Health**:
```bash
curl https://xe1.vault.aig.net/v1/sys/health
```

#### Node.js Client Example (using node-vault)

```javascript
const vault = require('node-vault');

// Initialize client
const client = vault({
    apiVersion: 'v1',
    endpoint: 'https://xe1.vault.aig.net'
});

// Authenticate
const authResponse = await client.awsIamLogin({ role: 'lambda-role' });
client.token = authResponse.auth.client_token;

// Read secret
const secret = await client.read('secret/data/inscore/prod/rds/db-credentials');
const dbPassword = secret.data.data.password;

// Renew token
await client.tokenRenewSelf({ increment: 3600 });
```

### Error Codes and Handling

| HTTP Status | Description | Recommended Action |
|-------------|-------------|-------------------|
| 200 | Success | Process response data |
| 204 | Success (No Content) | Operation completed successfully |
| 400 | Bad Request | Check request format and parameters |
| 403 | Forbidden | Check token validity and permissions |
| 404 | Not Found | Verify secret path exists |
| 429 | Rate Limited | Implement exponential backoff retry |
| 500 | Internal Server Error | Retry with backoff, alert if persistent |
| 502 | Bad Gateway | Vault service issue, retry with backoff |
| 503 | Service Unavailable | Vault sealed or unhealthy, alert operations |

### PKI Certificate API Endpoints

#### 8. Certificate Issuance API

**Endpoint**: `POST /v1/pki_int/issue/{role_name}`

**Description**: Issue a new X.509 certificate with private key

**URL Example**:
```
POST https://xe1.vault.aig.net/v1/pki_int/issue/inscore-lambda
```

**Request Headers**:
```
X-Vault-Token: s.xyz123abc456def789
Content-Type: application/json
```

**Request Body**:
```json
{
  "common_name": "api-gateway.inscore.internal",
  "alt_names": "api-gateway.inscore.aig.com,api.inscore.internal",
  "ip_sans": "10.0.1.100,10.0.1.101",
  "uri_sans": "spiffe://inscore.aig.com/api-gateway",
  "ttl": "720h",
  "format": "pem",
  "private_key_format": "pkcs8"
}
```

**Request Parameters**:
- `common_name` (required): Primary DNS name for certificate
- `alt_names` (optional): Comma-separated Subject Alternative Names
- `ip_sans` (optional): Comma-separated IP addresses for IP SANs
- `uri_sans` (optional): Comma-separated URIs (e.g., SPIFFE IDs)
- `ttl` (optional): Certificate lifetime (default from role)
- `format` (optional): Certificate format (pem, der, pem_bundle)
- `private_key_format` (optional): Private key format (pkcs8, der, pem)

**Success Response** (200 OK):
```json
{
  "request_id": "abc-123-def",
  "lease_id": "pki_int/issue/inscore-lambda/xyz789",
  "renewable": false,
  "lease_duration": 2592000,
  "data": {
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIDXTCCAkWg...",
    "issuing_ca": "-----BEGIN CERTIFICATE-----\nMIIDjTCCAnWg...",
    "ca_chain": [
      "-----BEGIN CERTIFICATE-----\nMIIDjTCCAnWg...",
      "-----BEGIN CERTIFICATE-----\nMIIDmTCCAoGg..."
    ],
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg...",
    "private_key_type": "rsa",
    "serial_number": "5e:71:3c:9a:7d:2b:1f:8e:4a:5c:3d:2e:1b:4f:6a:8c",
    "expiration": 1732104000,
    "ttl": 2592000
  }
}
```

**Error Response** (400 Bad Request):
```json
{
  "errors": [
    "common_name is not allowed by role policy"
  ]
}
```

#### 9. CSR Signing API

**Endpoint**: `POST /v1/pki_int/sign/{role_name}`

**Description**: Sign a Certificate Signing Request

**Request Body**:
```json
{
  "csr": "-----BEGIN CERTIFICATE REQUEST-----\nMIICvDCCAaQCAQAw...",
  "ttl": "720h",
  "format": "pem"
}
```

**Success Response** (200 OK):
```json
{
  "data": {
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIDXT...",
    "issuing_ca": "-----BEGIN CERTIFICATE-----\nMIIDjT...",
    "ca_chain": ["..."],
    "serial_number": "5e:71:3c:9a:7d:2b:1f:8e",
    "expiration": 1732104000
  },
  "lease_id": "pki_int/sign/inscore-lambda/abc123",
  "lease_duration": 2592000
}
```

#### 10. Certificate Revocation API

**Endpoint**: `POST /v1/pki_int/revoke`

**Description**: Revoke a certificate by serial number

**Request Body**:
```json
{
  "serial_number": "5e:71:3c:9a:7d:2b:1f:8e:4a:5c:3d:2e:1b:4f:6a:8c"
}
```

**Success Response** (200 OK):
```json
{
  "data": {
    "revocation_time": 1699274400,
    "revocation_time_rfc3339": "2023-11-06T14:00:00Z"
  }
}
```

#### 11. Read Certificate API

**Endpoint**: `GET /v1/pki_int/cert/{serial_number}`

**Description**: Read certificate by serial number

**URL Example**:
```
GET https://xe1.vault.aig.net/v1/pki_int/cert/5e-71-3c-9a-7d-2b-1f-8e
```

**Success Response** (200 OK):
```json
{
  "data": {
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIDXT...",
    "revocation_time": 0,
    "revocation_time_rfc3339": ""
  }
}
```

#### 12. List Certificates API

**Endpoint**: `GET /v1/pki_int/certs`

**Description**: List all certificate serial numbers

**Success Response** (200 OK):
```json
{
  "data": {
    "keys": [
      "5e:71:3c:9a:7d:2b:1f:8e:4a:5c:3d:2e:1b:4f:6a:8c",
      "6f:82:4d:ab:8e:3c:2g:9f:5b:6d:4e:3f:2c:5g:7b:9d"
    ]
  }
}
```

#### 13. Read CA Certificate API

**Endpoint**: `GET /v1/pki_int/ca/pem`

**Description**: Retrieve CA certificate in PEM format

**Success Response** (200 OK):
```
-----BEGIN CERTIFICATE-----
MIIDjTCCAnWgAwIBAgIUYwxyz...
-----END CERTIFICATE-----
```

#### 14. Read CA Certificate Chain API

**Endpoint**: `GET /v1/pki_int/ca_chain`

**Description**: Retrieve full CA certificate chain

**Success Response** (200 OK):
```
-----BEGIN CERTIFICATE-----
MIIDjTCCAnWgAwIBAgIUYw... (Intermediate CA)
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDmTCCAoGgAwIBAgIUXx... (Root CA)
-----END CERTIFICATE-----
```

#### 15. Read CRL (Certificate Revocation List) API

**Endpoint**: `GET /v1/pki_int/crl`

**Description**: Retrieve Certificate Revocation List

**Success Response** (200 OK):
```
-----BEGIN X509 CRL-----
MIIBnjCBlwIBATANBgkqhkiG9w0BAQs...
-----END X509 CRL-----
```

#### 16. Read CRL in PEM Format API

**Endpoint**: `GET /v1/pki_int/crl/pem`

**Description**: Retrieve CRL in PEM format

**Success Response** (200 OK):
```
-----BEGIN X509 CRL-----
MIIBnjCBlwIBATANBgkqhkiG9w0BAQs...
-----END X509 CRL-----
```

#### 17. PKI Role Management API

**Endpoint**: `POST /v1/pki_int/roles/{role_name}`

**Description**: Create or update a PKI role (configuration time only)

**Request Body**:
```json
{
  "allowed_domains": "inscore.aig.com,*.inscore.aig.com",
  "allow_subdomains": true,
  "allow_glob_domains": true,
  "allow_ip_sans": true,
  "server_flag": true,
  "client_flag": true,
  "ttl": "720h",
  "max_ttl": "2160h",
  "key_type": "rsa",
  "key_bits": 2048
}
```

**Success Response** (204 No Content)

**Read PKI Role Endpoint**: `GET /v1/pki_int/roles/{role_name}`

**Success Response** (200 OK):
```json
{
  "data": {
    "allowed_domains": ["inscore.aig.com", "*.inscore.aig.com"],
    "allow_subdomains": true,
    "allow_glob_domains": true,
    "allow_ip_sans": true,
    "server_flag": true,
    "client_flag": true,
    "ttl": 2592000,
    "max_ttl": 7776000,
    "key_type": "rsa",
    "key_bits": 2048
  }
}
```

### PKI API Client Examples

#### cURL Examples for Certificate Operations

**Issue Certificate**:
```bash
curl --request POST \
  --header "X-Vault-Token: ${VAULT_TOKEN}" \
  --data '{
    "common_name": "api-gateway.inscore.internal",
    "alt_names": "api-gateway.inscore.aig.com",
    "ttl": "720h"
  }' \
  https://xe1.vault.aig.net/v1/pki_int/issue/inscore-lambda
```

**Sign CSR**:
```bash
curl --request POST \
  --header "X-Vault-Token: ${VAULT_TOKEN}" \
  --data @csr-request.json \
  https://xe1.vault.aig.net/v1/pki_int/sign/inscore-lambda
```

**Revoke Certificate**:
```bash
curl --request POST \
  --header "X-Vault-Token: ${VAULT_TOKEN}" \
  --data '{"serial_number": "5e:71:3c:9a:7d:2b:1f:8e"}' \
  https://xe1.vault.aig.net/v1/pki_int/revoke
```

**Read Certificate**:
```bash
curl --header "X-Vault-Token: ${VAULT_TOKEN}" \
  https://xe1.vault.aig.net/v1/pki_int/cert/5e-71-3c-9a-7d-2b-1f-8e
```

**Download CA Certificate**:
```bash
curl https://xe1.vault.aig.net/v1/pki_int/ca/pem \
  --output ca-certificate.pem
```

**Download CRL**:
```bash
curl https://xe1.vault.aig.net/v1/pki_int/crl/pem \
  --output certificate-revocation-list.crl
```

#### Node.js Client Examples for PKI

```javascript
const vault = require('node-vault');

// Initialize and authenticate
const client = vault({
    apiVersion: 'v1',
    endpoint: 'https://xe1.vault.aig.net'
});
client.token = vaultToken;

// Issue certificate
const certResponse = await client.write('pki_int/issue/inscore-lambda', {
    common_name: 'api-gateway.inscore.internal',
    alt_names: 'api-gateway.inscore.aig.com',
    ttl: '720h'
});

const certificate = certResponse.data.certificate;
const privateKey = certResponse.data.private_key;
const caChain = certResponse.data.ca_chain;

// Sign CSR
const signedCert = await client.write('pki_int/sign/inscore-lambda', {
    csr: csrPemString,
    ttl: '720h'
});

// Revoke certificate
await client.write('pki_int/revoke', {
    serial_number: '5e:71:3c:9a:7d:2b:1f:8e'
});

// Read certificate
const cert = await client.read('pki_int/cert/5e-71-3c-9a-7d-2b-1f-8e');

// Read CA certificate
const caCert = await client.read('pki_int/ca/pem');

// Read CRL
const crl = await client.read('pki_int/crl/pem');
```

### Dynamic Secrets Lease Management APIs

#### 18. Lease Renewal API

**Endpoint**: `POST /v1/sys/leases/renew`

**Description**: Renew a lease for a dynamic secret (including certificates)

**Request Body**:
```json
{
  "lease_id": "pki_int/issue/inscore-lambda/abc123def456",
  "increment": 3600
}
```

**Success Response** (200 OK):
```json
{
  "lease_id": "pki_int/issue/inscore-lambda/abc123def456",
  "lease_duration": 3600,
  "renewable": true
}
```

**Note**: PKI certificates are typically not renewable. Instead, issue a new certificate before expiration.

#### 19. Lease Revocation API

**Endpoint**: `POST /v1/sys/leases/revoke`

**Description**: Revoke a lease immediately

**Request Body**:
```json
{
  "lease_id": "pki_int/issue/inscore-lambda/abc123def456"
}
```

**Success Response** (204 No Content)

#### 20. Lease Lookup API

**Endpoint**: `POST /v1/sys/leases/lookup`

**Description**: Retrieve information about a lease

**Request Body**:
```json
{
  "lease_id": "pki_int/issue/inscore-lambda/abc123def456"
}
```

**Success Response** (200 OK):
```json
{
  "data": {
    "id": "pki_int/issue/inscore-lambda/abc123def456",
    "issue_time": "2023-11-06T10:00:00Z",
    "expire_time": "2023-12-06T10:00:00Z",
    "last_renewal_time": null,
    "renewable": false,
    "ttl": 2592000
  }
}
```

## Security Considerations

### Benefits of AWS IAM Authentication

**No Static Credentials**:
- No passwords or API keys stored in code or environment variables
- Eliminates risk of credential leakage through source code repositories
- No credential rotation required for Lambda function authentication
- Reduces attack surface by removing static secrets

**Cryptographic Verification**:
- Uses AWS Signature v4 for tamper-proof authentication
- Ensures requests cannot be forged or replayed
- Timestamp validation prevents replay attacks
- Signature binds request to specific AWS credentials

**Dynamic Trust**:
- Leverages existing AWS IAM infrastructure and policies
- No separate credential management system required
- IAM role changes automatically reflect in Vault access
- Integrates with existing AWS governance and compliance controls

**Time-Limited Access**:
- Vault tokens have configurable TTL (Time To Live)
- Tokens automatically expire, limiting exposure window
- Supports token renewal for long-running operations
- Forces periodic re-authentication for enhanced security

**Audit Trail**:
- All authentication attempts logged in both AWS CloudTrail and Vault audit logs
- Complete visibility into who accessed what secrets and when
- Supports compliance requirements (SOC2, PCI-DSS, HIPAA)
- Enables security incident investigation and forensics

**Cross-Account Security**:
- Requires explicit trust relationships via IAM policies
- Trust policies define which accounts and roles can authenticate
- Prevents unauthorized cross-account access
- Supports multi-account AWS architectures

**Principle of Least Privilege**:
- `bound_iam_principal_arn` restricts access to specific roles
- Vault policies limit secret access based on authenticated identity
- Fine-grained access control at both AWS and Vault layers
- Minimizes blast radius of potential security breaches

### Security Controls

**Role Binding**:
- Vault authentication role restricts access to specific Lambda IAM roles via `bound_iam_principal_arn`
- Only Lambda functions with matching IAM role ARNs can authenticate
- Prevents lateral movement between different Lambda functions
- Enforces identity-based access control

**Cross-Account Trust**:
- Explicit trust relationship between Vault account and BU account via IAM trust policies
- Trust policies must explicitly allow role assumption
- Requires mutual agreement between accounts for access
- Supports organization-wide security policies

**Network Isolation**:
- Vault instances run in dedicated VPC with security groups and network ACLs
- Private subnets with no direct internet access
- VPC endpoints for AWS service communication
- Network segmentation prevents unauthorized access

**Token TTL**:
- Vault tokens expire after configured time period, requiring re-authentication
- Configurable TTL based on security requirements (typically 15-60 minutes)
- Automatic token renewal if within renewal window
- Expired tokens are immediately revoked and cannot be used

**STS Verification**:
- AWS STS provides cryptographic proof of identity, preventing impersonation
- STS validates that signatures match claimed identities
- Trusted third-party verification (AWS) eliminates self-attestation
- Ensures authentication cannot be spoofed or bypassed

**Audit Logging**:
- All Vault access attempts logged for security monitoring and compliance
- Logs include timestamp, requesting entity, secrets accessed, and outcome
- Immutable audit logs stored securely for forensic analysis
- Integration with SIEM systems for real-time security monitoring
- Alerts on suspicious access patterns or authentication failures

### Best Practices

**Lambda Configuration**:
- Use separate IAM roles for different Lambda functions based on least privilege
- Configure Lambda timeout to be less than Vault token TTL
- Use Lambda layers for vault-lambda-extension for consistent versioning
- Enable X-Ray tracing for Vault authentication debugging

**Vault Configuration**:
- Regularly rotate Vault root tokens and unseal keys
- Use Vault namespaces to isolate different applications or teams
- Implement Vault policies with minimal required permissions
- Enable Vault audit logging and monitor for anomalies
- Configure appropriate token TTL based on Lambda execution time

**Monitoring and Alerting**:
- Monitor authentication success/failure rates
- Alert on repeated authentication failures (potential attack)
- Track Vault token usage and renewal patterns
- Monitor cross-account role assumption activity
- Set up CloudWatch alarms for unusual access patterns

**Disaster Recovery**:
- Implement Vault high availability with multiple instances
- Regular backup of Vault configuration and policies
- Document recovery procedures for Vault outages
- Test failover scenarios periodically
- Maintain secondary authentication mechanisms for emergency access

### PKI and Dynamic Secrets Security

**Certificate Private Key Protection**:
- Private keys are generated within Vault and transmitted only once over TLS
- Private keys never stored on disk by Lambda functions, only in memory
- Private keys cleared from memory when Lambda container terminates
- No logging or caching of private key material
- Private keys are unique per certificate issuance

**Certificate Lifetime Management**:
- Short-lived certificates (7-90 days) reduce exposure window
- Automatic expiration eliminates need for manual revocation in most cases
- Certificate rotation at 80% of TTL provides safety margin
- Expired certificates automatically revoked by Vault
- No certificate hoarding or long-term storage required

**Certificate Authority Security**:
- Root CA private key secured offline and used only for signing intermediate CAs
- Intermediate CA used for day-to-day certificate issuance
- Root CA certificate stored with maximum TTL (10 years)
- Intermediate CA certificates have shorter TTL (5 years)
- CA private keys never exposed via API
- Separate CA hierarchies for different security domains

**Certificate Revocation Controls**:
- Immediate certificate revocation capability via API
- Certificate Revocation List (CRL) automatically updated
- CRL published to S3 for distribution to clients
- OCSP (Online Certificate Status Protocol) support for real-time validation
- Emergency bulk revocation for security incidents
- Audit trail of all revocation events

**PKI Role-Based Access Control**:
- PKI roles restrict which domains and SANs can be issued
- Role policies enforce organizational security standards
- Separate roles for different services or environments
- Roles enforce key types, key sizes, and certificate validity periods
- Roles can restrict certificate usage (server auth, client auth, code signing)

**Mutual TLS (mTLS) Security**:
- Both client and server authenticate with certificates
- Eliminates password-based authentication for service-to-service communication
- Certificate-based identity more secure than bearer tokens
- Certificate subject and SANs provide fine-grained identity
- Integration with service mesh for zero-trust architecture

**Dynamic Secret Lease Management**:
- All dynamic secrets have time-bound leases
- Leases automatically revoked upon expiration
- Lease renewal requires valid Vault token
- Lease IDs are unique and non-guessable
- Revoked secrets immediately invalidated

**Compliance and Audit**:
- All certificate issuance events logged with requesting entity
- Certificate serial numbers tracked for audit purposes
- Revocation events logged with reason and timestamp
- Compliance with industry standards (PCI-DSS, SOC2, HIPAA)
- Cryptographic audit trail prevents tampering

### Best Practices for PKI and Certificates

**Certificate Configuration**:
- Use RSA 2048-bit or higher for production certificates
- Consider ECDSA for performance-critical applications
- Include appropriate Subject Alternative Names (SANs) for all hostnames
- Use URI SANs for SPIFFE identity in service mesh
- Configure appropriate certificate TTL based on rotation capability
- Use separate PKI roles for different environments (dev, staging, prod)

**Certificate Rotation**:
- Implement proactive rotation at 80% of certificate TTL
- Automate certificate rotation to eliminate manual processes
- Test certificate rotation in non-production environments first
- Implement graceful handling of certificate reload without downtime
- Monitor certificate expiration and alert before expiration
- Revoke old certificates after successful rotation

**Private Key Management**:
- Never log or persist private keys to disk
- Clear private keys from memory when no longer needed
- Use memory-safe programming practices to prevent key leakage
- Avoid transmitting private keys over unencrypted channels
- Generate private keys with sufficient entropy
- Use PKCS#8 format for private key storage in memory

**Certificate Validation**:
- Always validate certificate chain to trusted root CA
- Check Certificate Revocation List (CRL) or use OCSP
- Validate certificate expiration before use
- Verify certificate hostname matches expected identity
- Enforce TLS version 1.2 or higher
- Use strong cipher suites (disable weak ciphers)

**PKI Infrastructure**:
- Secure root CA private key in Hardware Security Module (HSM)
- Use separate intermediate CAs for different purposes
- Implement CA key rotation every 3-5 years
- Maintain offline backup of CA certificates and configurations
- Document CA hierarchy and trust relationships
- Implement CA disaster recovery procedures

**Monitoring and Alerting**:
- Monitor certificate issuance rate for anomalies
- Alert on certificate expiration within threshold (7 days)
- Track certificate revocation events
- Monitor CRL size and distribution
- Alert on PKI role policy violations
- Track certificate usage patterns per service

**Certificate Distribution**:
- Distribute CA certificates via secure channels
- Publish CRL to highly available storage (S3, CDN)
- Implement CRL caching to reduce load
- Use OCSP stapling for performance
- Automate CA certificate trust distribution to clients
- Version CA certificates for rollover scenarios

**Integration Security**:
- Use Vault policies to restrict certificate issuance permissions
- Limit certificate issuance to authorized Lambda functions only
- Implement rate limiting on certificate issuance API
- Use separate Vault tokens for certificate operations
- Log all certificate-related API calls for audit
- Implement circuit breakers for Vault PKI API calls

**Incident Response**:
- Maintain playbook for compromised certificates
- Implement emergency certificate revocation procedures
- Automate CRL distribution during security incidents
- Notify affected services of certificate revocation
- Investigate certificate compromise root cause
- Reissue certificates after security incident resolution

### Dynamic Secrets vs Static Secrets Comparison

| Aspect | Static Secrets | Dynamic Secrets (PKI) |
|--------|---------------|----------------------|
| **Provisioning** | Manual configuration | Automatic generation on-demand |
| **Lifetime** | Long-lived (months to years) | Short-lived (days to weeks) |
| **Rotation** | Manual or scheduled | Automatic on expiration |
| **Revocation** | Manual process | Automatic via lease expiration |
| **Credential Sprawl** | High (same credential reused) | Low (unique per request) |
| **Compromise Impact** | High (long exposure window) | Low (short exposure window) |
| **Audit Complexity** | Difficult to track usage | Complete lifecycle audit trail |
| **Management Overhead** | High (manual rotation) | Low (automated lifecycle) |

This design provides a robust, secure foundation for Lambda functions to access both static and dynamic secrets from HashiCorp Vault using AWS IAM authentication, with comprehensive support for digital certificate lifecycle management, eliminating the need for static credentials while maintaining strong security controls and audit capabilities.
