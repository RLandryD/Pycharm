// CPIMessageStub.groovy
// Stub of SAP CPI's com.sap.gateway.ip.core.customdev.util.Message for LOCAL
// execution of Groovy scripts. Lets us actually RUN and verify CPI Groovy
// scripts off-tenant (was previously static-check only).
//
// Covers the common Message API surface. NOT a full CPI runtime — no
// ITApiFactory services (DataStore, SecureStore, MessageLog), no Camel
// exchange. Scripts using those need those stubbed too (see extended stub).

package cpi.stub

class Message {
    private Object body
    private Map<String,Object> headers = [:]
    private Map<String,Object> properties = [:]
    private Map<String,Object> attachments = [:]

    Object getBody() { return body }
    Object getBody(Class type) {
        if (body == null) return null
        if (type == String && !(body instanceof String)) {
            if (body instanceof byte[]) return new String(body, "UTF-8")
            return body.toString()
        }
        if (type == byte[].class && body instanceof String) {
            return ((String)body).getBytes("UTF-8")
        }
        return body
    }
    void setBody(Object b) { this.body = b }

    void setHeader(String name, Object value) { headers[name] = value }
    Object getHeader(String name, Class type) { return headers[name] }
    Map<String,Object> getHeaders() { return headers }
    void setHeaders(Map<String,Object> h) { this.headers = h }

    void setProperty(String name, Object value) { properties[name] = value }
    Object getProperty(String name) { return properties[name] }
    Map<String,Object> getProperties() { return properties }
    void setProperties(Map<String,Object> p) { this.properties = p }

    void addAttachmentAsString(String name, String content, String type) {
        attachments[name] = content
    }
    Map<String,Object> getAttachments() { return attachments }
}
