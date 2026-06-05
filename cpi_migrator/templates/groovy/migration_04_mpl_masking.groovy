/*
 * migration_04_mpl_masking.groovy
 *
 * Pattern: Log payload to the Message Processing Log while masking PII
 * (credit card numbers, IBANs, emails). Compliance-critical — every
 * regulated client (finance, healthcare, EU/GDPR) needs this.
 *
 * Use case: Consultants want full-payload logging for troubleshooting but
 * cannot store raw PII in MPL attachments (auditors will flag it). This
 * masks sensitive patterns before attaching.
 *
 * Verified: STATIC ONLY. MessageLog API via ITApiFactory (pitfall_c
 * correct form). The masking regexes are conservative — review against
 * your data's actual PII patterns before relying on them for compliance.
 *
 * Pitfall handled: pitfall_a (setBody after read), pitfall_c (explicit
 * MessageLogFactory). Masks a COPY for logging — original body is NEVER
 * altered, so the receiver still gets the real data.
 *
 * IMPORTANT: Masking regexes are best-effort, not a compliance guarantee.
 * They catch common formats but won't catch every PII representation.
 * Treat as defence-in-depth, not the only control.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.ITApiFactory
import com.sap.it.api.msglog.MessageLog
import com.sap.it.api.msglog.MessageLogFactory

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)   // original unchanged — receiver gets real data

    // Build a masked COPY purely for logging
    String masked = maskPii(body)

    def factory = ITApiFactory.getService(MessageLogFactory, null)
    if (factory != null) {
        MessageLog log = factory.getMessageLog(message)
        if (log != null && masked.length() < 200_000) {
            log.addAttachmentAsString("MaskedPayload", masked, "text/plain")
            log.setStringProperty("PayloadMasked", "true")
        }
    }
    return message
}

private String maskPii(String input) {
    String out = input
    // Credit card (13-16 digits, optional separators) -> keep last 4
    out = out.replaceAll(/\b(?:\d[ -]?){12,15}(\d{4})\b/) { full, last4 ->
        "************" + last4
    }
    // IBAN (2 letters + 2 digits + up to 30 alnum) -> keep country + last 4
    out = out.replaceAll(/\b([A-Z]{2})\d{2}[A-Z0-9]{4,26}([A-Z0-9]{4})\b/) { full, cc, last4 ->
        cc + "**************" + last4
    }
    // Email -> mask local part
    out = out.replaceAll(/\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/) { full, first, domain ->
        first + "***@" + domain
    }
    return out
}
