/*
 * complex_01_custom_cryptography.groovy
 *
 * Pattern: AES-256-GCM payload encryption/decryption using a key fetched
 * from CPI's Secure Parameter store. GCM is chosen over CBC because it
 * provides authenticated encryption (detects tampering automatically)
 * and is the modern recommendation. The IV is randomly generated per
 * message and prepended to the ciphertext.
 *
 * Use case: A receiver requires payload encryption beyond TLS — common
 * for B2B partners exchanging PII or for cross-tenant data that must
 * survive multiple hops. Or symmetric decryption of inbound payloads
 * from a partner that pre-encrypts before posting.
 *
 * Verified: Static analysis only. The Crypto API (Cipher, SecretKeySpec)
 * is JDK-standard and available in CPI's worker JVM. The CPI-specific
 * piece is SecureStoreService — verify the import path matches your
 * tenant version. Some older tenants use a different package.
 *
 * Pitfall handled (CRITICAL): NEVER reuse an IV with the same key in
 * GCM mode — doing so breaks confidentiality completely. We generate
 * a fresh 12-byte IV per message via SecureRandom.
 *
 * Pitfall handled: Don't store keys in headers or properties. They get
 * logged in MPL attachments. The key is retrieved from SecureStoreService
 * by alias and never assigned to anything observable.
 *
 * Pitfall handled: GCM auth tag must be appended to ciphertext for the
 * receiver to verify integrity. javax.crypto.Cipher in GCM mode handles
 * this automatically when you use doFinal() — the tag is in the last
 * 16 bytes of the output.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.ITApiFactory
import com.sap.it.api.securestore.SecureStoreService
import com.sap.it.api.securestore.UserCredential
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import java.security.SecureRandom
import java.util.Base64

def Message processData(Message message) {

    // Operation: "encrypt" or "decrypt" — driven by property set
    // upstream (e.g. by Content Modifier or a Router branch).
    def operation = message.getProperty("CryptoOperation") ?: "encrypt"

    // Key alias from Secure Parameter store. Stored as "User Credentials"
    // type with the AES key (32 bytes base64-encoded) in the password field.
    def keyAlias = message.getProperty("CryptoKeyAlias") ?: "DEFAULT_AES_KEY"

    SecureStoreService secureStore = ITApiFactory.getService(SecureStoreService, null)
    if (secureStore == null) {
        throw new IllegalStateException("SecureStoreService not available — running outside CPI runtime?")
    }
    UserCredential credential = secureStore.getUserCredential(keyAlias)
    if (credential == null) {
        throw new IllegalStateException("Crypto key alias '${keyAlias}' not found in Secure Parameter store")
    }

    // Key never assigned to message/header/property — keeps it out of logs
    byte[] keyBytes = Base64.decoder.decode(new String(credential.password))
    if (keyBytes.length != 32) {
        throw new IllegalStateException("AES-256 requires 32-byte key; alias '${keyAlias}' has ${keyBytes.length} bytes")
    }
    SecretKeySpec keySpec = new SecretKeySpec(keyBytes, "AES")

    byte[] input = message.getBody(byte[]) ?: new byte[0]
    byte[] output

    if (operation == "encrypt") {
        // Generate fresh 12-byte IV per message — CRITICAL for GCM safety
        byte[] iv = new byte[12]
        new SecureRandom().nextBytes(iv)

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, keySpec, new GCMParameterSpec(128, iv))
        byte[] ciphertext = cipher.doFinal(input)

        // Output layout: [12-byte IV][ciphertext + 16-byte GCM tag]
        // Receiver needs to know this layout to decrypt.
        output = new byte[iv.length + ciphertext.length]
        System.arraycopy(iv,         0, output, 0,           iv.length)
        System.arraycopy(ciphertext, 0, output, iv.length,   ciphertext.length)
        message.setHeader("CryptoIVLength", iv.length as String)

    } else if (operation == "decrypt") {
        // Expect [12-byte IV][ciphertext + 16-byte tag]
        if (input.length < 12 + 16) {
            throw new IllegalArgumentException("Ciphertext too short — needs IV (12) + at least one block + auth tag (16)")
        }
        byte[] iv         = input[0..11]         as byte[]
        byte[] ciphertext = input[12..-1]        as byte[]

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, keySpec, new GCMParameterSpec(128, iv))
        // doFinal throws AEADBadTagException if the ciphertext has been
        // tampered with — let it propagate. NEVER catch and swallow.
        output = cipher.doFinal(ciphertext)

    } else {
        throw new IllegalArgumentException("Unknown CryptoOperation: ${operation}")
    }

    message.setBody(output)
    message.setHeader("CryptoOperation", operation)
    return message
}
