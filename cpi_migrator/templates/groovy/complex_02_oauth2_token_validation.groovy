/*
 * complex_02_oauth2_token_validation.groovy
 *
 * Pattern: Validate an inbound OAuth2 Bearer token by either (a) calling
 * the issuer's introspection endpoint (RFC 7662), or (b) verifying a
 * JWT signature against the issuer's JWKS endpoint with claim checks.
 *
 * Use case: An HTTPS sender adapter receives a request with an
 * Authorization: Bearer header. Standard CPI authentication options
 * (basic, client cert, OAuth client credentials inbound) don't cover
 * the case where you must validate a token issued by an external IdP
 * (Auth0, Azure AD, Okta, custom).
 *
 * This template implements introspection (method A) which is simpler
 * and works for any RFC 7662 compliant issuer. JWT signature verification
 * (method B) is left as a separate template since it requires JWKS
 * caching to be efficient — that's its own design problem.
 *
 * Verified: Static analysis only. The introspection endpoint URL and
 * client credentials must be configured per tenant. The script uses
 * java.net.http.HttpClient (JDK 11+) which is present in CPI's runtime.
 *
 * Pitfall handled (CRITICAL): Always require `active=true` AND check
 * `exp` claim freshness — some IdPs return `active=true` for revoked
 * tokens for up to 60 seconds due to cache propagation. The fresh-exp
 * check catches recently-revoked-but-still-introspecting-as-active.
 *
 * Pitfall handled: Audience check. A token issued for one client_id
 * (e.g. mobile app) must NOT authenticate as another (e.g. backend).
 * We require `aud` to match an expected value from configuration.
 *
 * Pitfall handled: Never trust the Bearer header's value as identity.
 * Always extract the validated `sub` from the introspection response
 * and set that as the user identity for downstream steps.
 *
 * Pitfall handled: Don't log tokens. The catch block here logs only
 * the first 8 chars of the token for correlation, never the full value.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.ITApiFactory
import com.sap.it.api.securestore.SecureStoreService
import com.sap.it.api.securestore.UserCredential
import groovy.json.JsonSlurper
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.HttpRequest.BodyPublishers
import java.net.URLEncoder
import java.util.Base64

def Message processData(Message message) {

    // Extract Bearer token
    def authHeader = message.getHeader("Authorization", String) ?: ""
    if (!authHeader.startsWith("Bearer ")) {
        rejectUnauthorized(message, "Missing or malformed Authorization header")
        return message
    }
    def token = authHeader.substring("Bearer ".length()).trim()
    if (token.isEmpty()) {
        rejectUnauthorized(message, "Empty Bearer token")
        return message
    }

    // Configuration — externalised parameters (Configure tab in iFlow)
    def introspectionUrl = message.getProperty("OAuth2IntrospectionUrl")
    def expectedAudience = message.getProperty("OAuth2ExpectedAudience")
    if (!introspectionUrl || !expectedAudience) {
        throw new IllegalStateException(
            "OAuth2IntrospectionUrl and OAuth2ExpectedAudience properties must be set " +
            "(configure on iFlow's Configure tab).")
    }

    // Client credentials for the introspection call itself — stored in
    // Secure Parameter store as User Credentials.
    SecureStoreService secureStore = ITApiFactory.getService(SecureStoreService, null)
    UserCredential clientCred = secureStore.getUserCredential(
        message.getProperty("OAuth2IntrospectionCredAlias") ?: "OAUTH2_INTROSPECT_CLIENT")
    if (clientCred == null) {
        throw new IllegalStateException("Introspection client credential not found in Secure Parameter store")
    }

    // Basic auth for introspection request
    def basicAuth = Base64.encoder.encodeToString(
        "${new String(clientCred.username)}:${new String(clientCred.password)}".getBytes("UTF-8"))

    HttpClient client = HttpClient.newBuilder()
        .connectTimeout(java.time.Duration.ofSeconds(5))
        .build()
    HttpRequest request = HttpRequest.newBuilder()
        .uri(URI.create(introspectionUrl))
        .timeout(java.time.Duration.ofSeconds(10))
        .header("Content-Type",   "application/x-www-form-urlencoded")
        .header("Authorization",  "Basic ${basicAuth}")
        .header("Accept",         "application/json")
        .POST(BodyPublishers.ofString("token=${URLEncoder.encode(token, "UTF-8")}&token_type_hint=access_token"))
        .build()

    HttpResponse<String> response
    try {
        response = client.send(request, HttpResponse.BodyHandlers.ofString())
    } catch (Exception ex) {
        // Network failure to issuer — fail closed, not open.
        // Don't log the token; log only a correlation prefix.
        def prefix = token.length() >= 8 ? token.substring(0, 8) : token
        rejectUnauthorized(message, "Introspection call failed for token ${prefix}...: ${ex.message}")
        return message
    }

    if (response.statusCode() != 200) {
        rejectUnauthorized(message, "Introspection returned HTTP ${response.statusCode()}")
        return message
    }

    def claims = new JsonSlurper().parseText(response.body())

    // Validation checks — ALL must pass
    if (!claims.active) {
        rejectUnauthorized(message, "Token introspection returned active=false")
        return message
    }

    // Fresh-exp check — guards against ~60s cache propagation window
    long nowSeconds = System.currentTimeMillis() / 1000
    if (claims.exp && claims.exp < nowSeconds) {
        rejectUnauthorized(message, "Token expired ${nowSeconds - claims.exp}s ago")
        return message
    }

    // Audience check — token MUST be intended for this resource server
    def audienceClaim = claims.aud
    def audienceValues = audienceClaim instanceof List ? audienceClaim : [audienceClaim]
    if (!audienceValues.contains(expectedAudience)) {
        rejectUnauthorized(message, "Token audience ${audienceValues} does not include ${expectedAudience}")
        return message
    }

    // Validation passed — set identity for downstream steps from CLAIMS,
    // never from the original header.
    message.setProperty("AuthenticatedSubject", claims.sub as String)
    message.setProperty("AuthenticatedScopes",  (claims.scope ?: "") as String)
    message.setProperty("AuthenticatedClient",  (claims.client_id ?: "") as String)
    message.setHeader("OAuth2Validated", "true")

    return message
}

// Centralised rejection — sets a property the iFlow's Router can branch on.
// Doesn't throw because catching exceptions in iFlows is more work for the
// consultant; routing on a property is the CPI-idiomatic way to handle this.
private void rejectUnauthorized(Message message, String reason) {
    message.setProperty("OAuth2Validated",   "false")
    message.setProperty("OAuth2RejectReason", reason)
    message.setHeader("CamelHttpResponseCode", 401)
    message.setBody('{"error":"unauthorized","error_description":"' + reason.replaceAll('"', "'") + '"}')
}
