/*
 * migration_05_aggregation_batch.groovy
 *
 * Pattern: Collect related messages into a batch using the data store,
 * emit one combined message when the batch is complete. This is how PI's
 * collect/ccBPM aggregation patterns migrate to CPI.
 *
 * Use case: PI had a ccBPM that collected all line items for an order
 * across multiple inbound messages, then sent one consolidated document.
 * CPI has a Gather/Aggregator step, but for custom completion logic
 * (e.g. "wait until a control record arrives") Groovy + data store gives
 * full control.
 *
 * Verified: STATIC ONLY. DataStore API is CPI-specific — test the
 * accumulation + retrieval cycle in a tenant. This and the idempotency
 * template are the two highest-risk to deploy untested.
 *
 * Pitfall handled: setBody after read. Stateful by design (uses data
 * store) — this is the documented CPI way to do stateful aggregation,
 * but note it breaks the "Groovy should be pure" guideline ON PURPOSE
 * because CPI offers no cleaner primitive for custom-completion batching.
 *
 * Design note: This is a simplified accumulator. Production batching needs
 * a timeout/sweeper (a separate scheduled iFlow that flushes stale partial
 * batches) so incomplete batches don't accumulate forever.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.asdk.datastore.DataStoreService
import com.sap.it.api.asdk.datastore.DataBean
import com.sap.it.api.asdk.datastore.DataConfig
import com.sap.it.api.ITApiFactory
import groovy.json.JsonSlurper
import groovy.json.JsonOutput

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)

    def batchKey = message.getProperty("BatchKey")
    if (!batchKey) {
        throw new IllegalStateException("BatchKey property required for aggregation")
    }
    int expectedCount = (message.getProperty("BatchExpectedCount") ?: "0") as Integer

    def service = ITApiFactory.getService(DataStoreService, null)
    if (service == null) {
        throw new IllegalStateException("DataStoreService unavailable — outside CPI runtime?")
    }

    final String storeName = "BatchAccumulator"
    final String storeId = batchKey.toString()

    // Read existing accumulator (a JSON array of payload fragments)
    def existing = service.get(storeName, storeId)
    List fragments = []
    if (existing != null) {
        def existingText = new String(existing.getDataAsArray(), "UTF-8")
        fragments = new JsonSlurper().parseText(existingText) as List
    }

    // Add this message's fragment
    fragments.add(body)

    if (expectedCount > 0 && fragments.size() >= expectedCount) {
        // Batch complete — emit combined, clear accumulator
        def combined = new StringBuilder("<Batch>")
        fragments.each { combined.append(it) }
        combined.append("</Batch>")
        message.setBody(combined.toString())
        message.setProperty("BatchComplete", "true")
        message.setProperty("BatchSize", fragments.size() as String)
        try {
            service.delete(storeName, storeId)
        } catch (Exception delEx) {
            // Non-fatal: the scheduled sweeper will clean up. But record
            // WHY (pitfall_b — never swallow silently) so it's visible in MPL.
            message.setProperty("BatchDeleteWarning", delEx.message)
        }
    } else {
        // Batch incomplete — persist and signal hold
        DataBean bean = new DataBean()
        bean.setDataAsArray(JsonOutput.toJson(fragments).getBytes("UTF-8"))
        DataConfig config = new DataConfig()
        config.setStoreName(storeName)
        config.setId(storeId)
        config.setOverwrite(true)
        service.put(bean, config)
        message.setProperty("BatchComplete", "false")
        message.setProperty("BatchSize", fragments.size() as String)
        // iFlow Router should route BatchComplete=false to an end event
    }
    return message
}
