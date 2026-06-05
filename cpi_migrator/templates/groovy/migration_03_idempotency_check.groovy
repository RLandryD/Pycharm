/*
 * migration_03_idempotency_check.groovy
 *
 * Pattern: Duplicate detection via the CPI data store. Reads a unique
 * message key, checks if it's already been processed, and either marks it
 * for skipping or records it as seen. This is how PI's EOIO / exactly-once
 * semantics get rebuilt in CPI (which has no native exactly-once).
 *
 * Use case: A sender may redeliver the same message (network retry,
 * partner resend). Processing it twice would create duplicate orders.
 * This guards against that.
 *
 * Verified: STATIC ONLY. The DataStore API (com.sap.it.api.asdk.datastore)
 * is CPI-specific and its exact behaviour (visibility timeout, entry
 * expiry) MUST be verified in a tenant before production. This is the
 * highest-risk template to deploy untested.
 *
 * Pitfall handled: setBody after read. Sets a property the iFlow Router
 * branches on (IsDuplicate) rather than throwing — duplicates aren't
 * errors, they're a routing decision.
 *
 * Design note: This uses a "check then write" pattern which has a race
 * window under high concurrency. For strict exactly-once under parallel
 * processing, use the data store's atomic write with overwrite=false and
 * catch the collision — noted in comments below.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.asdk.datastore.DataStoreService
import com.sap.it.api.asdk.datastore.DataBean
import com.sap.it.api.asdk.datastore.DataConfig
import com.sap.it.api.ITApiFactory

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)

    // Unique key for this message — adapt to your dedup criteria
    def messageKey = message.getProperty("IdempotencyKey") ?:
                     message.getHeader("SAP_MessageProcessingLogID", String)
    if (!messageKey) {
        // No key to dedup on — let it through but flag for monitoring
        message.setProperty("IsDuplicate", "unknown")
        message.setProperty("IdempotencyWarning", "no IdempotencyKey available")
        return message
    }

    def service = ITApiFactory.getService(DataStoreService, null)
    if (service == null) {
        throw new IllegalStateException("DataStoreService unavailable — outside CPI runtime?")
    }

    final String storeName = message.getProperty("IdempotencyStore") ?: "ProcessedMessages"

    // Check if already seen
    def existing = service.get(storeName, messageKey.toString())
    if (existing != null) {
        message.setProperty("IsDuplicate", "true")
        // iFlow Router should route IsDuplicate=true to a no-op / log-and-end branch
        return message
    }

    // Not seen — record it. DataConfig controls expiry; set retention to
    // your dedup window (e.g. 7 days) via expirationPeriod.
    DataBean dataBean = new DataBean()
    dataBean.setDataAsArray(("processed:" + new Date().toString()).getBytes("UTF-8"))
    DataConfig config = new DataConfig()
    config.setStoreName(storeName)
    config.setId(messageKey.toString())
    config.setOverwrite(false)  // false => collision throws if another thread won the race
    try {
        service.put(dataBean, config)
        message.setProperty("IsDuplicate", "false")
    } catch (Exception raceEx) {
        // Another concurrent instance wrote first — treat as duplicate
        message.setProperty("IsDuplicate", "true")
        message.setProperty("IdempotencyRace", "true")
    }
    return message
}
