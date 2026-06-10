# PI/PO → CPI Migration Assessment Report

_Generated: 2026-06-09 21:25_

## Executive Summary

| Metric | Value |
|--------|-------|
| Total interfaces | **18** |
| 🟢 Low complexity | 0 |
| 🟡 Medium complexity | 11 |
| 🔴 High complexity | 7 |
| **Total effort estimate** | **134.9 days** |
| Destination targets | `s4hana_cloud` |

---

## Interface Inventory

| Interface | Sender | Receiver | Complexity | Effort | Pattern |
|-----------|--------|----------|------------|--------|---------|
| RCI093_SuccessFactors_to_OpenText_SuccessFactors |  () | Send_Mail_CompletedRun (Mail) | 🔴 HIGH | 44.9d | Point-to-Point HTTP/SOAP |
| OAUTH Token Validation V2 | Sender (HTTPS) | Receiver (HTTP) | 🔴 HIGH | 11.0d | Point-to-Point HTTP/SOAP |
| OAUTH Token Revoke V2 | Sender (HTTPS) | Receiver (HTTP) | 🔴 HIGH | 11.0d | Point-to-Point HTTP/SOAP |
| OAUTH Token Validation Backend V2 | Sender (SOAP) | Receiver (HTTP) | 🔴 HIGH | 9.2d | Point-to-Point HTTP/SOAP |
| RCI093_Clear_CustomFields |  () | Receiver (SuccessFactors) | 🔴 HIGH | 9.2d | Point-to-Point HTTP/SOAP |
| OAUTH Token Revoke | Sender (HTTPS) | Receiver1 (HTTP) | 🔴 HIGH | 8.3d | Point-to-Point HTTP/SOAP |
| OAUTH Token Validation | Sender (HTTPS) | Receiver (HTTP) | 🔴 HIGH | 8.3d | Point-to-Point HTTP/SOAP |
| Employee Services - Terminate | Sender (SOAP) | Receiver1 (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| OAUTH Token Validation Backend | Sender (SOAP) | Receiver1 (HTTP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employee Services - Create | Sender (SOAP) | Receiver1 (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employment Information - Pay Date File | Sender (SOAP) | Receiver1 (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employee Services - Update | Sender (SOAP) | Receiver1 (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employee Services - Terminate V2 | Sender (SOAP) | Receiver (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employee Services - Create V2 | Sender (SOAP) | Receiver (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employment Information - Pay Date File V2 | Sender (SOAP) | Receiver (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| Employee Services - Update V2 | Sender (SOAP) | Receiver (SOAP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| OAUTH Token V2 | Sender (HTTPS) | Receiver (HTTP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |
| OAUTH Token | Sender (HTTPS) | Receiver1 (HTTP) | 🟡 MEDIUM | 3d | Point-to-Point HTTP/SOAP |

---

## Destination: SAP S/4HANA Cloud (Public Edition)

| Interface | Sender→CPI | Receiver→CPI | Warnings | Hub Matches |
|-----------|-----------|--------------|----------|-------------|
| RCI093_SuccessFactors_to_OpenText_SuccessFactors | → | Mail→Mail | ⚠ 2 | 5 |
| OAUTH Token Validation V2 | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |
| OAUTH Token Revoke V2 | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |
| OAUTH Token Validation Backend V2 | SOAP→SOAP | HTTP→HTTPS | ✓ | 1 |
| RCI093_Clear_CustomFields | → | SuccessFactors→SuccessFactors | ⚠ 2 | 5 |
| OAUTH Token Revoke | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |
| OAUTH Token Validation | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |
| Employee Services - Terminate | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| OAUTH Token Validation Backend | SOAP→SOAP | HTTP→HTTPS | ✓ | 1 |
| Employee Services - Create | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| Employment Information - Pay Date File | SOAP→SOAP | SOAP→SOAP | ✓ | 1 |
| Employee Services - Update | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| Employee Services - Terminate V2 | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| Employee Services - Create V2 | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| Employment Information - Pay Date File V2 | SOAP→SOAP | SOAP→SOAP | ✓ | 1 |
| Employee Services - Update V2 | SOAP→SOAP | SOAP→SOAP | ✓ | 2 |
| OAUTH Token V2 | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |
| OAUTH Token | HTTPS→HTTPS | HTTP→HTTPS | ✓ | 0 |

### Pre-built Hub content for SAP S/4HANA Cloud (Public Edition)

- **Employee Services - Terminate** — [Employee Master Data Replication](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_Employee_Replication) `IntegrationFlow`
- **Employee Services - Terminate** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`
- **Employee Services - Create** — [Employee Master Data Replication](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_Employee_Replication) `IntegrationFlow`
- **Employee Services - Create** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`
- **Employment Information - Pay Date File** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`
- **OAUTH Token Validation Backend** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`
- **Employee Services - Update** — [Employee Master Data Replication](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_Employee_Replication) `IntegrationFlow`
- **Employee Services - Update** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`
- **Employee Services - Terminate V2** — [Employee Master Data Replication](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_Employee_Replication) `IntegrationFlow`
- **Employee Services - Terminate V2** — [Payment Advice Processing S4HANA Cloud](https://api.sap.com/package/SAPS4HANACloud/integrationflow/S4HC_PaymentAdvice) `IntegrationFlow`

---

## High Complexity — Action Required

### OAUTH Token Revoke
- **Score:** 26 | **Effort:** 8.3 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - 13 steps, 0 local process(es), 0 route(s), 1 exception subprocess(es), 2 mapping(s), 4 script(s), 1 receiver(s)

### OAUTH Token Validation
- **Score:** 26 | **Effort:** 8.3 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - 13 steps, 0 local process(es), 0 route(s), 1 exception subprocess(es), 2 mapping(s), 4 script(s), 1 receiver(s)

### OAUTH Token Validation V2
- **Score:** 35 | **Effort:** 11.0 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - 17 steps, 0 local process(es), 2 route(s), 1 exception subprocess(es), 2 mapping(s), 5 script(s), 1 receiver(s)

### OAUTH Token Revoke V2
- **Score:** 35 | **Effort:** 11.0 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - 17 steps, 0 local process(es), 2 route(s), 1 exception subprocess(es), 2 mapping(s), 5 script(s), 1 receiver(s)

### OAUTH Token Validation Backend V2
- **Score:** 29 | **Effort:** 9.2 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - 15 steps, 0 local process(es), 2 route(s), 1 exception subprocess(es), 0 mapping(s), 3 script(s), 1 receiver(s)

### RCI093_Clear_CustomFields
- **Score:** 29 | **Effort:** 9.2 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - Unknown sender adapter '' — manual review needed.
  - Unknown receiver adapter 'SuccessFactors' — manual review needed.
  - 5 steps, 0 local process(es), 0 route(s), 0 exception subprocess(es), 0 mapping(s), 2 script(s), 3 receiver(s)

### RCI093_SuccessFactors_to_OpenText_SuccessFactors
- **Score:** 148 | **Effort:** 44.9 days
- **Pattern:** Point-to-Point HTTP/SOAP
- **Notes:**
  - Unknown sender adapter '' — manual review needed.
  - 54 steps, 3 local process(es), 12 route(s), 3 exception subprocess(es), 7 mapping(s), 9 script(s), 14 receiver(s)
