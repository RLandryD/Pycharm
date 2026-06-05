import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper
import groovy.xml.MarkupBuilder

def Message processData(Message message) {
    // Retrieve headers from the message
    def headers = message.getHeaders()
    // Extract the JSON string from the header named 'FormattedHTTPQuery'
    def jsonString = headers.get("FormattedHTTPQuery")
    
    // Parse the JSON string into a JSON object
    def jsonSlurper = new JsonSlurper()
    def jsonObject = jsonSlurper.parseText(jsonString)

    // Initialize a writer for building the XML string
    def writer = new StringWriter()
    def xml = new MarkupBuilder(writer)
    xml.setDoubleQuotes(true) // Use double quotes for XML attributes
    
    
            //Define a mapping from jSON field names to XML filed names (for "orderBy conversion")

  def fieldMapping = [
          'billingDocumentId'        : 'BILL_DOCUMENT',
          'contractId'               : 'CONTRACT',
          'divisionCategory'         : 'DIVISION_CATEGORY',
          'amount'                   : 'BILL_AMOUNT',
          'startDate'                : 'BILL_START_DATE',
          'endDate'                  : 'BILL_END_DATE',
          'period'                   : 'BILL_PERIOD',
          'invoiceNumber'            : 'INVOICE',
          'contractAccountId'        : 'CONTRACT_ACCOUNT',
          'isSelectedForReversal'    : 'SELECTED_FOR_REVERSAL',
          'reversalReason'           : 'REVERSAL_REASON',
          'billingDocumentId'        : 'REVERSED'

  ] 

    // Build the XML structure with the root element and namespace
    xml.'n0:IsuC4cV2BillDocSubseqGet'('xmlns:n0': 'urn:sap-com:document:sap:soap:functions:mc-style') {
        // Handle dueDateFrom and dueDateTo if present in the JSON object
        if (jsonObject.dueDateFrom || jsonObject.dueDateTo) {
            ItDueDate {
                'item' {
                    Sign('I') 
                    Option('BT') 
                    DateFrom(jsonObject.dueDateFrom?.get(0)?.value ?: '') 
                    DateTo(jsonObject.dueDateTo?.get(0)?.value ?: '') 
                }
            }
        }

        // Iterate through each key-value pair in the JSON object
// First handle the IsBillParameter block
IsBillParameter {
    jsonObject.each { key, value ->
        switch (key) {
            case 'invoiceNumber':
                Invoice(value[0].value.trim())
                break

            case 'billingDocumentId':
                BillDocuments {
                    value.each { billDocuments ->
                        item(billDocuments.value.trim())
                    }
                }
                break


        }
    }
}

// handle all other fields outside IsBillParameter
jsonObject.each { key, value ->
    switch (key) {

        case 'invoiceNumber':
        case 'billingDocumentId':
            // Already handled inside IsBillParameter
            break

        case 'skip':
            IvSkip(value)
            break

        case 'search':
            IvSearchValue(value)
            break

        case 'top':
            IvTop(value)
            break


        case 'orderBy':
            def mappedValue = fieldMapping.get(value, value)
            IvSortField(mappedValue)
            break

        case 'orderByType':
            IvSortType(value)
            break

        default:
            // Handle any other dynamic fields if necessary
            break
    }
}


        // Always include ItAdditionalFilter with constant values
        ItAdditionalFilter {
            'item' {
                Fieldname('QueryFilter') 
                FieldValue(headers.get("QueryFilter")) 
                Sign('I') 
                Option('') 
                Low("") 
                High("") 
            }
        }
    }

    // Set the XML output as the message body
    message.setBody(writer.toString())

    // Return the modified message
    return message
}
