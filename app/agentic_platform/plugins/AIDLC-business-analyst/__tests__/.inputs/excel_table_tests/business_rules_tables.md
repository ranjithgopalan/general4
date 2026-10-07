# Business Rules for Insurance Policy System

## Table 1: Age Validation Rules

| Rule ID | Field Name | Data Type | Min Value | Max Value | Validation Message | Severity |
|---------|------------|-----------|-----------|-----------|-------------------|----------|
| AGE_001 | Date of Birth | Date | 18 years ago | 75 years ago | Insured must be between 18 and 75 years old | High |
| AGE_002 | Beneficiary Age | Integer | 0 | 120 | Beneficiary age must be realistic | Medium |
| AGE_003 | Dependent Age | Integer | 0 | 26 | Dependent must be under 26 years old | High |

## Table 2: Coverage Amount Rules

| Rule ID | Policy Type | Min Coverage | Max Coverage | Increment | Formula | Notes |
|---------|-------------|--------------|--------------|-----------|---------|-------|
| COV_001 | Term Life | $10,000 | $5,000,000 | $1,000 | Coverage must be in increments of $1,000 | Standard policy |
| COV_002 | Whole Life | $25,000 | $2,000,000 | $5,000 | Coverage must be in increments of $5,000 | Premium product |
| COV_003 | Health | $5,000 | $10,000,000 | $1,000 | Annual maximum coverage | ACA compliant |
| COV_004 | Auto | $15,000 | $1,000,000 | $5,000 | Liability coverage minimum per state law | Varies by state |

## Table 3: Premium Calculation Factors

| Factor ID | Factor Name | Factor Type | Base Rate | Age Multiplier | Risk Multiplier | Formula |
|-----------|-------------|-------------|-----------|----------------|-----------------|---------|
| PREM_001 | Base Premium | Coverage-based | 0.001 | N/A | 1.0 | Coverage × 0.001 |
| PREM_002 | Age Factor | Age-based | N/A | (Age - 18) × 0.005 | 1.0 | Added to base |
| PREM_003 | Low Risk | Risk-based | N/A | N/A | 1.0 | Multiplier applied to total |
| PREM_004 | Medium Risk | Risk-based | N/A | N/A | 1.5 | Multiplier applied to total |
| PREM_005 | High Risk | Risk-based | N/A | N/A | 2.0 | Multiplier applied to total |

## Table 4: Policy Status Workflow

| Current Status | Action | Next Status | Required Approval | Reversible | Conditions |
|----------------|--------|-------------|-------------------|------------|------------|
| Draft | Submit | Pending Review | None | Yes | All required fields completed |
| Pending Review | Approve | Active | Manager | No | No validation errors |
| Pending Review | Reject | Draft | Manager | Yes | Reason required |
| Active | Suspend | Suspended | Manager | Yes | Reason required |
| Active | Cancel | Cancelled | Manager | No | Reason required, 30-day notice |
| Suspended | Reactivate | Active | Manager | Yes | Outstanding issues resolved |
| Active | Expire | Expired | Automatic | No | End date reached |

## Table 5: Required Fields by Policy Type

| Policy Type | Field Name | Required | Data Type | Max Length | Validation Rule | Default Value |
|-------------|------------|----------|-----------|------------|-----------------|---------------|
| Term Life | Policy Holder Name | Yes | String | 100 | Alpha + spaces only | N/A |
| Term Life | Date of Birth | Yes | Date | N/A | AGE_001 | N/A |
| Term Life | Coverage Amount | Yes | Currency | N/A | COV_001 | $100,000 |
| Term Life | Beneficiary Name | Yes | String | 100 | Alpha + spaces only | N/A |
| Term Life | Term Length | Yes | Integer | N/A | 10, 20, or 30 years | 20 |
| Health | Policy Holder Name | Yes | String | 100 | Alpha + spaces only | N/A |
| Health | Date of Birth | Yes | Date | N/A | AGE_001 | N/A |
| Health | Coverage Amount | Yes | Currency | N/A | COV_003 | $1,000,000 |
| Health | Deductible | Yes | Currency | N/A | $500-$10,000 | $1,000 |
| Health | Pre-existing Conditions | No | Text | 1000 | Free text | None |

