# Correlation ID Design for Booking Transactions Flow

## Table of Contents
1. [Modification History](#modification-history)
2. [Overview](#overview)
3. [Design Principles](#design-principles)
4. [Correlation ID Format](#correlation-id-format)
5. [Implementation by Component](#implementation-by-component)
6. [Logging and Monitoring](#logging-and-monitoring)
7. [Complete Flow Summary](#complete-flow-summary)
8. [Indicative Code Examples](#indicative-code-examples)
9. [Best Practices](#best-practices)
10. [Testing Recommendations](#testing-recommendations)
11. [Troubleshooting Guide](#troubleshooting-guide)
12. [Future Enhancements](#future-enhancements)
13. [Conclusion](#conclusion)

## Modification History

| Version | Date       | Author   | Description of Changes                                                                                                                                                                    |
| ------- | ---------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2024-01-15 | TDD Team | Initial document creation with Correlation ID design specifications                                                                                                                       |
| 1.1     | 2025-01-15 | TDD Team | Added Storage Clarification section:<br/>- Clarified that Correlation ID does not get stored in DynamoDB or PostgreSQL databases<br/>- Updated Best Practices section with storage policy |

## Overview
This document outlines the correlation ID strategy for API requests in the booking flow, from Axis UI through ui-component-service, core services, Submit API, S3, EventBridge, SQS, Orchestrator, and Step Functions. Each API request gets its own correlation ID for tracing purposes.

## Design Principles

### 1. CorrelationId per API Request
- **One correlation ID per API request**
- Each API call gets its own unique correlation ID
- Enables tracing and debugging of individual requests
- Does not span the entire booking transaction

### 2. Generation Strategy
- **Primary**: Axis UI generates correlation ID for each API request
- **Fallback**: API generates correlation ID if not provided by UI (valid only for Submit/Ingestor lambda)

### 3. Storage Clarification

**IMPORTANT**: Correlation ID does not get stored in DynamoDB or PostgreSQL database. It is used solely for request tracing and logging purposes. The correlation ID is:
- Passed in HTTP headers (`X-Correlation-ID`)
- Logged to CloudWatch Logs, Splunk, and AWS X-Ray
- Propagated through the request chain
- Included in all log entries for a given request
- Stored in S3 object metadata for event tracing

The correlation ID is transient and exists only for the duration of the request lifecycle. **It is not persisted in DynamoDB or PostgreSQL databases.**

---

## Correlation ID Format

### Format
```
{uuid-v4}
```

**Example**: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`

### Description
- **UUID v4**: Standard UUID version 4 format for guaranteed uniqueness across all services

---

## Implementation by Component

### 1. Axis UI (Booking Channel)

#### Generation
- UI generates correlation ID for each API request using UUID v4
- Correlation ID format: `{uuid-v4}`

#### HTTP Interceptor
- HTTP interceptor adds `X-Correlation-ID` header to each outgoing request
- If correlation ID is not present, generate a new one

#### Usage in Booking Flow
- Each API call generates a new correlation ID
- Correlation ID is added to request headers automatically

---

### 2. Lambda Services (API Gateway + Lambda)

#### Middleware/Handler (Node.js/TypeScript)
- Extract correlation ID from `X-Correlation-ID` header
- If not provided, generate new correlation ID using UUID v4
- Attach correlation ID to request object
- Add correlation ID to response headers
- Log correlation ID to CloudWatch

#### Lambda Function Handler
- Extract or generate correlation ID from request headers
- Log all processing steps with correlation ID to CloudWatch Logs
- Return correlation ID in response headers

---

### 3. Submit API (Lambda)

#### Lambda Function Handler
- Extract correlation ID from `X-Correlation-ID` HTTP header of Submit/Ingestor API request
- Log processing steps with correlation ID
- Upload POM to S3 with correlation ID included in object metadata(S3 Event Notification)
- Return correlation ID in response headers

#### S3 Upload with Event Notification
- Store correlation ID in S3 object metadata as `x-correlation-id` (extracted from HTTP header)
- When S3 object is uploaded, S3 generates an Event notification containing the object metadata
- This S3 Event message includes the correlation ID and is intercepted by EventBridge
- Log S3 key and correlation ID to CloudWatch

---

### 4. S3 Event → EventBridge → SQS

#### S3 Object Metadata
- Include correlation ID in S3 metadata: `x-correlation-id`
- Include source and timestamp in metadata

Example S3 metadata:
```json
{
  "x-correlation-id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "source": "submit-api",
  "timestamp": "2025-01-12T14:30:25Z"
}
```

#### EventBridge Rule
- Configure EventBridge to capture S3 Object Created events
- Filter by bucket name: `booking-pom-bucket`

EventBridge Rule pattern:
```json
{
  "source": ["aws.s3"],
  "detail-type": ["Object Created"],
  "detail": {
    "bucket": {
      "name": ["booking-pom-bucket"]
    }
  }
}
```

#### EventBridge Target (SQS)
- EventBridge sends event to SQS queue
- **Correlation ID is passed in the SQS message from S3 metadata**
- Message includes bucket name, S3 key, and correlation ID from metadata

Example SQS message:
```json
{
  "version": "0",
  "id": "event-id",
  "source": "aws.s3",
  "detail-type": "Object Created",
  "detail": {
    "version": "0",
    "bucket": {
      "name": "booking-pom-bucket"
    },
    "object": {
      "key": "pom/pom.json",
      "size": 12345,
      "etag": "d41d8cd98f00b204e9800998ecf8427e"
    },
    "object-metadata": {
      "x-correlation-id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "source": "submit-api",
      "timestamp": "2025-01-12T14:30:25Z"
    },
    "request-id": "request-id",
    "requester": "AWS:LAMBDA",
    "source-ip-address": "10.0.0.1",
    "reason": "PutObject"
  }
}
```

**Note**: The correlation ID is included in the `object-metadata` section of the SQS message, extracted from S3 object metadata.

---

### 5. Orchestrator (Lambda reading from SQS)

#### SQS Message Processing
- Read messages from SQS queue
- Extract correlation ID from SQS message `object-metadata` section
- Log processing with correlation ID

#### Step Function Invocation
- Start Step Function execution with correlation ID in input
- Include correlation ID in execution name: `execution-{correlationId}-{timestamp}`
- Pass POM data and correlation ID to Step Function

---

### 6. Step Function

#### State Machine Definition
- Define states for POM processing: ValidateInput, PersistToInscore
- Pass correlation ID to each Lambda function as parameter: `correlationId.$": "$.correlationId"`
- Log correlation ID at each state transition

#### Lambda Functions in Step Function

**Validation Function:**
- Extract correlation ID and POM from event
- Log validation steps with correlation ID
- Validate POM and return validation status

**Persistence Function:**
- Extract correlation ID and data from event
- Process and persist data as needed
- Log success/failure with correlation ID

---

## Logging and Monitoring

**IMPORTANT**: The correlationId MUST be logged in all monitoring and observability platforms to enable request tracing across services:
- **CloudWatch Logs**: For AWS Lambda and service logs
- **Splunk**: For centralized log aggregation and analysis
- **AWS X-Ray**: For distributed tracing and performance monitoring

### CloudWatch Logs
**Best Practices for CloudWatch Logging:**
- Always include `correlationId` as a top-level field in JSON logs
- Use structured logging (JSON format) for easier querying
- Log at key transaction points: request received, processing started, processing completed, errors
- Include context: bookingId, userId, transactionType, service, action, status

### CloudWatch Insights Queries
- Find all logs for a specific correlation ID
- Find errors for a specific correlation ID
- Parse JSON logs and extract correlation ID, service, action, status
- Sort logs by timestamp to trace request flow

### Splunk Integration
Details to be confirmed.

### AWS X-Ray Tracing
**X-Ray Integration:**
- Enable AWS X-Ray for distributed tracing with correlation ID
- Add correlation ID as annotation (indexed for filtering)
- Add bookingId, userId as annotations
- Add transaction metadata (correlationId, bookingId, transactionType, timestamp)
- Log X-Ray trace ID with correlation ID

**X-Ray Service Map Configuration:**
- Annotations (correlationId, bookingId) are indexed and can be filtered in X-Ray console
- Use X-Ray filter expressions to find requests by correlation ID
- Track service dependencies and latencies for specific correlation IDs

### Cross-Platform Correlation
To enable correlation across CloudWatch, Splunk, and X-Ray:

1. **Always log correlationId** in every service and at every request point
2. **Use consistent field naming**: `correlationId` (not correlation_id or CorrelationID)
3. **Link X-Ray trace ID with correlationId**: Log both in CloudWatch/Splunk for cross-referencing
4. **Create unified dashboards**: Link CloudWatch Insights, Splunk, and X-Ray by correlationId

---

## Complete Flow Summary

```
1. Axis UI
   ├─ Generate Correlation ID per API request: {uuid-v4}
   └─ Add to HTTP Header: X-Correlation-ID

2. Lambda Services (API Gateway + Lambda)
   ├─ Extract from Header or Generate using UUID v4
   └─ Log correlation ID to CloudWatch Logs

3. Submit API (Lambda)
   ├─ Extract correlation ID from X-Correlation-ID HTTP header
   ├─ Upload to S3 (Key: pom/{timestamp}.json) with Metadata: x-correlation-id
   └─ S3 generates Event notification with object metadata (including correlation ID)

4. S3 → EventBridge → SQS
   ├─ EventBridge intercepts S3 Event message containing correlation ID from object metadata
   └─ SQS Message contains correlation ID extracted from S3 Event

5. Orchestrator (Lambda)
   ├─ Read from SQS
   ├─ Extract correlation ID from SQS message object-metadata section
   ├─ Read POM from S3
   └─ Start Step Function with correlationId in input

6. Step Function
   ├─ Pass correlationId to Lambda functions
   ├─ Log at each state transition
   └─ Include in execution name

7. Persistence Layer (Lambda)
   ├─ Receive correlationId from Step Function
   └─ Process and persist data with correlation ID logging
```

---

## Indicative Code Examples

**Note**: The following code examples are indicative and for reference purposes only.

### Orchestrator Lambda (SQS to Step Function)

```typescript
import { SQSEvent } from 'aws-lambda';
import { S3, StepFunctions } from 'aws-sdk';

const s3 = new S3();
const stepFunctions = new StepFunctions();

export const handler = async (event: SQSEvent) => {
  for (const record of event.Records) {
    const message = JSON.parse(record.body);
    const correlationId = message.detail['object-metadata']['x-correlation-id'];

    console.log(`[Orchestrator] Processing with Correlation ID: ${correlationId}`);

    // Read POM from S3
    const s3Key = message.detail.object.key;
    const bucket = message.detail.bucket.name;
    const pomData = await s3.getObject({ Bucket: bucket, Key: s3Key }).promise();
    const pom = JSON.parse(pomData.Body?.toString() || '{}');

    // Start Step Function
    await stepFunctions.startExecution({
      stateMachineArn: process.env.STATE_MACHINE_ARN,
      input: JSON.stringify({ correlationId, pom }),
      name: `execution-${correlationId}-${Date.now()}`
    }).promise();

    console.log(`[Orchestrator] Started Step Function with Correlation ID: ${correlationId}`);
  }
};
```

### Step Function State Machine Definition

```json
{
  "Comment": "Booking POM Processing with Correlation ID",
  "StartAt": "ValidateInput",
  "States": {
    "ValidateInput": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:validate-function",
      "Parameters": {
        "correlationId.$": "$.correlationId",
        "pom.$": "$.pom"
      },
      "Next": "PersistData"
    },
    "PersistData": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:persist-function",
      "Parameters": {
        "correlationId.$": "$.correlationId",
        "data.$": "$.pom"
      },
      "End": true
    }
  }
}
```

### Persistence Lambda Function

```typescript
export const handler = async (event: any) => {
  const { correlationId, data } = event;

  console.log(`[Persistence] Processing with Correlation ID: ${correlationId}`);

  try {
    // Process and persist data
    await persistData(data);

    console.log(`[Persistence] Successfully processed with Correlation ID: ${correlationId}`);

    return {
      correlationId,
      status: 'SUCCESS'
    };
  } catch (error) {
    console.error(`[Persistence] Error with Correlation ID ${correlationId}:`, error);
    throw error;
  }
};

async function persistData(data: any) {
  // Implementation for data persistence
  // This could involve writing to databases, calling APIs, etc.
}
```

---

## Best Practices

### 1. Header Naming
- Use standard header: `X-Correlation-ID`
- Case-insensitive handling in code

### 2. Uniqueness
- Use UUID v4 for guaranteed uniqueness
- Include timestamp for readability

### 3. Propagation
- Pass through ALL service boundaries
- Include in logs, databases, and message queues

### 4. Fallback Generation
- Always generate if not provided
- Log warnings when auto-generated

### 5. Storage
- **Do NOT store in DynamoDB or PostgreSQL databases**
- Include in S3 metadata (for event tracing only)
- Use only for logging and request tracing purposes

### 6. Logging
- Structured logging with correlation ID
- CloudWatch Log Groups per service
- Use CloudWatch Insights for tracing

### 7. Error Handling
- Include correlation ID in error responses
- Log errors with correlation ID
- Enable debugging and support

---

## Testing Recommendations

### Unit Tests
- Test correlation ID generation format and validity
- Verify UUID v4 uniqueness
- Test fallback generation when not provided

### Integration Tests
- Verify correlation ID propagates through entire flow
- Test fallback generation when not provided
- Validate storage in all databases

### Load Tests
- Ensure correlation IDs remain unique under high load
- Verify no performance impact from ID generation

---

## Troubleshooting Guide

### Issue: Correlation ID not propagating
- Check HTTP interceptor is registered
- Verify middleware extracts from headers correctly
- Ensure S3 metadata is set

### Issue: Duplicate correlation IDs
- Verify UUID generation is working
- Check for clock synchronization issues

### Issue: Cannot find request in logs
- Verify correlation ID is logged consistently
- Check CloudWatch log retention settings
- Use CloudWatch Insights queries

---

## Future Enhancements

1. **Distributed Tracing Integration**
   - Integrate with AWS X-Ray
   - Consider OpenTelemetry

2. **Automatic Retry Handling**
   - Preserve correlation ID across retries
   - Track retry attempts

---

## Conclusion

This design ensures:
- **Request-level traceability** across all services
- **Automatic generation** when UI doesn't provide ID
- **Consistent propagation** through AWS services via message metadata
- **Efficient querying** via database indexes
- **Comprehensive logging** for debugging

Implement this design incrementally, starting with UI and API layers, then progressively adding to downstream services.
