// run_cpi_script.groovy <script.groovy> [bodyText]
// Loads a CPI Groovy script, injects a stub Message, runs processData, prints result.
import cpi.stub.Message

def scriptFile = args[0]
def bodyText = args.length > 1 ? args[1] : "test-body"

// Read the script, strip the CPI import (we provide our own Message), eval it
def scriptText = new File(scriptFile).text
scriptText = scriptText.replaceAll(/import com\.sap\.gateway\.ip\.core\.customdev\.util\.Message/, "import cpi.stub.Message")

def shell = new GroovyShell(this.class.classLoader)
def script = shell.parse(scriptText)

def msg = new Message()
msg.setBody(bodyText)

try {
    def result = script.processData(msg)
    println "=== SUCCESS ==="
    println "Body: " + (result.getBody(String) ?: "(null)")
    println "Headers: " + result.getHeaders()
    println "Properties: " + result.getProperties()
} catch (Exception e) {
    println "=== RUNTIME ERROR ==="
    println e.class.name + ": " + e.message
}