## Table 6: Risk Assessment Criteria

| Risk Level | Age Range | Coverage Range | Pre-existing Conditions | Occupation Category | Premium Multiplier |
|------------|-----------|----------------|-------------------------|---------------------|-------------------|
| Low | 18-35 | $10,000-$250,000 | None | Low-risk (Office, Teaching) | 1.0 |
| Low | 36-50 | $10,000-$100,000 | None | Low-risk | 1.0 |
| Medium | 18-35 | $250,001-$1,000,000 | None | Medium-risk (Sales, Healthcare) | 1.5 |
| Medium | 36-50 | $100,001-$500,000 | Minor conditions | Any | 1.5 |
| Medium | 51-65 | $10,000-$250,000 | None | Low-risk | 1.5 |
| High | Any | >$1,000,000 | Any | Any | 2.0 |
| High | 36-50 | Any | Major conditions | High-risk (Construction, Mining) | 2.0 |
| High | 51-75 | >$250,000 | Any | Any | 2.0 |

## Table 7: State-Specific Requirements

| State Code | State Name | Min Liability | Uninsured Motorist | PIP Required | Additional Requirements |
|------------|------------|---------------|-------------------|--------------|------------------------|
| CA | California | $15,000/$30,000 | Optional | No | Earthquake coverage available |
| TX | Texas | $30,000/$60,000 | Optional | No | Additional verification for high-risk |
| FL | Florida | $10,000/$20,000 | Required | Yes | Hurricane coverage recommended |
| NY | New York | $25,000/$50,000 | Required | No | Additional fraud prevention checks |
| MA | Massachusetts | $20,000/$40,000 | Required | Yes | Snow/ice coverage included |

## Table 8: Compliance Requirements Matrix

| Compliance Type | Regulation | Applicable Policy Types | Requirement | Audit Frequency | Penalty for Non-Compliance |
|----------------|------------|------------------------|-------------|-----------------|---------------------------|
| Federal | HIPAA | Health | Encrypt all PHI, BAA required | Annual | $100-$50,000 per violation |
| Federal | ACA | Health | Essential health benefits, no lifetime limits | Annual | Policy invalidation |
| State | DOI Filing | All | File rates with state DOI | Quarterly | Fines, license suspension |
| Industry | SOC 2 | All | Security controls, audit trail | Annual | Customer contract violations |
| Industry | PCI DSS | All with payments | Secure payment processing | Quarterly | Fines, processor restrictions |

## Table 9: System Performance Requirements

| Requirement ID | Metric | Measurement | Target | Current | Gap | Priority |
|---------------|--------|-------------|--------|---------|-----|----------|
| PERF_001 | Concurrent Users | Number of simultaneous active sessions | 500 | N/A | N/A | High |
| PERF_002 | Page Load Time | Time from request to fully rendered | <3 sec | N/A | N/A | High |
| PERF_003 | Search Response | Time to return search results | <2 sec | N/A | N/A | High |
| PERF_004 | Database Query | Average query execution time | <1 sec | N/A | N/A | Medium |
| PERF_005 | API Response | REST API response time | <500 ms | N/A | N/A | Medium |
| PERF_006 | Uptime | System availability percentage | 99.9% | N/A | N/A | Critical |

## Table 10: Error Codes and Messages

| Error Code | Error Category | User Message | Technical Details | Recommended Action | Severity |
|------------|---------------|--------------|-------------------|-------------------|----------|
| ERR_001 | Validation | Age must be between 18 and 75 years | Age validation failed (AGE_001) | Verify date of birth | High |
| ERR_002 | Validation | Coverage amount must be between $10,000 and $5,000,000 | Coverage validation failed (COV_001) | Adjust coverage amount | High |
| ERR_003 | Business Rule | Premium calculation failed | Formula error in PREM calculation | Contact system administrator | Critical |
| ERR_004 | Authorization | You do not have permission to perform this action | RBAC check failed | Request appropriate role from manager | Medium |
| ERR_005 | Integration | Unable to retrieve customer data from CRM | CRM API returned error 500 | Retry or contact support | High |
| ERR_006 | Data | Duplicate policy detected for this customer | Policy number already exists | Review existing policy | Medium |
